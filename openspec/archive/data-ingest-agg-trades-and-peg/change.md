# Change: data-ingest-agg-trades-and-peg

**SDD change**: `data-ingest-agg-trades-and-peg`
**Date**: 2026-08-25
**Status**: Shipped
**Tracking issue**: see sibling `issue.md` (#221)

Mirror of engram topic key `sdd/data-ingest-agg-trades-and-peg/change`.

## Summary

Extends the Binance Vision ingest (#218 / PR #219) to cover the remaining MVP1 data classes: (1) **aggTrades** — Binance aggregated trades, needed for intrabar slippage calibration and fill-timing (Spec 2b + Spec 6); (2) **CoinGecko peg history** — hourly USDT/USDC/DAI vs USD, needed for the SA-9 depeg overlay wired by Spec 8. Also fixes a bug in the existing klines script where 404 responses for future months (not yet published on Binance Vision) were mis-classified as `failed`, causing the script to exit 1 even when everything actually worked.

## Scope

### In

- **`scripts/data/ingest_binance_vision.py` — `--include` flag**: values `klines` (default, current behaviour), `agg_trades`, or `all`. When `agg_trades` selected, the URL path segment becomes `aggTrades/<pair>/<pair>-aggTrades-<yyyy>-<mm>.zip` (no interval). Same SHA256 verify, same manifest structure (namespaced by dataset), same resume logic.
- **404 classification fix**: split the current `failed` counter into `failed` (unexpected — network, corruption, unknown 5xx) and `not_yet_published` (expected — 404 on `.CHECKSUM` fetch for future months). Exit 0 if no unexpected failures, regardless of `not_yet_published` count.
- **`scripts/data/ingest_coingecko_peg.py` NEW**: stdlib-only, paginates the CoinGecko `coins/{id}/market_chart/range` endpoint in 90-day windows for `tether`, `usd-coin`, `dai`. Writes hourly rows into `<data-dir>/peg/<coin-id>/<yyyy>-<mm>.json` and records SHA256 in `data-manifest.json` under `peg/` namespace.
- **Small docs update**: `docs/experiments/swing-mvp1/data-ingest-plan.md` gets a two-paragraph "Runbook" section pointing at the actual scripts.
- **Changelog fragment**: `changelog.d/wave-data-ingest-agg-trades-and-peg.md`.

### Out (defer)

- Raw trades (redundant with aggTrades for our use).
- bookTicker (top-of-book only — defer to Spec 6 if calibration needs it).
- L2 diffs (doesn't exist historically; captured live during shadow phase).
- VIX / MOVE / S&P macro data (MVP2 / SA-9 §8.6 enhancement).
- Live real-time collectors (separate VPS-side script).

## Decisions

### D-1: Extend the existing script instead of new `ingest_binance_agg_trades.py`

- **Decision**: add `--include` flag to `ingest_binance_vision.py`.
- **Alternatives**: (a) new `ingest_binance_agg_trades.py` (near-total code duplication); (b) subcommands (`ingest_binance_vision.py klines` / `... agg_trades`) — slightly cleaner CLI but re-plumbing argparse.
- **Rationale**: URL + checksum + manifest logic is identical; the ONLY difference is one URL-path segment and the absence of interval. Extension is ~30 LOC, duplication would be 250 LOC.
- **Cost of reversal**: LOW.

### D-2: CoinGecko as separate script

- **Decision**: new file `ingest_coingecko_peg.py`. Different vendor, different API (pagination, JSON not ZIP, no per-file SHA256 upstream). Merging would fork the download loop unnecessarily.
- **Alternatives**: single mega-script with `--source binance-vision|coingecko`.
- **Rationale**: separation of concerns; each script is <200 LOC; easier to swap CoinGecko for Kaiko / Nomics later without touching Binance path.
- **Cost of reversal**: LOW.

### D-3: 404 for future months = `not_yet_published`, not `failed`

- **Decision**: split counters. Only unexpected errors (network timeouts, 500-class HTTP, SHA mismatch, unknown exceptions) count as `failed` and drive the exit code.
- **Alternatives**: (a) filter target list to current-month-1 up front (loses future-facing operator-runs-later semantics); (b) keep current behaviour (broken, mis-classifies expected outcome).
- **Rationale**: A pipeline that reports "failed" when everything worked as expected trains operators to ignore its exit code.
- **Cost of reversal**: LOW.

### D-4: Namespace manifest entries by dataset kind

- **Decision**: manifest key format changes to `<dataset>/<pair>/<interval|->/<yyyy>-<mm>`. For klines: `klines/BTCUSDT/1h/2020-09`. For aggTrades: `agg_trades/BTCUSDT/-/2020-09` (no interval). For peg: `peg/tether/-/2020-09`.
- **Alternatives**: separate manifest files per dataset.
- **Rationale**: single-manifest is simpler to reason about + easier for pre-reg §16 fingerprint aggregation. Backward-compat: on first run, old-format entries (`BTCUSDT/1h/2020-09`) are migrated in-place to `klines/BTCUSDT/1h/2020-09`.
- **Cost of reversal**: LOW.

## Requirements

### R-1: aggTrades download works end-to-end

- **A-1.1**: `python scripts/data/ingest_binance_vision.py --include agg_trades --dry-run` prints target list with `aggTrades/<pair>/<yyyy>-<mm>` format (no interval).
- **A-1.2**: A small live run (`--include agg_trades --pair BTCUSDT` filtered to a subset) writes ZIPs under `<data-dir>/agg_trades/BTCUSDT/<yyyy>/<mm>/` and updates the manifest.

### R-2: 404 classification distinguishes expected from unexpected

- **A-2.1**: On a run with only future-month 404s, exit code = 0.
- **A-2.2**: The final summary line prints `X downloaded, Y skipped, Z not-yet-published, W failed`.
- **A-2.3**: Only `W > 0` (real failures) drives exit code 1.

### R-3: CoinGecko peg script downloads USDT/USDC/DAI

- **A-3.1**: `python scripts/data/ingest_coingecko_peg.py --dry-run` prints the 90-day windows for each coin.
- **A-3.2**: A live run writes JSON files under `<data-dir>/peg/<coin-id>/<yyyy>-<mm>.json` and adds `peg/<coin-id>/-/<yyyy>-<mm>` entries to the manifest.

### R-4: Resume-safe across dataset types

- **A-4.1**: Rerunning any script with any `--include` value skips previously-downloaded files (matching manifest entry by namespaced key).

## Design

Manifest schema (namespaced):

```json
{
  "klines/BTCUSDT/1h/2020-09": {"sha256": "abc...", "size_bytes": 42467, "url": "..."},
  "agg_trades/BTCUSDT/-/2020-09": {"sha256": "def...", "size_bytes": 12345678, "url": "..."},
  "peg/tether/-/2020-09": {"sha256": "ghi...", "size_bytes": 8912, "url": "..."}
}
```

CoinGecko URL pattern (per `docs/experiments/swing-mvp1/data-ingest-plan.md` §3):

```text
https://api.coingecko.com/api/v3/coins/{id}/market_chart/range
    ?vs_currency=usd
    &from={unix_start}
    &to={unix_end}
```

Coin IDs: `tether`, `usd-coin`, `dai`. 90-day windows to stay in the hourly-granularity band for the free tier. Rate limit: ~10-30 calls/min free tier; script sleeps 3s between requests to stay safely inside.

## Test plan

1. `python scripts/data/ingest_binance_vision.py --dry-run` — klines default behaviour unchanged (backward compat).
2. `python scripts/data/ingest_binance_vision.py --include agg_trades --dry-run` — enumerates aggTrades targets with no-interval format.
3. `python scripts/data/ingest_coingecko_peg.py --dry-run` — enumerates 90-day windows.
4. Small live subset run — verifies SHA + manifest for one aggTrades file + one peg window.
5. `bash scripts/dev/pr-preflight.sh` all 14 checks green.

## Risks

- **R1** — CoinGecko free-tier rate limit (10-30/min). Mitigation: 3s sleep between requests keeps us at ≤20/min. Backoff-on-429 is a follow-up.
- **R2** — Manifest migration on first-run of updated script may partially rewrite old entries. Mitigation: read → detect old-format keys → rewrite atomically (temp file + rename). Fresh runs on empty manifest are a no-op.
- **R3** — aggTrades ZIPs are LARGE (10+ MB per BTC month). ~76 months × 3 pairs = ~230 files × avg 15 MB = ~3.5 GB total download. Mitigation: same disk-space warning as klines script; operator's D: has 400 GB free.
- **R4** — CoinGecko API schema drift. Mitigation: script validates the response has expected keys (`prices`, `market_caps`, `total_volumes`); logs + skips on schema mismatch.

## Dependencies

- Predecessor: #218 (PR #219) — this change extends that script.
- Pre-reg v1 §5.5 (correlation lock + depeg rules Spec 8 will wire).
- `docs/experiments/swing-mvp1/data-ingest-plan.md` §3 (CoinGecko URL pattern documented).

## Tasks

- [ ] 1. Extend `ingest_binance_vision.py` with `--include` flag + generalized `process_file`.
- [ ] 2. Fix 404 classification (split `failed` / `not_yet_published`).
- [ ] 3. Add manifest key namespacing + backward-compat migration.
- [ ] 4. Write `ingest_coingecko_peg.py`.
- [ ] 5. Add "Runbook" section to `data-ingest-plan.md`.
- [ ] 6. Add `changelog.d/wave-data-ingest-agg-trades-and-peg.md`.
- [ ] 7. `bash scripts/dev/pr-preflight.sh` green.
- [ ] 8. Open PR via `bash scripts/dev/create-pr.sh --render-body data-ingest-agg-trades-and-peg`.
