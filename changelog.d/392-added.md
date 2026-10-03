- **Portfolio rebuilt from the ledger on startup** (`src/hermes/entrypoints/serve.py`, `src/hermes/application/portfolio/tracker.py`):
  `hermes serve` replays the strategy's recorded `TradeExecuted` events to restore cash and positions instead of
  restarting from the seed, and refuses to start when the ledger cannot be replayed. `TradeExecuted` now carries
  `strategy_id` (event schema version 2).
