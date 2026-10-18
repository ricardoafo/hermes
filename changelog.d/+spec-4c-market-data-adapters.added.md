- **Spec 4c — market data adapters** (`src/hermes/adapters/market_data/`):
  `binance_historical.py`, a filesystem-backed `MarketDataPort` that streams
  `Bar` and `Trade` objects in order from Binance Vision klines and aggTrades
  ZIP archives under `$HERMES_DATA_DIR`, with no network I/O;
  `binance_live_ws.py`, a WebSocket L2 depth adapter for the shadow phase
  (`stream_orderbook_diffs`, `get_snapshot` for the book bootstrap, and
  sequence-gap detection); and `consolidator.py`, a `BarConsolidator` that
  builds closed OHLCV bars from a trade stream for every `KlineInterval`,
  UTC epoch-aligned, never using a trade at or after the bar's close time.
