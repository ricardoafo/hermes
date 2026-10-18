# Change: spec-4c-market-data-adapters

**Status**: Shipped

## Intent

Deliver concrete `MarketDataPort` implementations (protocol defined by spec-4b)
that feed real historical and live data to the backtest and shadow pipelines.

## Scope

### In

- `src/hermes/adapters/market_data/binance_historical.py` — Parquet/CSV-backed
  `MarketDataPort` reading Binance Vision klines + aggTrades from disk.
- `src/hermes/adapters/market_data/binance_live_ws.py` — WebSocket L2 depth
  stream collector implementing `stream_orderbook_diffs`. Shadow-phase only.
- `src/hermes/adapters/market_data/consolidator.py` — bar consolidator
  producing closed 4h/1D bars from tick/trade streams.
- Testing pyramid: ≥30 unit, ≥8 property, ≥3 integration.
- `changelog.d/wave-spec-4c-market-data-adapters.md`.

### Out

- The 24/7 VPS-side L2 collector systemd unit.
- Kaiko / Tardis alternative sources.
- Kraken market data (MVP2).

## Design decisions

- D-1: `BinanceHistoricalAdapter` reads Binance Vision ZIP+CSV from
  `$HERMES_DATA_DIR`. Streams bars and trades in ascending timestamp order.
  No network I/O — pure filesystem reads.
- D-2: `BinanceLiveWsAdapter` bootstraps from REST snapshot then applies
  WebSocket depth diffs. Raises `InfraError` on sequence-number gaps.
  `get_snapshot` and `stream_bars` / `stream_trades` raise `NotImplementedError`
  (shadow-phase only; backtest doesn't need live L2).
- D-3: `BarConsolidator` accepts a trade stream and emits closed bars at a
  target interval. No lookahead — a bar at window T never uses a trade with
  `trade_time > close_time`. Deterministic close_time from open_time + interval.
- D-4: `BinanceHistoricalAdapter` implements full `MarketDataPort`; L2 methods
  raise `NotImplementedError` (historical data has no L2 depth).
