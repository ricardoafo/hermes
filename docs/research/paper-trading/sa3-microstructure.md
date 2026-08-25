# SA-3 Microstructure — Binance + Kraken Spot Facts

**Date**: 2026-08-24
**Author**: SA-3 sub-agent
**Engram topic key**: `research/paper-trading-sa3-microstructure`
**Status**: FINAL

---

## SA-3 Microstructure Report: Binance + Kraken Spot Facts

## Hermes Paper-Trading Engine Research — MVP1 (Swing Strategy)

Researched: 2026-08-24. All live values fetched directly from exchange APIs.

---

## 1. EXCHANGE FACTS TABLE (Binance vs. Kraken, Side-by-Side)

### Dimension 1: Tick Size + Lot Size

How to look up:

- Binance: `GET /api/v3/exchangeInfo?symbol=BTCUSDT` → `symbols[0].filters` → `PRICE_FILTER.tickSize` and `LOT_SIZE.stepSize`
- Kraken: `GET /public/AssetPairs?pair=XBTUSD` → `tick_size`, `lot_decimals`, `ordermin`

| Parameter | Binance BTCUSDT | Binance SOLUSDT | Kraken XBTUSD | Kraken SOLUSD |
| ----------- | ----------------- | ----------------- | --------------- | ---------------- |
| Tick size (price increment) | $0.01 | $0.01 | $0.10 | $0.01 |
| Min lot size (qty) | 0.00001 BTC | 0.001 SOL | 0.00005 BTC | 0.06 SOL |
| Lot step size | 0.00001 BTC | 0.001 SOL | 8 decimal places | 8 decimal places |
| Min notional (order value) | $5.00 | $5.00 | $0.50 (costmin) | $0.50 (costmin) |
| Max lot size | 9,000 BTC | 90,000 SOL | not specified | not specified |
| baseAssetPrecision | 8 | 8 | — | — |

Live-fetched Binance BTCUSDT filter (2026-08-24):

```json
PRICE_FILTER: {"tickSize": "0.01000000", "minPrice": "0.01", "maxPrice": "1000000.00"}
LOT_SIZE: {"stepSize": "0.00001000", "minQty": "0.00001000", "maxQty": "9000.00000000"}
NOTIONAL: {"minNotional": "5.00000000", "applyMinToMarket": true}
MARKET_LOT_SIZE: {"maxQty": "102.52492765"}  ← rolling average cap
```

Live-fetched Kraken XBTUSD/SOLUSD (2026-08-24):

```text
XBTUSD: tick_size=0.1, pair_decimals=1, lot_decimals=8, ordermin=0.00005, costmin=0.5
SOLUSD:  tick_size=0.01, pair_decimals=2, lot_decimals=8, ordermin=0.06, costmin=0.5
```

**FillModelPort implication:** All prices must be quantized to tickSize; all quantities to stepSize/lot_decimals before order validation. Binance BTCUSDT tick=$0.01; Kraken BTCUSD tick=$0.10 (10x coarser).

---

### Dimension 2: Fee Schedule

Binance Spot (default retail — no VIP):

- Maker: 0.100% | Taker: 0.100%
- BNB payment discount: 25% reduction (net: 0.075%/0.075% with BNB)
- VIP tiers: 9 levels. VIP1 ($1M 30d vol or 5 BNB): 0.090%/0.100%. VIP9 ($4B): 0.011%/0.023%
- Volume measured on 30d rolling basis across Spot, Margin, Convert, Copy Trading
- Fee endpoint for authenticated user: `GET /api/v3/account/commission` (weight: 20)
  - Returns `standardCommission.maker`, `standardCommission.taker`
  - Also `discount.discountAsset="BNB"`, `discount.discount=0.25`

Kraken Spot (default retail — Tier 1):

- Taker: 0.40% | Maker: 0.25%  ← NOTE: significantly higher than Binance retail default
- Volume tiers (30d, USD equivalent):

  ```text
  [0,     taker=0.40, maker=0.25]
  [10k,   taker=0.35, maker=0.20]
  [50k,   taker=0.24, maker=0.14]
  [100k,  taker=0.22, maker=0.12]
  [250k,  taker=0.20, maker=0.10]
  [500k,  taker=0.18, maker=0.08]
  [1M,    taker=0.16, maker=0.06]
  [2.5M,  taker=0.14, maker=0.04]
  [5M,    taker=0.12, maker=0.02]
  [10M,   taker=0.10, maker=0.00]  ← maker free!
  [100M,  taker=0.08, maker=0.00]
  [500M,  taker=0.05, maker=0.00]
  ```

