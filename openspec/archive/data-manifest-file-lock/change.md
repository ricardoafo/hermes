# Change: data-manifest-file-lock

**SDD change**: `data-manifest-file-lock`
**Date**: 2026-08-26
**Status**: Shipped
**Tracking issue**: see sibling `issue.md` (#257)

Mirror of engram topic key `sdd/data-manifest-file-lock/change`.

## Summary

Closes a silent-data-loss race in `data-manifest.json`. Both `scripts/data/ingest_binance_vision.py` and `scripts/data/ingest_defillama_peg.py` write to the same shared manifest via a read-modify-atomic-write pattern. When two scripts run concurrently (klines + agg_trades + peg all in flight — the operator's overnight pattern) the read-modify cycle is unlocked, so the second-to-flush write can overwrite the first script's newly-added entries. Fix: introduce `scripts/data/_manifest_util.py` with a `manifest_transaction(path)` context manager wrapping the read/mutate/write cycle in a cross-platform `filelock.FileLock`. Both ingest scripts refactored to use the transaction.

## Scope

### In

- **NEW**: `scripts/data/_manifest_util.py` (~80 LOC) exposing:
  - `manifest_transaction(path: Path) -> Iterator[dict]` context manager.
  - Locks `<path>.lock` (cross-platform via `filelock.FileLock`) around read + mutate + write.
  - Writes atomically (temp + rename) on `__exit__` regardless of exception (only when the caller reached the yield).
- **MOD**: `scripts/data/ingest_binance_vision.py` — replace the standalone `save_manifest()` calls with `manifest_transaction()`. Every mutation happens inside the transaction; caller no longer touches disk directly.
- **MOD**: `scripts/data/ingest_defillama_peg.py` — same refactor.
- **NEW**: `tests/unit/scripts_data/test_manifest_util.py` — thread + subprocess concurrency test: 2 threads × 100 dict-updates → final manifest has 200 entries (no lost updates). Deterministic with `filelock`'s blocking acquire.
- **MOD**: `pyproject.toml` — promote `filelock` from transitive to direct dep.
- **NEW**: `changelog.d/wave-data-manifest-file-lock.md`.

### Out

- Splitting the manifest by namespace (klines vs agg_trades vs peg). Would eliminate the race entirely, but forces downstream readers to know about the split. Bigger design change; revisit if the lock ever becomes a contention hotspot (currently sub-second latency per checkpoint).
- Refactoring the whole ingest pipeline to a database. Overkill for a JSON manifest.
- Rewriting the atomic-write pattern (temp + rename) — that part is fine; only the read-modify-write cycle needs the lock.

## Decisions

### D-1: `filelock.FileLock` (Python), not a platform-specific primitive

- **Decision**: use `filelock.FileLock` (PyPI, cross-platform).
- **Alternatives**: (a) `fcntl.flock` (Unix-only, no Windows); (b) `msvcrt.locking` (Windows-only); (c) `portalocker`; (d) sentinel `.lock` file with `O_CREAT|O_EXCL` semantics.
- **Rationale**: The operator runs on Windows; CI runs on Linux. `filelock` is already a transitive dep, well-maintained, and abstracts the platform difference. `O_CREAT|O_EXCL` sentinel is simpler but leaks on abnormal exit (needs a manual cleanup pass) — `filelock` handles OS-level lock release automatically.
- **Cost of reversal**: LOW — swap the implementation inside `manifest_transaction`.

### D-2: One shared helper module in `scripts/data/`

- **Decision**: `scripts/data/_manifest_util.py` — leading underscore signals internal, importable from same-dir scripts via `from _manifest_util import ...`.
- **Alternatives**: (a) duplicate the helper in each ingest script; (b) promote to `src/hermes/infrastructure/data_manifest.py` (real package).
- **Rationale**: Duplication risks drift when the transaction semantics evolve. Promoting to `src/hermes/` is over-broad — the helper is data-ingest-tooling-specific, not domain / adapter concern. Same-dir import keeps the helper co-located with its callers. `sys.path[0]` on script invocation includes the script's dir, so imports "just work" when scripts are run directly.
- **Cost of reversal**: LOW — either duplicate down or promote up.

### D-3: Transaction re-reads manifest ON ENTER, writes ON EXIT

- **Decision**: `__enter__` acquires lock + re-reads from disk; `__exit__` writes back + releases.
- **Alternatives**: (a) accept an already-loaded dict, only lock the write; (b) never re-read — trust the passed-in dict.
- **Rationale**: The race being fixed is exactly the read-vs-write ordering. Re-reading under the lock is the fix. Trusting a passed-in dict re-introduces the race.
- **Cost of reversal**: LOW — behavior change would require callers to think again.

### D-4: Manifest write-back on exceptions — write only if the caller reached the yield

- **Decision**: `__exit__` writes the current dict state only when `__enter__` completed successfully (yielded). On exceptions raised inside the `with` block, the dict as-modified is still written (partial-progress preservation matches the existing `save_manifest_every_10` checkpoint semantics).
- **Alternatives**: rollback on exception (drop mutations made inside the block).
- **Rationale**: The existing scripts already write partial progress every 10 files (see `MANIFEST_CHECKPOINT_EVERY`). Rollback would REDUCE resume-safety by discarding successful downloads that happened before an exception.
- **Cost of reversal**: LOW — add a rollback flag if operators want it.

## Requirements

### R-1: Concurrency test passes (no lost updates)

- **A-1.1**: `pytest tests/unit/scripts_data/test_manifest_util.py::test_two_threads_no_lost_updates` — final manifest has exactly 200 keys after 2 threads × 100 unique-key writes.
- **A-1.2**: `pytest tests/unit/scripts_data/test_manifest_util.py::test_two_subprocesses_no_lost_updates` — same guarantee across OS-level processes (not just threads).

### R-2: Existing ingest smoke passes

- **A-2.1**: `python scripts/data/ingest_defillama_peg.py --coin tether --limit 1` — writes one file, updates manifest, exit 0.
- **A-2.2**: `python scripts/data/ingest_binance_vision.py --dry-run` — enumerates targets without side effect.

### R-3: Lock cleanup on abnormal exit

- **A-3.1**: A KeyboardInterrupt raised mid-transaction releases the lock (verified via a follow-up `manifest_transaction()` call in the same process not blocking).

### R-4: `filelock` promoted to direct dep

- **A-4.1**: `pyproject.toml` `[project] dependencies` list contains `filelock>=3`.

## Design

```python
# scripts/data/_manifest_util.py
"""Shared read-modify-atomic-write transaction for data-manifest.json.

Prevents lost updates when two ingest scripts share the same manifest.
See openspec change `data-manifest-file-lock` for the race narrative.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from filelock import FileLock


@contextmanager
def manifest_transaction(path: Path, timeout: float = 60.0) -> Iterator[dict]:
    """Yield the manifest under an exclusive file lock.

    Read on enter; write atomically on exit (even if the block raised).
    Lock file is ``<path>.lock`` — kept next to the manifest for
    discoverability. Timeout defaults to 60s (plenty for a JSON write).
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = FileLock(str(path) + ".lock", timeout=timeout)
    with lock:
        # Re-read under the lock — this is the critical step.
        if path.exists():
            try:
                data = json.loads(path.read_text())
            except json.JSONDecodeError:
                data = {}
        else:
            data = {}
        try:
            yield data
        finally:
            # Atomic write — temp + rename, same pattern the ingest
            # scripts had before.
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
            tmp.replace(path)
```

Caller refactor (Binance ingest excerpt):

```python
# Before:
manifest = {}
if manifest_path.exists():
    manifest = json.loads(manifest_path.read_text())
...
manifest[key] = {...}
save_manifest(manifest_path, manifest)

# After:
from _manifest_util import manifest_transaction

...
with manifest_transaction(manifest_path) as manifest:
    manifest[key] = {...}
```

Batched-write pattern (every N files) keeps working — call `manifest_transaction()` every N files instead of `save_manifest()`.

## Test plan

1. `pytest tests/unit/scripts_data/test_manifest_util.py -v` — both concurrency tests exit 0.
2. `python scripts/data/ingest_defillama_peg.py --coin tether --limit 1` — smoke pass.
3. `python scripts/data/ingest_binance_vision.py --dry-run` — dry-run unchanged.
4. `bash scripts/dev/pr-preflight.sh --render-body data-manifest-file-lock` — all 16 checks green.

## Risks

- **R1** — `filelock` semantics on network filesystems (SMB/NFS) can be flaky. Mitigation: the operator's data dir is a local drive; document the constraint in the ingest script docstrings.
- **R2** — Long-held locks stall other ingest scripts. Mitigation: 60s timeout raises `filelock.Timeout` if a run hangs; each ingest checkpoint takes sub-second under the lock.
- **R3** — Test flakiness: threading tests can flake on slow CI runners. Mitigation: use blocking-acquire semantics (deterministic), not polling. Cap runtime with `pytest-timeout` (already in place).

## Dependencies on other changes

- No hard dependency. Complements #246 (DefiLlama swap) — that change introduced the peg ingest that now shares the manifest with klines/agg_trades.
- Complementary to any future ingest source (Kraken, exchange fills export) that would add a fourth writer.
