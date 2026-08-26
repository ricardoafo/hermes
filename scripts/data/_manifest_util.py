"""Shared read-modify-atomic-write transaction for ``data-manifest.json``.

Prevents lost updates when two ingest scripts share the same manifest.
Prior to this module, ``ingest_binance_vision.py`` and
``ingest_defillama_peg.py`` each read the manifest, mutated a dict, and
wrote it back atomically — but the read-mutate-write cycle itself was
not serialised. Running klines + agg_trades + peg ingests concurrently
could silently drop the just-added entries of whichever writer flushed
first.

See openspec change ``data-manifest-file-lock`` (#257) for the full
narrative and design decisions.

Usage:

.. code-block:: python

    from _manifest_util import manifest_transaction

    with manifest_transaction(manifest_path) as manifest:
        manifest[key] = payload

The context manager acquires an exclusive lock on
``<manifest_path>.lock`` (cross-platform via :mod:`filelock`), re-reads
the manifest from disk under the lock, yields the dict, and writes the
mutated dict back atomically (temp file + rename) on exit. The lock is
released on normal exit and on exceptions.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from filelock import FileLock

__all__ = ["manifest_read", "manifest_transaction"]


def manifest_read(path: Path) -> dict:
    """Return a snapshot of the manifest, read under an exclusive lock.

    Convenience wrapper for the common "load once at startup for resume
    checks" pattern. Uses :func:`manifest_transaction` under the hood
    but yields a plain dict (not a context manager) so callers don't
    have to indent their startup code.
    """
    with manifest_transaction(path) as data:
        return dict(data)


@contextmanager
def manifest_transaction(path: Path, timeout: float = 60.0) -> Iterator[dict]:
    """Yield the manifest dict under an exclusive file lock.

    The manifest is re-read from disk when the transaction enters, so
    the caller always sees the latest committed state — including any
    entries written by a sibling ingest script that flushed while this
    process was doing work.

    On exit the (possibly-mutated) dict is written back atomically via
    temp-file + rename. The write happens whether the ``with`` block
    exited normally or via exception; the existing checkpoint-every-N
    semantics of the ingest scripts already relies on partial-progress
    preservation, and rollback would reduce resume-safety.

    Args:
        path: Path to the manifest JSON file. Its parent directory is
            created on demand.
        timeout: Seconds to wait for the lock before raising
            :class:`filelock.Timeout`. Defaults to 60s — plenty for a
            JSON write, tight enough that a stuck writer surfaces
            visibly instead of stalling indefinitely.

    Yields:
        The manifest dict. Mutate in place; changes are persisted on
        exit.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = FileLock(str(path) + ".lock", timeout=timeout)
    with lock:
        if path.exists():
            try:
                data = json.loads(path.read_text())
            except json.JSONDecodeError:
                # Corrupt file — treat as empty rather than crash the
                # caller. The next successful write will replace it.
                data = {}
        else:
            data = {}
        try:
            yield data
        finally:
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
            tmp.replace(path)
