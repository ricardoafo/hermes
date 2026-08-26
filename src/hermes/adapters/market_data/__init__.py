"""Market-data adapters — concrete MarketDataPort implementations.

This sub-package provides two concrete adapters:

- :mod:`binance_historical` — filesystem-backed adapter reading Binance Vision
  klines/aggTrades ZIP archives.
- :mod:`binance_live_ws` — WebSocket L2 depth-stream collector for shadow-phase.
- :mod:`consolidator` — bar consolidator producing closed OHLCV bars from trades.

Reference: spec-4c-market-data-adapters change.md D-1 through D-4.
"""
