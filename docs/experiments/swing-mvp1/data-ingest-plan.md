---
name: Data Ingest Plan — Swing MVP1
experiment_id: swing-mvp1-v1
author: Claude (research agent)
date: 2026-08-25
status: DRAFT — pending operator review
---

# Data Ingest Plan — Swing MVP1

This document specifies exactly where to get the data, how to store it, how to validate it, and how to collect live L2 data for the shadow phase. It covers MVP1 (Binance) and includes MVP2 prep notes (Kraken) where called out.

---

## 1. Binance Bulk Download (OHLCV + aggTrades)

### 1.1 URL Patterns — Verified

All URLs below were verified to return valid ZIP files as of 2026-08-25.

**Base URL:** `https://data.binance.vision/data/spot/monthly/`

#### Klines — 1h

```text
https://data.binance.vision/data/spot/monthly/klines/{PAIR}/{interval}/{PAIR}-{interval}-{YYYY}-{MM}.zip
```

Examples (all confirmed 200 OK):

```text
https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/1h/BTCUSDT-1h-2020-01.zip
https://data.binance.vision/data/spot/monthly/klines/ETHUSDT/1h/ETHUSDT-1h-2020-01.zip
https://data.binance.vision/data/spot/monthly/klines/SOLUSDT/1h/SOLUSDT-1h-2020-09.zip
```

#### Klines — 4h

```text
https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/4h/BTCUSDT-4h-2020-01.zip
https://data.binance.vision/data/spot/monthly/klines/ETHUSDT/4h/ETHUSDT-4h-2020-01.zip
https://data.binance.vision/data/spot/monthly/klines/SOLUSDT/4h/SOLUSDT-4h-2020-09.zip
```

#### Klines — 1d

```text
https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/1d/BTCUSDT-1d-2020-01.zip
https://data.binance.vision/data/spot/monthly/klines/ETHUSDT/1d/ETHUSDT-1d-2020-01.zip
https://data.binance.vision/data/spot/monthly/klines/SOLUSDT/1d/SOLUSDT-1d-2020-09.zip
```

#### aggTrades

```text
https://data.binance.vision/data/spot/monthly/aggTrades/{PAIR}/{PAIR}-aggTrades-{YYYY}-{MM}.zip
```

Examples:

```text
https://data.binance.vision/data/spot/monthly/aggTrades/BTCUSDT/BTCUSDT-aggTrades-2020-01.zip
https://data.binance.vision/data/spot/monthly/aggTrades/ETHUSDT/ETHUSDT-aggTrades-2020-01.zip
https://data.binance.vision/data/spot/monthly/aggTrades/SOLUSDT/SOLUSDT-aggTrades-2020-09.zip
```

Note: aggTrades files are large (confirmed >10 MB per month for BTCUSDT). Plan storage and bandwidth accordingly.

#### SHA256 Checksums (provided by Binance)

Every ZIP file has a companion `.CHECKSUM` file at the same URL with `.CHECKSUM` appended:

```text
https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/1h/BTCUSDT-1h-2020-01.zip.CHECKSUM
```

Confirmed format: a single line containing the SHA256 hex digest followed by two spaces and the filename (standard `sha256sum` output format):

```text
<sha256hex>  BTCUSDT-1h-2020-01.zip
```

Always download and verify the `.CHECKSUM` before ingesting. Do not ingest a file whose computed SHA256 does not match.

### 1.2 Uniform Start Date — 2020-09-01

SOLUSDT listed on Binance in late August/September 2020. Confirmed:

- `SOLUSDT-1h-2020-01.zip` → **404** (does not exist)
- `SOLUSDT-1h-2020-09.zip` → **200 OK** (earliest available)

Per pre-reg v1 §4, the Train window starts **2020-09-01 for all three pairs (BTC, ETH, SOL)** so that all pairs share identical windows. BTC and ETH have earlier data available on Binance Vision but it is NOT downloaded and NOT ingested — the OOS-wall enforcer refuses reads outside the pre-reg windows regardless. Accepted trade-off: 2020 Q1 COVID crash is excluded from Train.

