- **Signal event and portfolio state** (`src/hermes/domain/events/signal_emitted.py`,
  `src/hermes/domain/portfolio/`, `src/hermes/application/portfolio/tracker.py`):
  `SignalEmitted` carries a strategy signal onto the event bus and into the
  ledger, with its time taken from the bar. `PortfolioSnapshot` folds fills and
  closed bars into cash, positions and PnL with average-cost accounting, and
  `PortfolioTracker` hands the risk rules their `PortfolioState` from it.
  `config/strategies.yaml` is now the single `swing-donchian-4h` entry with
  `capital_budget` 5000 and `quote` USDT, which seed the tracker. Closes #308.
