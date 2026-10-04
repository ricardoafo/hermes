- **Shadow fills in `hermes serve`** (`src/hermes/application/execution/shadow.py`,
  `src/hermes/entrypoints/serve.py`,
  `src/hermes/adapters/market_data/binance_depth_snapshot.py`): every live
  signal now goes through the `FillKernel` the backtest uses. A simulated order
  walks a keyless Binance depth snapshot, or the pre-registration §6.2 book
  when the snapshot fails or the bar is a catch-up. The fill is recorded with
  `TradeRecorder` as a `TradeExecuted` v2 trade under the strategy id, with
  EUR values or pending. A `FillQualityRecorded` row stores the book used and
  the drift from the §6.2 price for the SHADOW_CERTIFIED gate. Entries pass
  the risk overlay (RO-03 to RO-08) and the §10 kill evaluator, and are sized
  by the roster's pre-registered sizing policy. A fill is never dated before
  the shadow capital seed, so the seed always pays for it, and each bar's
  portfolio mark is taken after that bar's fill. After a
  restart a re-evaluated bar does not fill twice, and the portfolio is rebuilt
  from the recorded trades. Fill logs and pushed notifications carry no P&L.
  Each release now ships the signed pre-registration and the fee history.
  Closes #353.