### 1.3 Download Script Outline

```python
import hashlib, urllib.request, pathlib, json, zipfile

PAIRS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
INTERVALS = ["1h", "4h", "1d"]
SOL_START = {"SOLUSDT": (2020, 9)}  # first available month
DEFAULT_START = (2020, 1)
BASE = "https://data.binance.vision/data/spot/monthly"


def iter_months(start_ym, end_ym):
    y, m = start_ym
    ey, em = end_ym
    while (y, m) <= (ey, em):
        yield y, m
        m += 1
        if m > 12:
            m = 1
            y += 1


def verify_and_save(url, dest_path, checksum_url):
    with urllib.request.urlopen(checksum_url) as r:
        expected = r.read().decode().split()[0]
    data = urllib.request.urlopen(url).read()
    actual = hashlib.sha256(data).hexdigest()
    assert actual == expected, f"SHA256 mismatch: {url}"
    dest_path.write_bytes(data)
    return actual


# Returns {filename: sha256hex} entries for data-manifest.json
```

---

## 2. Kraken Historical OHLC — MVP2 Prep (NOT needed for MVP1)

> MVP1 is Binance-only (OD-6). This section is informational for MVP2 planning.

### REST Endpoint (confirmed live)

```text
GET https://api.kraken.com/0/public/OHLC?pair={PAIR}&interval={INTERVAL}&since={UNIX_TS}
```

Parameters:

- `pair`: e.g. `XBTUSD`, `ETHUSD`, `SOLUSD`
- `interval`: minutes — `60` (1h), `240` (4h), `1440` (1d)
- `since`: Unix timestamp (seconds) of the start of the range

Response structure (confirmed):

```json
{
  "error": [],
  "result": {
    "XXBTZUSD": [
      [timestamp, open, high, low, close, vwap, volume, count],
      ...
    ],
    "last": <last_timestamp>
  }
}
```

Notes:

- Prices are returned as quoted strings, not numbers.
- Maximum 720 candles per call; paginate using the `last` field as `since` for the next page.
- Kraken historical data starts from ~2013 for BTC, 2016 for ETH. SOL may have shorter history.
- No API key required for public OHLC endpoint.

### Pair names for MVP2

| Our pair | Kraken pair name                     |
|----------|--------------------------------------|
| BTC/USDT | XBTUSDT (or XXBTZUSD if quoting USD) |
| ETH/USDT | ETHUSDT                              |
| SOL/USDT | SOLUSDT                              |

Verify pair existence before relying on them: `GET https://api.kraken.com/0/public/AssetPairs`

---

## 3. CoinGecko Peg History — SA-9 Depeg Overlay

### Purpose

The SA-9 depeg overlay needs hourly USDT/USDC/DAI peg data to flag episodes where the base currency itself depegged (e.g., USDC March 2023 SVB event, USDT May 2022 Terra/Luna). These events require special handling — trades during a depeg may report misleading PnL in USDT terms.

### Endpoint (confirmed live)

```text
GET https://api.coingecko.com/api/v3/coins/{id}/market_chart
    ?vs_currency=usd
    &days={N}
    &interval=hourly
```

Coin IDs:

| Stablecoin | CoinGecko id |
| ------------ | ------------- |
| USDT | `tether` |
| USDC | `usd-coin` |
| DAI | `dai` |

### Granularity Rules (important)

When `interval=hourly` is explicitly passed:

- Returns hourly data for up to the **past 100 days** on the free tier
- For historical data beyond 100 days you MUST paginate using `from` / `to` (Unix timestamps, seconds) with the range endpoint:

```text
GET https://api.coingecko.com/api/v3/coins/{id}/market_chart/range
    ?vs_currency=usd
    &from={unix_start}
    &to={unix_end}
```

The range endpoint returns hourly data when the range is ≤ 90 days, daily when > 90 days on the free tier. For full hourly history back to 2020, use 90-day windows iterated.

### Rate Limits

- Free tier (no key): ~10-30 calls/minute. Use exponential backoff.
- Pro API: `https://pro-api.coingecko.com/api/v3/...` with `x-cg-pro-api-key` header — higher rate limits and full historical access.

