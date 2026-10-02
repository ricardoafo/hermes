- **Calibration no longer reads daily bars before they close**
  (`src/hermes/domain/strategy/swing/adapter.py`,
  `scripts/backtest/run_calibration.py`): the calibration driver merges the
  4h and 1D streams by close time, and the swing adapter's EMA regime filter
  ignores any 1D bar that has not closed by the 4h bar it evaluates, so a
  caller ordering bars by open time can no longer leak up to 16 hours of
  future prices into entries and exits. Closes #311.