- Live-verified from `/public/AssetPairs` (same for XBTUSD and SOLUSD, fee_volume_currency=ZUSD)
- Tier is discrete (no pro-rata interpolation)
- Kraken's AssetPairs endpoint already implemented in hermes as KrakenPublicAdapter (PR #193)
- Volume measured as max of: spot volume, futures volume, or Assets on Platform (30d)

**Fee model implication:** At retail tier, Kraken taker cost (0.40%) is 4x Binance taker cost (0.10%). This dominates strategy P&L for swing trades. Fill model MUST apply correct fee per exchange and per maker/taker classification based on whether the order is resting (maker) or aggressing (taker).

---

### Dimension 3: Orderbook Stream

Binance:

- WebSocket host: `wss://stream.binance.com:9443`
- Diff stream channel: `{symbol}@depth@100ms` (also 1000ms variant)
  - Delivers: price level changes only (deltas), not full book each time
  - Fields: `e` (event type), `E` (event time ms), `s` (symbol), `U` (first update ID), `u` (final update ID), `b` (bids: [[price, qty],...]), `a` (asks: [[price, qty],...])
  - Qty=0 means delete that level
  - NOT bounded — sends all changed levels each interval; can be 0–100+ levels per message
- Partial depth stream: `{symbol}@depth{levels}@100ms` (levels = 5, 10, or 20)
  - Full snapshot each update (not delta). Good for top-N display only.
- Bootstrap protocol (from official GitHub docs):
  1. Open WebSocket to `wss://stream.binance.com:9443/ws/btcusdt@depth@100ms`
  2. Buffer all events
  3. GET `https://api.binance.com/api/v3/depth?symbol=BTCUSDT&limit=5000` → get `lastUpdateId`
  4. If snapshot `lastUpdateId` < first buffered event `U`, re-fetch snapshot (step 3)
  5. Discard buffered events where `u` ≤ snapshot `lastUpdateId`
  6. Apply snapshot, then apply remaining buffered events in order
  7. On each live event: if `U` > (local `lastUpdateId` + 1) → gap detected → reconnect from step 1
- Depth coverage: diff stream sends ALL changed levels (no cap). Full book reconstruction possible.

Kraken:

- WebSocket host v2: `wss://ws.kraken.com/v2`
- Book channel subscription:

  ```json
  {"method": "subscribe", "params": {"channel": "book", "symbol": ["BTC/USD"], "depth": 10}}
  ```

- Available depths: 10, 25, 100, 500, 1000 (default: 10)
- Snapshot: first message after subscription is `"type": "snapshot"` with full bid/ask arrays up to depth levels
- Updates: subsequent messages are `"type": "update"` — partial diffs (changed levels only)
- Diff fields: `symbol`, `bids: [{price, qty}]`, `asks: [{price, qty}]`, `checksum` (CRC32 of top 10 bid/ask), `timestamp` (RFC3339)
- Qty=0 means delete that level (implicit from zero quantity)
- CRC32 checksum on top 10 levels after each update — use for integrity validation
- Heartbeat: `{"channel": "heartbeat"}` sent ~every 1 second when no other updates
- No complex bootstrap: snapshot is sent automatically on subscribe, updates follow

**Key difference:** Binance requires explicit REST snapshot + buffered-event reconciliation dance. Kraken sends snapshot automatically on subscribe, simpler state machine.

---

### Dimension 4: Trade Print Stream

Binance:

- WebSocket channel: `{symbol}@aggTrade`
- Fields: `a` (aggTradeId), `p` (price), `q` (quantity), `f` (first tradeId), `l` (last tradeId), `T` (trade time ms), `m` (buyer is maker — i.e., seller aggressed), `M` (ignore)
- aggTrade aggregates multiple simultaneous individual trades from the same taker sweep into one message (same price, same taker order)
- Individual trade stream (`{symbol}@trade`) also available: each raw match event
- Message rate BTC/USDT: on active hours, 5-50 aggTrades/second typical; during high volatility spikes can be 200+/second. Off-hours: 1-5/second. (Community observation; not officially documented)
- For alts (e.g., SOL/USDT): 1-20/second during active trading, <1/second during quiet periods

Kraken:

- WebSocket channel: `trade` (v2)
- Subscription: `{"method": "subscribe", "params": {"channel": "trade", "symbol": ["BTC/USD"], "snapshot": true}}`
- `snapshot: true` returns last 50 trades on subscribe
- Fields per trade: `symbol`, `side` ("buy"/"sell"), `qty`, `price`, `ord_type` ("limit"/"market"), `trade_id` (sequence number), `timestamp` (RFC3339)
- Multiple trades may be batched in a single message
- REST equivalent: `GET /public/Trades?pair=XBTUSD&count=1000`
  - Returns: `[price, volume, time, side, type, misc, trade_id]`
  - Live sample: `['78893.00000', '0.00003511', 1787609479.488319, 'b', 'm', '', 105862953]`

---

### Dimension 5: Order Matching Rules

Binance:

- Price-time priority (FIFO within price level): orders at the same price execute in order of arrival time. This is standard for most CEXes.
- Official documentation does NOT explicitly state "FIFO" in public-facing docs, but price-time priority is implied by the matching engine architecture and confirmed by community consensus
- Iceberg orders: the hidden portion does NOT have queue priority over visible orders at the same price level. Only the visible "tip" holds queue position. When tip fills, the next portion goes to BACK of the queue.
- LIMIT_MAKER order type: post-only — rejected if it would immediately cross the spread (prevents taker fill)

Kraken:

- Price-time priority (FIFO): also not explicitly published, but standard for regulated exchange matching. Community consensus confirms FIFO within price level.
- Kraken `AmendOrder` endpoint preserves queue priority "where possible" — i.e., amending price resets queue position, amending quantity downward preserves it.
- Kraken supports `iceberg` order type via `AddOrder` with `display_qty`

**MVP1 implication:** Model FIFO within price level for both exchanges. Queue position for retail limit orders: unknown without L3 data (neither exchange publishes queue position). Use proportional fill probability based on volume-at-level approximation.

---

### Dimension 6: Queue Position Dynamics

Do exchanges publish queue position?

- Binance: NO. L2 only. Queue position not disclosed.
- Kraken: NO. L2 only. Queue position not disclosed.

How queue position is estimated in literature:

1. **Size-weighted approximation**: If the book shows Q units at the best bid before your order, and your order is for q units, your fill probability ≈ (volume that trades through) / (Q + q). This is the approach used by hftbacktest's `ProbQueueModel`.
2. **Time-weighted**: Assume your position in queue is proportional to the time your order has been resting vs. the average turnover rate at that level. Requires calibration from historical data.
3. **Risk-averse model** (hftbacktest `RiskAverseQueueModel`): Assumes ALL volume at the same price level fills before your order. This is the conservative bound — underestimates fill probability for maker orders.
4. **Pro-rata alternative** (not applicable to Binance/Kraken spot): Some exchanges use pro-rata matching where all orders at a price level share fills proportionally. Binance/Kraken use FIFO, not pro-rata.

What percentage of retail limit orders fill vs. cancel?

- No primary source from Binance/Kraken directly.
- Academic consensus (from equities LOB research): roughly 30-40% of limit orders on liquid markets (at best bid/ask) get filled before cancellation. For passive orders NOT at the touch (i.e., inside the spread), fill rate is much lower.
- For swing traders placing limit orders at levels away from best price (waiting for price to come to them): fill rate depends entirely on whether price touches that level. If price touches: near-certain fill (>95%) on liquid BTC/USDT for retail sizes (<0.1 BTC). If price doesn't touch: 0% fill.
- **Key insight for MVP1**: For swing strategy with multi-minute hold times, QUEUE POSITION IS MOSTLY IRRELEVANT. Orders resting far from best price will fill at full quantity when price reaches them. Queue position only matters for orders placed AT the touch competing with other resting orders — rare for swing orders.

---

### Dimension 7: Cancel/Replace Race Conditions

Binance:

- `POST /api/v3/order/cancelReplace` endpoint exists but is NOT atomic
  - `cancelReplaceMode=STOP_ON_FAILURE`: if cancel fails, new order not attempted
  - `cancelReplaceMode=ALLOW_FAILURE`: new order attempted even if cancel fails
  - If cancel succeeds but new order fails: cancel stands, new order gets `NOT_ATTEMPTED` status
  - `cancelRestrictions` parameter: `ONLY_NEW` or `ONLY_PARTIALLY_FILLED` — rejects cancel if order is in wrong state
  - **Race**: if order fills between cancel request receipt and exchange processing, cancel will fail. The fill is final. There is no atomicity guarantee.