### Sample Response

```json
{
  "prices": [
    [1696118400000, 0.9998611759],
    [1696122000000, 0.9997823412],
    ...
  ],
  "market_caps": [...],
  "total_volumes": [...]
}
```

Timestamps are Unix milliseconds. Divide by 1000 for seconds.

### Depeg Detection Threshold (SA-9 reference)

Flag a depeg event when stablecoin price deviates > 0.5% from $1.00 for ≥ 2 consecutive hours:

- USDT < 0.995 or > 1.005
- USDC < 0.995 or > 1.005
- DAI < 0.995 or > 1.005

Known historical depeg events to validate against:

- USDT: May 12, 2022 (~$0.953 intraday low during Terra/Luna collapse)
- USDC: March 11, 2023 (~$0.870 low, Silicon Valley Bank collapse)
- DAI: March 11, 2023 (followed USDC depeg, USDC collateral exposure)

---

## 4. Parquet Schema

### 4.1 OHLCV Parquet Schema

Column names match Binance CSV headers (confirmed from downloaded files):

| Column | Dtype | Notes |
| -------- | ------- | ------- |
| `open_time` | `int64` | Unix milliseconds (Binance native) |
| `open` | `float64` | |
| `high` | `float64` | |
| `low` | `float64` | |
| `close` | `float64` | |
| `volume` | `float64` | Base asset volume (BTC, ETH, SOL) |
| `close_time` | `int64` | Unix milliseconds |
| `quote_asset_volume` | `float64` | USDT volume |
| `number_of_trades` | `int64` | |
| `taker_buy_base_asset_volume` | `float64` | |
| `taker_buy_quote_asset_volume` | `float64` | |
| `ignore` | `float64` | Binance filler column — drop on ingest |

Add derived columns on ingest:

| Column | Dtype | Notes |
| -------- | ------- | ------- |
| `open_time_utc` | `timestamp[us, tz=UTC]` | Derived from `open_time` |
| `pair` | `string` (categorical) | e.g. `BTCUSDT` |
| `interval` | `string` (categorical) | e.g. `1h` |
| `source_file` | `string` | Original ZIP filename |
| `sha256` | `string` | SHA256 of source ZIP |

### 4.2 aggTrades Parquet Schema

| Column | Dtype | Notes |
| -------- | ------- | ------- |
| `agg_trade_id` | `int64` | |
| `price` | `float64` | |
| `quantity` | `float64` | Base asset |
| `first_trade_id` | `int64` | |
| `last_trade_id` | `int64` | |
| `transact_time` | `int64` | Unix milliseconds |
| `is_buyer_maker` | `bool` | True = seller was taker |

Add on ingest: `pair`, `source_file`, `sha256`, `transact_time_utc`.

### 4.3 L2 Diff Parquet Schema (live collector output)

| Column | Dtype | Notes |
| -------- | ------- | ------- |
| `event_time` | `int64` | Unix milliseconds (from WS event) |
| `first_update_id` | `int64` | `U` field from diff stream |
| `final_update_id` | `int64` | `u` field from diff stream |
| `side` | `string` (categorical) | `bid` or `ask` |
| `price` | `float64` | |
| `quantity` | `float64` | 0 = level removal |
| `pair` | `string` (categorical) | |

One file per day per pair, covering all bid/ask updates. Snapshots stored separately (see §7).

### 4.4 Partition Scheme (Hive-style)

```text
data/
  ohlcv/
    interval=1h/
      pair=BTCUSDT/
        year=2020/
          month=01/
            part-00000.parquet
          month=02/
            part-00000.parquet
    interval=4h/
      ...
    interval=1d/
      ...
  agg_trades/
    pair=BTCUSDT/
      year=2020/
        month=01/
          part-00000.parquet
  l2_diffs/
    pair=BTCUSDT/
      year=2026/
        month=08/
          day=25/
            part-00000.parquet
  l2_snapshots/
    pair=BTCUSDT/
      year=2026/
        month=08/
          day=25/
            snapshot-<updateId>.parquet
  peg_history/
    coin=tether/
      year=2020/
        month=01/
          part-00000.parquet
```

