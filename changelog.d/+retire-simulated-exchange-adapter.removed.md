- **SimulatedExchangeAdapter** (`src/hermes/adapters/exchanges/simulated.py`)
  is removed: no mode built it, since backtest and serve fill through
  `FillKernel` (#353). See
  `docs/decisions/2026-10-18-retire-simulated-exchange-adapter.md`. Part of
  #1181.
