# Change: data-ingest-binance-vision

**SDD change**: `data-ingest-binance-vision`
**Date**: 2026-08-25
**Status**: Shipped
**Tracking issue**: see sibling `issue.md` (#218)

Mirror of engram topic key `sdd/data-ingest-binance-vision/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

Materialise the plan documented in `docs/experiments/swing-mvp1/data-ingest-plan.md` §1 into a runnable Python script `scripts/data/ingest_binance_vision.py`. Downloads all monthly klines ZIPs for BTC/ETH/SOL × 1h/4h/1d across the pre-reg v1 §4 uniform window (2020-09-01 → 2026-12-31), SHA256-verifies each file against Binance's `.CHECKSUM` companions, and writes a resume-safe `data-manifest.json` per pre-reg §16 requirement. Main architectural pick: **stdlib only** (`urllib` + `hashlib` + `json`) — no new dependency. Main risk: Binance Vision rate limits + partial-download interruption; mitigated by per-20-file manifest checkpointing.

## Scope

### In

- `scripts/data/ingest_binance_vision.py` — stdlib Python script that enumerates all `(pair, interval, month)` targets, downloads + verifies each ZIP, writes to `$HERMES_DATA_DIR/klines/<pair>/<interval>/<yyyy>/<mm>/`, and maintains `data-manifest.json`.
- CLI flags: `--data-dir <path>` (override `$HERMES_DATA_DIR`), `--manifest <path>`, `--pair <p>` (repeatable), `--interval <i>` (repeatable), `--dry-run` (list targets without downloading).
- Resume behaviour: file exists AND manifest entry present → skip; either missing → re-download + re-verify.
- Manifest checkpoint every 20 files (interrupt-tolerant).
- `changelog.d/wave-data-ingest-binance-vision.md` fragment.

### Out (defer)

- Parquet conversion — Spec 4c (`MarketDataPort` historical adapter).
- Live L2 orderbook collector — separate script that runs on VPS `hermes-01` for the concurrent OD-1 Option B shadow-prep path.
- Kraken data — MVP2.
- CoinGecko peg history (for SA-9 depeg overlay) — separate ingest for a separate data class.
- Any Python-package dependency beyond stdlib.

## Decisions

### D-1: stdlib-only implementation (no requests, no httpx)

- **Decision**: use `urllib.request.urlopen` for HTTP and `hashlib.sha256` for verify. No third-party deps.
- **Alternatives**: (a) `requests` (nicer API, extra dep); (b) `httpx` (async-capable, extra dep + async complexity for no benefit at this scale).
- **Rationale**: 549 files × 1 HTTP call each is not IO-bound enough to justify async, and adding a dep for a one-off data-ingest script bloats `pyproject.toml`. stdlib is portable and Python 3.14-native.
- **Cost of reversal**: LOW — swap `urlopen` for `requests.get` if a downstream reason emerges.

### D-2: SHA256 verify BEFORE writing to disk

- **Decision**: download the ZIP into memory, verify SHA256 against the `.CHECKSUM` file, only then write to disk.
- **Alternatives**: (a) stream to disk + verify after (better for very large files, but our max ZIP is <20 MB); (b) skip verify (fragile, reject).
- **Rationale**: pre-reg v1 §16 (Dataset fingerprints) MANDATES a SHA256 per file. Verifying before write means a corrupted file never lands. The 20 MB memory footprint per download is negligible.
- **Cost of reversal**: LOW.

### D-3: Manifest checkpointing every 20 files

- **Decision**: write `data-manifest.json` to disk every 20 successful downloads (and once at the end).
- **Alternatives**: (a) checkpoint every file (I/O overhead); (b) checkpoint only at end (interrupt loses everything).
- **Rationale**: 20 is the sweet spot — an interrupt loses at most ~20 files worth of manifest entries (~2 MB) but the files themselves are on disk and are re-verifiable on the next run's resume pass.
- **Cost of reversal**: LOW — tune the constant.

### D-4: Uniform window 2020-09-01 for all pairs

- **Decision**: use the pre-reg v1 §4 uniform-start window across BTC/ETH/SOL, even though BTC + ETH have earlier data available on Binance Vision.
- **Alternatives**: per-pair start dates.
- **Rationale**: pre-reg §4 is BINDING. Per-pair windows are a §11 deviation.
- **Cost of reversal**: HIGH — reversing means invalidating the pre-reg (v2 needed).

## Requirements

### R-1: Script runs end-to-end producing correct file layout

- **A-1.1**: `python scripts/data/ingest_binance_vision.py --dry-run` prints ≥ 500 target files with paths matching `<pair>/<interval>/<yyyy>-<mm>` format.
- **A-1.2**: After full run, `find $HERMES_DATA_DIR/klines -name '*.zip' | wc -l` returns ≥ 549 (allowing for latest months not yet published).
- **A-1.3**: `data-manifest.json` contains a `sha256` field for every downloaded file.

### R-2: SHA256 verification catches corruption

- **A-2.1**: Manually corrupting a downloaded ZIP + rerunning re-verifies and re-downloads it (behaviour tested via unit test in follow-up).

### R-3: Resume-safe

- **A-3.1**: After a full run, a second `python scripts/data/ingest_binance_vision.py` completes in < 5 seconds and reports every file as "skipped (already in manifest)".

### R-4: Pre-reg §4 window respected

- **A-4.1**: Manual check — first downloaded month is 2020-09; last is `2026-12` (or the latest month available at run time).

## Design

Structure:

```python
PAIRS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
INTERVALS = ["1h", "4h", "1d"]
BASE_URL = "https://data.binance.vision/data/spot/monthly/klines"
START = (2020, 9)  # pre-reg v1 §4 uniform start
END = (2026, 12)  # pre-reg v1 §4 OOS end


def iter_months(): ...
def download(url, timeout=60) -> bytes: ...
def sha256(data: bytes) -> str: ...
def process_file(pair, interval, y, m, root, manifest) -> tuple[bool, str]: ...
def main() -> int: ...
```

CLI:

```text
python scripts/data/ingest_binance_vision.py [OPTIONS]
  --data-dir PATH        Override $HERMES_DATA_DIR (default D:/hermes-data)
  --manifest PATH        Override manifest location
  --pair PAIR            Restrict to a single pair (repeatable)
  --interval INTERVAL    Restrict to a single interval (repeatable)
  --dry-run              List targets without downloading
```

Manifest entry shape:

```json
{
  "BTCUSDT/1h/2020-09": {
    "sha256": "abc123...",
    "url": "https://data.binance.vision/.../BTCUSDT-1h-2020-09.zip",
    "size_bytes": 12345678,
    "downloaded_at": "2026-08-25"
  }
}
```

## Test plan

1. `python scripts/data/ingest_binance_vision.py --dry-run` — prints target list, exits 0.
2. `python scripts/data/ingest_binance_vision.py --pair BTCUSDT --interval 1d` — small subset (~61 files) completes in ~10 min, manifest updated.
3. Interrupt mid-run (Ctrl-C after ~30 files), re-run — resumes from where it left off, no re-verify overhead on skipped files.
4. Manually rm one downloaded file, re-run — script detects the gap, re-downloads + re-verifies that file only.
5. `bash scripts/dev/pr-preflight.sh` all 14 checks green on this branch.

## Risks

- **R1** — Binance Vision rate limits. Mitigation: script is single-threaded (no burst); if a 429 arrives, retry with backoff is a follow-up (not exercised in observed usage).
- **R2** — Latest months may not yet be published. Mitigation: 404 on `.CHECKSUM` fetch is caught and logged as "not available yet"; script continues.
- **R3** — Network interruption mid-download of a single ZIP. Mitigation: default 60s per-request timeout; failed file rolls back cleanly (no partial write to disk); resume pass picks it up.
- **R4** — Disk fills up. Mitigation: pre-reg v1 §4 estimates ~200 GB decoded Parquet; ZIPs total ~50 GB. Operator confirmed D: has 400 GB free. Script prints a "check disk space" warning if <100 GB free at start.

## Dependencies

- Pre-reg v1 §4 (frozen windows, uniform start) — BINDING.
- `docs/experiments/swing-mvp1/data-ingest-plan.md` §1 — the URL patterns and download-script outline this change materialises.
- No external Python packages.

## Tasks

- [ ] 1. Create `scripts/data/ingest_binance_vision.py`.
- [ ] 2. Verify `--dry-run` locally lists the expected target set.
- [ ] 3. Add `changelog.d/wave-data-ingest-binance-vision.md`.
- [ ] 4. `bash scripts/dev/pr-preflight.sh` green.
- [ ] 5. Open PR via `bash scripts/dev/create-pr.sh --render-body data-ingest-binance-vision`.