Compression: **Zstd** (level 3) per the pre-registration. Use `compression="zstd"` in PyArrow or polars.

---

## 5. SHA256 Fingerprinting Workflow

### 5.1 Per-file Fingerprinting (Python)

```python
import hashlib, json, pathlib, datetime


def sha256_file(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def register_file(
    manifest: dict,
    path: pathlib.Path,
    file_type: str,  # "ohlcv_zip" | "parquet" | "l2_snapshot" | ...
    pair: str | None = None,
    interval: str | None = None,
    period: str | None = None,  # e.g. "2020-01"
) -> str:
    digest = sha256_file(path)
    key = str(path.relative_to(pathlib.Path("data")))
    manifest["files"][key] = {
        "sha256": digest,
        "type": file_type,
        "pair": pair,
        "interval": interval,
        "period": period,
        "registered_at": datetime.datetime.utcnow().isoformat() + "Z",
        "size_bytes": path.stat().st_size,
    }
    return digest
```

### 5.2 data-manifest.json Structure

```json
{
  "schema_version": "1",
  "experiment_id": "swing-mvp1-v1",
  "generated_at": "2026-08-25T00:00:00Z",
  "manifest_sha256": null,
  "files": {
    "ohlcv/interval=1h/pair=BTCUSDT/year=2020/month=01/part-00000.parquet": {
      "sha256": "abc123...",
      "type": "parquet",
      "pair": "BTCUSDT",
      "interval": "1h",
      "period": "2020-01",
      "registered_at": "2026-08-25T10:00:00Z",
      "size_bytes": 12345
    }
  }
}
```

The `manifest_sha256` field is computed last — it is the SHA256 of the manifest file itself with the `manifest_sha256` field set to `null`, serialized as compact JSON (no indentation, keys sorted). This hash goes into `pre-registration-v1.md §16`.

### 5.3 Binance-provided Checksum Verification

Before converting ZIP → Parquet, verify against Binance's own checksum:

```python
def verify_binance_checksum(zip_path: pathlib.Path, checksum_url: str) -> bool:
    import urllib.request

    with urllib.request.urlopen(checksum_url) as r:
        expected_hex = r.read().decode().split()[0]
    actual = sha256_file(zip_path)
    return actual == expected_hex
```

If this fails, the ZIP is corrupted — re-download. Do not proceed to Parquet conversion.

---

## 6. Gap Detection + Validation Rules

### 6.1 Structural Completeness

| Check | Condition | Severity |
| ------- | ----------- | ---------- |
| Monthly file exists | All months from pair-start to current covered | CRITICAL — halt |
| Bar count per month | 1h: 672–744 bars; 4h: 168–186 bars; 1d: 28–31 bars | WARNING if ±5% |
| No duplicate timestamps | `open_time` must be unique per (pair, interval) | CRITICAL |
| Strict monotonicity | `open_time[i+1] == open_time[i] + interval_ms` | CRITICAL — gap or dupe |

### 6.2 Price Sanity

| Check | Rule |
| ------- | ------ |
| No zero/negative values | `open`, `high`, `low`, `close`, `volume` all > 0 |
| OHLC ordering | `low <= open <= high` and `low <= close <= high` |
| Max inter-bar price jump | `abs(close[i] / close[i-1] - 1) < 0.50` — flag if >50% move |
| Zero-volume bars | Flag but do not drop — may be a valid exchange halt |

### 6.3 Volume Anomalies

| Check        | Rule                                                               |
|--------------|--------------------------------------------------------------------|
| Volume spike | `volume[i] > mean(volume) + 10 * std(volume)` → flag for review    |
| Volume floor | If >3 consecutive bars have `volume == 0` → likely data corruption |

### 6.4 Cross-Source Consistency (for MVP2 Kraken cross-check)

When Kraken data is available:

- On any 1h bar, compare Binance `close` vs Kraken `close` for same timestamp
- Flag if divergence > 1% — typically indicates a fat-finger, exchange glitch, or data error
- This check is not applicable for MVP1 (single source)

