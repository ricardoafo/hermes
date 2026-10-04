- **Backtest phase 2: real fill path** (`src/hermes/application/execution/kernel.py`,
  `src/hermes/application/backtest/replay.py`,
  `src/hermes/domain/market_data/synthetic_orderbook.py`,
  `scripts/backtest/run_calibration_full.py`): backtest signals now go through
  `FillKernel`, the fill path `hermes serve` will share (#353): entries through
  the `OrderRouter`, exits as a sale of the held quantity, both filled by
  `OrderbookWalkFillModel`, folded into `PortfolioTracker` and returned as
  `TradeExecuted` v2 events that the startup rebuild can replay. Each signal
  gets a synthetic order book whose touch sits at the pre-registered price
  (§6.2: entries 5 bps over close, chandelier exits 15 bps under the stop),
  with depth from the bar's volume, so M-02 slippage is no longer zero. The
  trial each run logs now carries the Sharpe of the daily equity curve.
  Closes #313.
