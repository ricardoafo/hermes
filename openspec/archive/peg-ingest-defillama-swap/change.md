# Change: peg-ingest-defillama-swap

**SDD change**: `peg-ingest-defillama-swap`
**Date**: 2026-08-26
**Status**: Shipped
**Tracking issue**: see sibling `issue.md` (#245)

Mirror of engram topic key `sdd/peg-ingest-defillama-swap/change`.

## Summary

Reverses decision D-2 of `data-ingest-agg-trades-and-peg`: swap CoinGecko for DefiLlama as the stablecoin peg source. CoinGecko's free tier changed since `data-ingest-plan.md` was written — a live run on 2026-08-26 produced 0 successful downloads (5x HTTP 401 for pre-2018 data, 216x HTTP 429 for everything else). DefiLlama's `coins.llama.fi/chart` endpoint has no auth requirement, generous rate limits, and covers all three target coins back to 2020. Drop-in replacement — identical manifest key layout so downstream SA-9 depeg overlay code (Spec 8) sees no shape change.

## Scope

### In

- **New**: `scripts/data/ingest_defillama_peg.py` — stdlib-only, iterates `(tether, usd-coin, dai)` × `(2020-09 .. 2026-12)` monthly windows, calls `https://coins.llama.fi/chart/coingecko:<id>?start=<unix>&span=744&period=1h`, writes one JSON per (coin, month) at `<data-dir>/peg/<coin>/<yyyy>-<mm>.json`, records SHA256 under manifest key `peg/<coin>/-/<yyyy>-<mm>` (unchanged from D-4).
- **Delete**: `scripts/data/ingest_coingecko_peg.py` (fully replaced, does not work).
- **Docs update**: `docs/experiments/swing-mvp1/data-ingest-plan.md` §3 CoinGecko block replaced with DefiLlama block; runbook section pointer updated.
- **Belt-and-suspenders**: `.claude/` in `.gitignore` + `.markdownlint-cli2.jsonc` ignores (also present on #244 branch — folded here so this branch is self-contained).
- **Changelog fragment**: `changelog.d/wave-peg-ingest-defillama-swap.md`.

### Out (defer)

- Kaiko / Nomics / Chainlink oracle as tertiary fallback — DefiLlama covers MVP1 needs; alternatives revisited only if DefiLlama also degrades.
- Backfill of the hourly-bucket resampler in the SA-9 consumer — that lives in Spec 8's application code, out of scope here.
- Migration of any existing partial CoinGecko downloads — the 2026-08-26 live run produced zero files on disk, nothing to migrate.

## Decisions

### D-1: DefiLlama over Kaiko / Nomics / on-chain oracle

- **Decision**: Use DefiLlama's free public `coins.llama.fi/chart` endpoint.
- **Alternatives**:
  - (a) CoinGecko demo API key — free tier changed, requires signup + per-request key, still capped at 30/min. Rejected: creates a per-operator secret dependency, still slow.
  - (b) Kaiko — enterprise-grade, paid, wrong fit for a hobby-scale ingest.
  - (c) Chainlink stablecoin oracle on-chain — accurate but requires per-block RPC calls to Ethereum + Arbitrum + Optimism; ~5000 RPC calls per month per coin.
- **Rationale**: DefiLlama is free, no auth, aggregates from CoinGecko + on-chain sources internally, has historical coverage of USDT (2015+), USDC (2018+), DAI (2019+). Ample data density for the SA-9 depeg check (WARN 1.5% / HALT 3% thresholds don't require sub-minute precision).
- **Cost of reversal**: LOW — the JSON payload format lives on disk; the SA-9 consumer will read whatever shape is present.

### D-2: Retire (not deprecate-with-warning) `ingest_coingecko_peg.py`

- **Decision**: `git rm` the file.
- **Alternatives**: keep + add deprecation banner; keep + wrap as thin forwarder to the new script.
- **Rationale**: The script has never successfully downloaded a file since the CoinGecko API change. Zero downstream code depends on it. Leaving a broken script + banner is worse than removing it (operators hit it, then have to read the banner). Git history preserves it.
- **Cost of reversal**: LOW.

### D-3: Manifest key layout unchanged (`peg/<coin>/-/<yyyy>-<mm>`)

- **Decision**: Same key format as `data-ingest-agg-trades-and-peg` D-4.
- **Alternatives**: bump the key namespace (e.g. `peg-defillama/<coin>/-/<yyyy>-<mm>`) to signal provenance.
- **Rationale**: The SA-9 consumer only cares that the file has a plausible USDT/USDC/DAI price series. Provenance lives inside the JSON blob (DefiLlama includes `symbol` and `confidence` fields per data point). Bumping the namespace forces a downstream reader change for zero user-visible benefit.
- **Cost of reversal**: LOW.

### D-4: One HTTP call per (coin, month), not multi-coin per month

- **Decision**: 3 coins × 76 months = 228 sequential calls, ≥1s sleep between them.
- **Alternatives**: multi-coin per call (76 total) — DefiLlama supports comma-separated coin identifiers.
- **Rationale**: Matches the manifest key structure 1:1 — one HTTP call maps to one on-disk file maps to one manifest entry. Recovering from a mid-run failure is trivial: rerun, the manifest resume-check skips completed windows. Multi-coin per call would require splitting the response into 3 files inside the script, adding non-trivial edge cases (partial coin failure, per-coin retry).
- **Cost of reversal**: MEDIUM — switching to multi-coin later requires rewriting the download loop but does not touch the manifest structure or on-disk layout.

## Requirements

### R-1: DefiLlama script downloads all three coins end-to-end

- **A-1.1**: `python scripts/data/ingest_defillama_peg.py --dry-run` prints the (coin, month) target list (228 items).
- **A-1.2**: A live run on a subset (`--coin tether --dry-run` and then a real single-window run) writes a valid JSON file under `<data-dir>/peg/tether/YYYY-MM.json` and records `peg/tether/-/YYYY-MM` in the manifest.
- **A-1.3**: Full run exit code = 0 when all 228 windows succeed (or complete as `skipped` from the manifest).

### R-2: CoinGecko script is deleted

- **A-2.1**: `test ! -f scripts/data/ingest_coingecko_peg.py` returns 0.
- **A-2.2**: `rg -q coingecko scripts/data/` returns non-zero (or matches only historical comments).

### R-3: Manifest structure unchanged from D-4

- **A-3.1**: `jq -r 'keys[] | select(startswith("peg/"))' data-manifest.json` after a successful run produces keys of the shape `peg/<coin>/-/<yyyy>-<mm>` (unchanged from `data-ingest-agg-trades-and-peg`).

### R-4: `.claude/` is ignored by git AND markdownlint (belt-and-suspenders)

- **A-4.1**: `grep -q '^\.claude/' .gitignore` returns 0.
- **A-4.2**: `grep -q '\.claude/\*\*' .markdownlint-cli2.jsonc` returns 0.

## Design

New script skeleton (fills in the manifest namespace `peg/<coin>/-/<yyyy>-<mm>`):

```python
BASE_URL = "https://coins.llama.fi/chart"
COINS: tuple[str, ...] = ("tether", "usd-coin", "dai")
# pre-reg v1 §4 uniform window (BINDING; matches ingest_binance_vision.py)
START_YEAR, START_MONTH = 2020, 9
END_YEAR, END_MONTH = 2026, 12
RATE_LIMIT_SLEEP_SECONDS = 1  # DefiLlama tolerates ~5-10 rps; we stay well under.

def process_window(coin, year, month, root, manifest):
    key = f"peg/{coin}/-/{year:04d}-{month:02d}"
    dest = root / "peg" / coin / f"{year:04d}-{month:02d}.json"
    if key in manifest and dest.exists():
        return "skipped", "already in manifest"
    from_ts, _ = month_bounds(year, month)
    if from_ts > int(datetime.now(UTC).timestamp()):
        return "not_yet", "window starts in the future"
    url = f"{BASE_URL}/coingecko:{coin}?start={from_ts}&span=744&period=1h&searchWidth=600"
    raw = download_json(url)
    parsed = json.loads(raw)
    prices = parsed.get("coins", {}).get(f"coingecko:{coin}", {}).get("prices", [])
    if not prices and datetime.fromtimestamp(from_ts, UTC).month > int(datetime.now(UTC).timestamp()):
        return "not_yet", "empty response for near-future window"
    if not prices:
        return "failed", f"unexpected empty prices for {coin} in {year}-{month:02d}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(raw)
    manifest[key] = {"sha256": sha256_hex(raw), "url": url, ...}
    return "ok", f"OK ({len(prices)} points, {len(raw):,} bytes)"
```

Response shape (from a live probe on 2026-08-26):

```json
{
  "coins": {
    "coingecko:tether": {
      "symbol": "USDT",
      "confidence": 0.99,
      "prices": [
        {"timestamp": 1601521466, "price": 0.9992985343782648},
        {"timestamp": 1601571862, "price": 0.9942266645379838}
      ]
    }
  }
}
```

## Test plan

1. `python scripts/data/ingest_defillama_peg.py --dry-run` — enumerates 228 windows.
2. Single-window live run: `python scripts/data/ingest_defillama_peg.py --coin tether --limit 1` — verifies SHA + manifest write for one file.
3. `test ! -f scripts/data/ingest_coingecko_peg.py` — CoinGecko script removed.
4. `bash scripts/dev/pr-preflight.sh` — all 14 checks green.

## Risks

- **R1** — DefiLlama coverage gaps for early 2020 stablecoin months. Mitigation: script classifies `empty prices` for windows before the coin's on-chain launch as `not_yet` (matches the Binance klines script's convention for future windows). Operator sees the outcome in the summary line.
- **R2** — DefiLlama API schema drift. Mitigation: script validates `coins.<key>.prices` shape and logs the actual response keys on mismatch; single-point-of-parse in `process_window`.
- **R3** — Same-month rerun after DefiLlama backfills the tail — the manifest check-and-skip logic will skip the window even if new data is available. Mitigation: `--force` flag (deferred to follow-up if operators complain).
- **R4** — Chart endpoint returns fewer than 744 hourly points per month (DefiLlama returns actual data points, not evenly-spaced hourly buckets). Mitigation: the SA-9 depeg consumer resamples to hourly buckets downstream; the ingest just captures whatever DefiLlama has.

## Dependencies on other changes

- Predecessor: `data-ingest-agg-trades-and-peg` (#221, merged via PR #223) — this change reverses its D-2.
- Overlaps with #244 (`.claude/` ignore config). Folded here so this branch is self-contained; conflict at merge time is trivial (both add the same ignore lines).