### 6.5 aggTrades ↔ OHLCV Consistency

- For each OHLCV 1h bar, the OHLC range must contain the aggTrades price range for that hour
- aggTrades volume sum (by hour) should approximately match OHLCV `quote_asset_volume` for that bar
- Acceptable tolerance: ±1% (rounding from partial trades at bar boundary)

### 6.6 Gap Handling Policy

| Gap type | Action |
| ---------- | -------- |
| Missing ZIP from Binance | Re-download; if persistently unavailable, document in manifest with `"status": "missing"` |
| SOL before 2020-09 | Expected — document as structural gap, not a data error |
| Isolated missing bar (exchange halt) | Document; backtest engine must handle `NaN` volume gracefully |
| Multiple missing bars (>6h on 1h data) | Mark entire day as `"unreliable"` in manifest |

---

## 7. L2 Collector Spec

### 7.1 Binance Diff Depth WebSocket

**Endpoint:**

```text
wss://stream.binance.com:9443/ws/{symbol}@depth@100ms
```

Examples:

```text
wss://stream.binance.com:9443/ws/btcusdt@depth@100ms
wss://stream.binance.com:9443/ws/ethusdt@depth@100ms
wss://stream.binance.com:9443/ws/solusdt@depth@100ms
```

For multiple streams on one connection (preferred):

```text
wss://stream.binance.com:9443/stream?streams=btcusdt@depth@100ms/ethusdt@depth@100ms/solusdt@depth@100ms
```

**Message structure per update:**

```json
{
  "e": "depthUpdate",
  "E": 1609459200123,    // event time (ms)
  "s": "BTCUSDT",
  "U": 1234567890,       // first update ID in this event
  "u": 1234567899,       // final update ID in this event
  "b": [["29000.00", "1.500"], ...],  // bid updates [price, qty]
  "a": [["29001.00", "0.000"], ...]   // ask updates (qty=0 = removal)
}
```

### 7.2 Snapshot Bootstrap (CRITICAL — known gotcha)

The diff stream alone is insufficient to build a local order book. You MUST bootstrap with a REST snapshot. The exact procedure (from Binance docs) is:

**Step 1:** Open the WebSocket connection and START BUFFERING diff events immediately. Note the `U` value of the very first event received.

**Step 2:** Fetch a REST depth snapshot:

```text
GET https://api.binance.com/api/v3/depth?symbol=BTCUSDT&limit=5000
```

Response: `{"lastUpdateId": N, "bids": [...], "asks": [...]}`

**Step 3:** Validate timing. If `snapshot.lastUpdateId < buffered_events[0].U`, the snapshot is stale — go back to Step 2. This means the stream has moved past the snapshot; a newer snapshot is needed.

**Step 4:** Filter buffered events. Discard all events where `event.u <= snapshot.lastUpdateId`. These updates are already reflected in the snapshot.

**Step 5:** Initialize local book with snapshot state. Set current `lastUpdateId = snapshot.lastUpdateId`.

**Step 6:** Apply remaining buffered events in order. For each event, assert `event.U == currentLastUpdateId + 1`; if not, there is a gap — restart from Step 1.

**Step 7:** From this point, apply live stream events sequentially. For each:

- Assert `event.U == currentLastUpdateId + 1` (no gaps)
- Apply bid/ask updates (price level → qty; qty=0 removes the level)
- Set `currentLastUpdateId = event.u`

**On disconnect:** Re-run the full bootstrap (Steps 1–7). Do NOT attempt to resume — assume the stream state is inconsistent.

**Snapshot storage:** Persist each bootstrap snapshot as Parquet to `data/l2_snapshots/pair=X/year=Y/month=M/day=D/snapshot-<updateId>.parquet` for replay capability.

### 7.3 Kraken v2 WebSocket Order Book

> MVP2 prep. Not needed for MVP1.

**Endpoint:** `wss://ws.kraken.com/v2`

**Subscribe:**