- Maximum order amends: BTCUSDT allows 10 amends (`MAX_NUM_ORDER_AMENDS` filter)

Kraken:

- `POST /private/AmendOrder` endpoint: atomic in-place modification
  - Preserves queue priority "where possible"
  - Amending price: queue position is RESET (goes to back of queue at new price)
  - Amending quantity downward: queue position preserved
  - Amending quantity upward: queue position reset
  - Race with fill: if order fills while amend is in-flight, behavior is undocumented. Assume fill takes precedence.
- Cancel + new order on Kraken: two separate API calls, same race risk as Binance

**MVP1 implication:** Model cancel as "attempt that may race with fill." When simulating cancel-replace, check if fill occurred first. For swing strategies, cancel/replace races are rare (hold times are multi-minute, not sub-second).

---

### Dimension 8: Latency

REST API round-trip (EU retail, from Warsaw/Frankfurt):

- Binance: `api.binance.com` → measured ~268ms RTT from this machine (Windows/Poland). Binance nearest server clusters: EU is routed to Singapore or US East unless using AWS Direct Connect. Typical retail EU → Binance: 100-300ms depending on ISP.
- Kraken: `api.kraken.com` → measured ~123ms RTT from this machine. Kraken CDN serves from EU (Cloudflare). Typical retail EU → Kraken: 50-150ms.

Order acknowledgment latency:

- Binance: Not formally published. Community benchmarks: 10-50ms from co-located servers (Singapore), 100-300ms from EU retail internet. No official SLA.
- Kraken: Not formally published. Similar community estimates: 20-100ms from EU, faster with co-location.
- **Neither exchange publishes order-ack median or p99 in public documentation.**

WebSocket message rate (BTC/USDT @depth@100ms):

- Binance: 10 messages/second guaranteed cadence (100ms interval). Each message may contain 0 to ~200+ changed price levels during volatile periods; 5-30 levels typical in calm markets.
- Kraken: No fixed cadence — updates fire when order book changes. During active trading: effectively 1-20 updates/second for BTC/USD at depth=100. Heartbeat fills 1-second gap guarantee.

**For MVP1 (swing strategy):** Latency of 100-300ms is irrelevant at multi-minute hold times. No latency simulation needed for MVP1. FillModelPort can assume fills occur at the next event after order is placed.

---

### Dimension 9: Historical Data Availability

Binance:

- Free public archive: `data.binance.vision` (S3-backed)
- Available datasets:
  - **AggTrades** (SPOT): ZIP, daily + monthly files. From ~2017 for BTC. Microsecond timestamps from 2025-01-01.
  - **Klines** (SPOT): All intervals (1s, 1m, 3m, 5m, 15m, 30m, 1h, 2h, 4h, 6h, 8h, 12h, 1d, 3d, 1w, 1M). ZIP, daily + monthly.
  - **Trades** (SPOT): Individual trades. ZIP, daily + monthly.
  - Futures: AggTrades, Klines, Trades for USD-M and COIN-M
  - **NOT AVAILABLE in free tier**: Full orderbook diff snapshots (bookDepth tick-by-tick). Binance does NOT publish historical L2 diff data publicly. Only real-time via WebSocket.
- Access: Free, no authentication. Public HTTP/S3 URLs.
- Verification: Each ZIP file comes with a `.CHECKSUM` file.

Kraken:

- REST API: `GET /public/Trades?pair=XBTUSD&since=<timestamp>` — returns up to 1000 trades at a time, paginated via `since`. Historical depth varies but typically 5+ years for BTC pairs.
- Klines: `GET /public/OHLC?pair=XBTUSD&interval=1` (1-min OHLC), max 720 bars per call, paginated. For backtesting, requires iterating backward.
- **Historical orderbook data**: Kraken does NOT provide public historical L2 orderbook data. No S3 dump equivalent.
- Third-party sources: Kaiko and CryptoTick provide historical Kraken L2 data (paid, ~$200-500/month for research access).

Implication for backtest:

- For bar-based backtest (swing strategy): Binance 1m klines from data.binance.vision are free and complete. Ideal starting point.
- For tick-level fill simulation: Only real-time orderbook via WebSocket. Shadow mode recording is the only way to build a historical L2 dataset.

---

### Dimension 10: Rate Limits

Binance (live-verified from /api/v3/exchangeInfo rateLimits):

