### Added

- **spec-4b MarketDataPort** (`src/hermes/ports/market_data.py`,
  `src/hermes/domain/market_data/bar.py`,
  `src/hermes/domain/market_data/trade.py`,
  `src/hermes/domain/market_data/orderbook_diff.py`,
  `openspec/changes/spec-4b-market-data-port/`):
  `@runtime_checkable` Protocol defining the contract for live market-data adapters —
  `stream_bars` (closed-bar consolidated stream), `stream_trades` (tick-by-tick stream),
  `stream_orderbook_diffs` (L2 incremental depth updates), and `get_snapshot` (REST
  bootstrap for L2 state). Domain value objects `Bar`, `Trade` (with `TradeSide` enum),
  and `OrderbookDiff` / `OrderbookDiffLevel` land alongside the port; `Bar` reuses
  `KlineInterval` from the existing klines foundation, and `OrderbookDiff` carries
  exchange sequence numbers (`first_update_id` / `final_update_id`) enabling gap detection
  in adapters. Streams return `AsyncIterator[T]` per PEP 525, matching the async-first
  adapter policy. This is a pure port-introduction change: no concrete adapters ship here.
  Follow-up: spec-4c will deliver Binance and Kraken WebSocket streaming adapters and the
  `Consolidator` that produces bars from a raw trade stream.