```json
{
  "method": "subscribe",
  "params": {
    "channel": "book",
    "symbol": ["BTC/USD", "ETH/USD", "SOL/USD"],
    "depth": 100,
    "snapshot": true
  }
}
```

**CRC32 Verification:**

Every book message includes a `checksum` field. To verify:

1. Concatenate the top 10 asks (ascending price) + top 10 bids (descending price)
2. For each level: append `price_string` + `qty_string` (strip trailing zeros and decimal point, e.g., `29000.00` → `2900000`, `1.5` → `15`)
3. Compute CRC32 of the resulting concatenated string
4. Compare against the `checksum` field (uint32)

If checksum fails on an update: discard book state and re-subscribe (request a new snapshot). This is the Kraken equivalent of the Binance gap-detection restart.

**Reference:** <https://docs.kraken.com/api/docs/websocket-v2/book>

### 7.4 L2 Storage Sizing Estimate

Assumptions:

- BTC: ~15 depth updates/second at 100ms cadence = ~150 events/second but each event has ~5–20 price level changes avg
- Active trading hours see burst to ~50 level changes/event
- Each level change: ~40 bytes in Parquet (price f64 + qty f64 + metadata) with Zstd

Conservative estimate per pair per day:

```text
BTC:  150 events/s × 10 avg changes/event × 40 bytes × 86400 s / (4 compression ratio) ≈ 1.3 GB/day
ETH:  ~0.8 GB/day (lower volume, similar depth activity)
SOL:  ~0.4 GB/day (lower absolute depth activity)

Total raw: ~2.5 GB/day for 3 pairs
With Zstd compression (ratio ~4x on float64 price/qty): ~625 MB/day
Monthly: ~18 GB/month
Annually: ~215 GB/year
```

**Aggressive estimate** (high volatility events, BTC during news): ~3–4× the above.

Plan for **1–2 TB SSD** for 12 months of shadow-phase L2 data. aggTrades are smaller (tick-by-tick but not full book) — estimate 200–400 GB/year for 3 pairs.

---

## 8. Download Scope Summary

### MVP1 Required Downloads (backtest window)

| Pair | Timeframes | Date range | Est. ZIP count |
| ------ | ----------- | ----------- | --------------- |
| BTCUSDT | 1h, 4h, 1d | 2020-09 → 2026-07 | ~183 ZIPs |
| ETHUSDT | 1h, 4h, 1d | 2020-09 → 2026-07 | ~183 ZIPs |
| SOLUSDT | 1h, 4h, 1d | 2020-09 → 2026-07 | ~183 ZIPs |

Total: ~549 ZIPs × 3 timeframes = 549 klines ZIPs + optional aggTrades (~549 ZIPs). Uniform start date per pre-reg v1 §4.

aggTrades are optional for the OHLCV backtest but required for the `OrderbookWalkFillModel` (shadow phase). Download them concurrently if storage allows.

### Priority Order for Week 1–2 Ingest

1. BTCUSDT 1h, 4h, 1d — all months (backtest engine development)
2. ETHUSDT 1h, 4h, 1d — all months
3. SOLUSDT 1h, 4h, 1d — from 2020-09
4. aggTrades — BTCUSDT first, then ETH, then SOL (shadow phase prep)
5. Peg history — USDT, USDC, DAI from CoinGecko (SA-9 overlay)

---

## 9. data-manifest.json Bootstrap

The manifest must be created before any file is ingested. Initial structure:

```json
{
  "schema_version": "1",
  "experiment_id": "swing-mvp1-v1",
  "created_at": "2026-08-25T00:00:00Z",
  "last_updated": null,
  "manifest_sha256": null,
  "known_gaps": [
    {
      "pair": "SOLUSDT",
      "gap_type": "structural",
      "description": "SOL not listed on Binance before 2020-09. Data starts 2020-09-01.",
      "affects_intervals": ["1h", "4h", "1d"]
    }
  ],
  "files": {}
}
```

After all files are ingested, compute `manifest_sha256` (SHA256 of manifest with `manifest_sha256: null`, compact JSON, sorted keys) and fill it in. This final hash goes into `pre-registration-v1.md §16`.