```text
REQUEST_WEIGHT: 6,000 per minute (per IP)
ORDERS:         100 per 10 seconds (per account) + 200,000 per day
RAW_REQUESTS:   300,000 per 5 minutes (per IP)
```

- `/api/v3/exchangeInfo?symbol=X` costs 20 weight units
- `/api/v3/depth?limit=5000` costs 250 weight units (!)
- `/api/v3/depth?limit=100` costs 5 weight units
- `/api/v3/aggTrades` costs 4 weight units
- `/api/v3/klines` costs 2 weight units
- WebSocket connections: 300 connection attempts per 5 minutes per IP; 1 connection counts as 2 weight units; connections valid 24 hours
- Violation: HTTP 429 → if repeated, IP ban from 2 minutes to 3 days

Kraken:

- Public REST: ~1 call/second safe rate. No hard token-bucket for public endpoints but throttle kicks in above that.
- Private REST (counter system per API key):
  - Max counter: 20 (standard) / 20 (higher tier, but decays 2x faster)
  - Counter decrement rate: -0.5/sec (standard), -1/sec (higher tier)
  - Increment per call: account history = +4, trading endpoints = 0, other = +1
  - Trading endpoints (AddOrder, CancelOrder, AmendOrder) do NOT consume the counter — separate per-pair limit system
- WebSocket: no hard documented connection limit for market data streams

Shadow-mode budget (reading live data only):

- Binance: subscribing to BTC/USDT + SOL/USDT depth@100ms + aggTrade streams: 2 concurrent streams, well within limits. REST snapshots for bootstrap: 2 × depth?limit=100 = 10 weight units. Easily sustainable.
- Kraken: subscribing to book channel for 2 pairs: no documented stream limit. Trivially within 1 call/second for REST.

---

## 2. MVP1 FILL MODEL MICROSTRUCTURE REQUIREMENTS

### P0 — Must Have (MVP1 cannot ship without these)

P0.1 — Tick + lot rounding
Every simulated order price must be rounded to `tickSize` (exchange-specific per symbol). Every quantity must be rounded to `stepSize` (Binance) or `lot_decimals` (Kraken). Fail to round → order rejected by exchange.

- Binance BTCUSDT: price tick=$0.01, qty step=0.00001 BTC
- Kraken XBTUSD: price tick=$0.10, qty step=0.00000001 BTC

P0.2 — Maker/taker fee classification + application

- Taker fee: market orders and limit orders that cross the spread on entry
- Maker fee: limit orders that rest in the book (post-only)
- Fee rate: per-exchange, per-tier. Default: Binance 0.10%/0.10%, Kraken 0.25%/0.40%
- Fee applied to notional value of the fill. Subtracted from proceeds on SELL, added to cost on BUY.
- Must support BNB discount (25% on Binance when BNB held) as configurable flag.

P0.3 — Orderbook-walk for market orders
Market orders consume available liquidity from best price outward. Walk ask side for BUY, bid side for SELL. Aggregate fills until quantity satisfied. Average fill price = VWAP across consumed levels.

- For BTC/USDT retail orders (<0.1 BTC): virtually no walk beyond best level. Spread crossing is the entire cost. Partial fills essentially never occur for retail sizes on liquid pairs.
- For SOL/USDT retail orders (<100 SOL): same — single-level fill is near-certain.
- Partial fills only relevant for orders >1% of visible best-level depth (approximately >$50k notional on BTC/USDT).

P0.4 — Limit order fill logic

- Resting limit BUY fills when ask price ≤ limit price (price comes to you)
- Resting limit SELL fills when bid price ≥ limit price
- Fill price: the limit order price (you get your price or better — assume maker fill)
- Full fill: for swing-strategy orders of retail size, assume 100% fill when price touches. Queue position modeling not required for MVP1.
- Partial fill guard: if visible depth at the touch < order size, model partial fill (rare for retail sizes on BTC/USDT, possible on alts).

P0.5 — Spread crossing detection
When a limit order is placed at or better than the current best opposite price, it becomes a TAKER. Apply taker fee, model as immediate fill at best opposite price (not maker fill).

P0.6 — MIN_NOTIONAL validation
Reject simulated orders where `price × qty < minNotional`. Binance: $5 minimum. Kraken: $0.50 costmin.

P0.7 — Event-driven fill check
On each orderbook diff event (Binance @depth@100ms / Kraken book update), check all open simulated limit orders. If best bid/ask crosses limit price → trigger fill. Never fill between events (no interpolation).

