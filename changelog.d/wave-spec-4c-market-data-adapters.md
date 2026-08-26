# spec-4c-market-data-adapters

## Added

- `src/hermes/adapters/market_data/binance_historical.py` — filesystem-backed
  `MarketDataPort` reading Binance Vision klines and aggTrades ZIP archives from
  `$HERMES_DATA_DIR`. Streams `Bar` and `Trade` objects in chronological order
  with no network I/O. L2 methods raise `NotImplementedError` (historical data
  carries no depth).

- `src/hermes/adapters/market_data/binance_live_ws.py` — WebSocket L2 depth
  adapter for shadow-phase. Implements `stream_orderbook_diffs` and
  `get_snapshot` (REST depth snapshot for book bootstrap). Includes
  sequence-number gap detection per SA-3 §Binance L2 snapshot procedure.
  Bar and trade methods raise `NotImplementedError` (shadow-phase adapter).

- `src/hermes/adapters/market_data/consolidator.py` — `BarConsolidator` that
  produces closed OHLCV `Bar` objects from a trade tick stream. Enforces the
  no-lookahead invariant: a bar at window T never uses trades with
  `trade_time >= close_time`. Supports all `KlineInterval` values (1m, 5m, 1h,
  4h, 1d). Window alignment is UTC epoch-aligned.

## Tests

- 30+ unit tests covering OHLCV aggregation, CSV/ZIP reader roundtrip, depth
  message parsing, config validation, and error paths.
- 8 property-based tests (Hypothesis) verifying `bar.high >= every trade price`,
  `bar.volume == sum(trade.quantity)`, no-lookahead, OHLCV ordering invariants,
  ascending bar order, and close_time > open_time.
- 3 integration tests: historical adapter + fixture ZIP → bars, multi-ZIP
  chronological merging, and the full adapter → consolidator pipeline.