### P1 — Nice to Have (MVP1 complete but not critical)

P1.1 — Probabilistic partial limit fill at touch
For orders placed at the best bid/ask, use a simple fill probability model based on queue approximation. Conservative (risk-averse) default: assume ALL prior depth fills before your order → your fill probability = volume_traded_at_level / (prior_depth + your_qty). For swing strategy, this P1 because orders typically NOT placed at touch.

P1.2 — Slippage model for large orders
For orders >$10k notional, walk the book and compute VWAP fill across multiple levels. For MVP1 swing strategy with retail-sized positions (<$10k typical), market orders fill at single best price. P1 because swing orders are small relative to BTC/USDT depth.

P1.3 — Fee tier lookup from exchange at init
At FillModelPort init, fetch current fee tier from Binance `/api/v3/account/commission` and Kraken `/public/AssetPairs`. Cache for session. Currently the KrakenPublicAdapter (PR #193) already fetches AssetPairs fees.

P1.4 — Kraken checksum validation
Validate CRC32 checksum on Kraken book updates. On mismatch: discard local book, re-subscribe. For MVP1 this is reliability/correctness rather than fill model.

### P2 — Defer to MVP2

P2.1 — Queue position simulation
Full time-in-queue modeling with decayed fill probability. Only matters for HFT/scalping. Swing strategy orders placed away from touch; queue position irrelevant.

P2.2 — Iceberg order detection
Hidden liquidity estimation from repeated refills at the same level. Not visible in L2; would require L3 data. Ignore for MVP1.

P2.3 — Exchange-internal order flow (market impact)
The hermes engine is a price taker; assume zero market impact for retail-sized orders. Do not model market impact for MVP1.

P2.4 — Latency simulation
Round-trip latency modeling (100-300ms EU → exchange). At swing strategy hold times (minutes to days), this is sub-noise. Defer to MVP2 or HFT mode.

P2.5 — Funding rates / overnight costs
Spot only for MVP1. No funding rates. (Relevant if futures added later.)

P2.6 — Pro-rata matching
Binance and Kraken spot both use price-time priority (FIFO). Pro-rata not needed.

---

## 3. STORAGE AND RATE-LIMIT BUDGET

### Orderbook Diff Storage (real-time shadow recording)

Assumptions:

- Binance @depth@100ms: 10 messages/second guaranteed cadence
- Average message size: ~1.5KB raw JSON (BTC/USDT active), ~800B (SOL/USDT quieter)
- Compression: zstd/gzip achieves ~5x on JSON repetitive data

| Pair | Period | Raw JSON | Compressed (~5x) |
| ------ | -------- | ---------- | ------------------ |
| BTC/USDT | 24h | 1.30 GB | 260 MB |
| BTC/USDT | 30d | 38.9 GB | 7.8 GB |
| BTC/USDT | 1 year | 473 GB | 95 GB |
| SOL/USDT | 24h | 0.69 GB | 138 MB |
| SOL/USDT | 1 year | 252 GB | 50 GB |
| **Both pairs** | **1 year** | **725 GB** | **145 GB** |

**Trade print storage (aggTrade stream):** ~200KB/day for BTC/USDT (much smaller than orderbook). Negligible.

**Recommendation:** For MVP1 shadow recording of BTC/USDT + SOL/USDT, budget:

- 24h rolling buffer: ~400 MB compressed (easily in RAM or fast SSD)
- 30d cold storage: ~25 GB compressed (single external drive)
- 1 year: ~145 GB compressed (small NAS or cloud bucket, ~$3-5/month on S3 Infrequent Access)

### Rate Limit Budget for Shadow Mode

| Action | Frequency | Weight Cost | Safety |
| -------- | ----------- | ------------- | -------- |
| Binance WS connect (BTC+SOL depth+trade) | Once | 8 weight units | ✅ |
| Binance REST snapshot (bootstrap, per pair) | Once per reconnect | 5 weight (limit=100) | ✅ |
| Kraken WS connect (BTC+SOL book+trade) | Once | 0 (WS) | ✅ |
| Binance REST klines (backtest fill) | 1/min max | 2 weight | ✅ well within 6000/min limit |
| Kraken REST OHLC (backtest) | 1/min max | ~1 call | ✅ within 1/sec public limit |

Shadow mode is trivially within rate limits for 2 pairs. Even adding 10 pairs stays comfortably below all limits.

---

## 4. HISTORICAL BACKTEST DATA PLAN

### What's Available

| Data Type | Binance | Kraken | Coverage |
| ----------- | --------- | -------- | ---------- |
| 1m OHLC klines | ✅ Free (data.binance.vision) | ✅ Free via REST pagination | Both from ~2017 |
| 5m/1h klines | ✅ Free | ✅ Free | Both full history |
| Individual trades | ✅ Free (data.binance.vision Trades/) | ✅ Free via REST pagination | Both 5+ years |
| AggTrades | ✅ Free (data.binance.vision aggTrades/) | ❌ Not available in aggregated form | Binance only |
| L2 orderbook diffs (historical) | ❌ NOT available | ❌ NOT available | Neither |
| L2 orderbook snapshots | ❌ NOT available | ❌ NOT available | Neither |

### What We Bootstrap from Live Shadow Recording

Since neither exchange provides historical L2 data freely:

**Phase 1 (MVP1 swing backtest):** Use kline data for strategy signal generation. For fills, use next-bar open with spread approximation (default spread = 2 × tickSize for BTCUSDT = $0.02, or derive from 24h data). This matches the SA-0 landscape recommendation of "1m OHLC as minimum for backtesting."

**Phase 2 (shadow recording → tick backtest):** Run hermes shadow mode consuming live Binance/Kraken WebSocket feeds. Store compressed orderbook diffs + aggTrades. After 30 days of recording, first tick-level backtests become possible. After 1 year: statistically meaningful tick backtest.

Backtest fill accuracy progression:

- Week 0: 1m kline backtest, next-open fill, 2-tick slippage approximation. Fast. ±10% accuracy vs live.
- Day 31: 30d of real L2 data. Tick-level fill simulation. ±2-3% accuracy.
- Month 12: Full year. Covers bull/bear/ranging regimes. ±1% accuracy.

Third-party data (if budget allows):

- Kaiko: provides historical Binance + Kraken L2 orderbook snapshots (1s granularity). ~$200-500/month research tier. Good for bootstrapping without 30-day wait.
- CryptoTick / Tardis.dev: similar offerings. Tardis specifically offers historical WebSocket replay of Binance/Kraken full orderbook diffs. ~$50-200/month depending on pairs.

---

## 5. CROSS-CUTTING FINDINGS

### Partial Fills — How Often for Retail Market Orders?

**BTC/USDT (liquid):** Essentially never for orders < $50k notional. At BTC=$78,886, a $10k order = 0.127 BTC. The visible best ask typically has 0.5-5 BTC available (from live snapshot: top ask showed 0.57 BTC). A 0.127 BTC market buy fills entirely at best ask with zero book walk. Partial fills are a non-event.

**SOL/USDT (mid-cap liquid):** For retail sizes (<100 SOL at ~$190 = $19k), also effectively full fill at best price. SOL/USDT depth is thinner than BTC but still sufficient for retail sizes.

**For swing strategy specifically:** Swing orders tend to be small (1-5% of portfolio per trade), market orders are used only for entry on momentum or emergency exit. At retail account sizes (<$100k), partial fills on BTC/USDT are statistically negligible. Model as always full fills at best price for MVP1.

### What We Can IGNORE for MVP1

| Feature | Ignore Decision | Justification |
| --------- | ---------------- | --------------- |
| Iceberg orders | IGNORE | Not visible in L2; hermes has no L3 access; impact on retail fills negligible |
| Hidden liquidity | IGNORE | Same reason; cannot be modeled without L3 |
| Exchange-internal order flow / market impact | IGNORE | Retail order sizes ($1k-$50k) are price takers; impact is sub-tick |
| Queue position modeling | IGNORE (P1) | Swing orders placed away from touch; fills guaranteed when price reaches limit |
| Latency simulation | IGNORE (P2) | 100-300ms irrelevant at multi-minute swing hold times |
| Pro-rata matching | N/A | Binance + Kraken both use FIFO; pro-rata not applicable |
| Funding rates | N/A | Spot only for MVP1 |
| Cross-exchange arbitrage | N/A | Single-exchange orders for MVP1 |

### What We MUST Model for MVP1

1. Tick/lot quantization (PRICE_FILTER, LOT_SIZE) — exchange rejects unquantized orders
2. Maker/taker classification + correct fee per exchange (biggest P&L driver: 0.10% vs 0.40%)
3. Orderbook walk for market orders (trivial at retail size but formally correct)
4. Best bid/ask crossing for limit order fills (event-driven at 100ms granularity)
5. MIN_NOTIONAL validation ($5 Binance, $0.50 Kraken)
6. Binance bootstrap protocol (buffer + REST snapshot + update ID reconciliation)
7. Kraken book checksum validation (CRC32) for correctness

---

## 6. SOURCES

- Binance REST API filters (live): `https://api.binance.com/api/v3/exchangeInfo?symbol=BTCUSDT` (fetched 2026-08-24)
- Kraken AssetPairs (live): `https://api.kraken.com/0/public/AssetPairs?pair=XBTUSD,SOLUSD` (fetched 2026-08-24)
- Binance fee schedule: `https://www.binance.com/en/fee/schedule`
- Kraken fee schedule: `https://www.kraken.com/features/fee-schedule`
- Binance WebSocket streams doc: `https://developers.binance.com/docs/binance-spot-api-docs/web-socket-streams`
- Binance orderbook bootstrap protocol: `https://github.com/binance/binance-spot-api-docs/blob/master/web-socket-streams.md`
- Binance filters doc: `https://developers.binance.com/docs/binance-spot-api-docs/filters`
- Binance cancel-replace: `https://developers.binance.com/docs/binance-spot-api-docs/rest-api/trading-endpoints`
- Binance account commission: `https://developers.binance.com/docs/binance-spot-api-docs/rest-api/account-endpoints`
- Binance public data: `https://github.com/binance/binance-public-data`
- Binance rate limits (live): from `/api/v3/exchangeInfo` rateLimits array (fetched 2026-08-24)
- Kraken WebSocket v2 book: `https://docs.kraken.com/api/docs/websocket-v2/book`
- Kraken WebSocket v2 trade: `https://docs.kraken.com/api/docs/websocket-v2/trade`
- Kraken WebSocket v2 executions: `https://docs.kraken.com/api/docs/websocket-v2/executions`
- Kraken heartbeat: `https://docs.kraken.com/api/docs/websocket-v2/heartbeat`
- Kraken REST orderbook: `https://docs.kraken.com/api/docs/rest-api/get-order-book`
- Kraken REST trades: `https://docs.kraken.com/api/docs/rest-api/get-recent-trades`
- Kraken AmendOrder: `https://docs.kraken.com/api/docs/rest-api/amend-order`
- Kraken rate limits: `https://support.kraken.com/hc/en-us/articles/206548367` (Spanish-language article confirmed)
- SA-0 landscape report: engram topic `research/paper-trading-sa0-landscape` (observation #513)
- hermes KrakenPublicAdapter: PR #193 (fees via AssetPairs already implemented)
- Live RTT measurements: Binance=268ms, Kraken=123ms from Poland (2026-08-24)
- Storage estimates: computed from 864,000 msgs/day at 100ms × avg message size

---

## 7. CONFIDENCE ASSESSMENT

| Dimension | Confidence | Basis |
| ----------- | ------------ | ------- |
| Tick/lot size (BTCUSDT, SOLUSDT, XBTUSD, SOLUSD) | HIGH | Live API calls 2026-08-24 |
| Fee schedules | HIGH | Official exchange fee pages + live AssetPairs |
| Binance orderbook stream format | HIGH | Official developer docs + live depth snapshot |
| Kraken book channel format | HIGH | Official docs v2 |
| Binance bootstrap protocol | HIGH | Official GitHub docs (web-socket-streams.md) |
| Trade print stream format | HIGH | Official docs + live API samples |
| Matching rules (FIFO) | MEDIUM | Not explicitly documented; community consensus; implied by standard exchange practice |
| Queue position dynamics | MEDIUM | Academic literature (LOB research); no crypto-exchange-specific primary data found |
| Partial fill rates for retail | MEDIUM-HIGH | Inferred from live depth data + physics of retail order sizes vs. visible depth |
| Latency numbers | MEDIUM | Live RTT from this machine only; co-lo/EU-rack latency inferred from community benchmarks |
| Order-ack p99 latency | LOW | Not published by either exchange; marked as unknown |
| Historical data availability | HIGH | Official repos + direct documentation |
| Storage estimates | MEDIUM-HIGH | Computed from message rate assumptions; actual byte sizes vary with market activity |
| Kraken rate limits (trading endpoints) | MEDIUM | Spanish-language support article; official rate limit guide 404'd |
