### Added

- **SimulatedExchangeAdapter** (`src/hermes/adapters/exchanges/simulated.py`,
  closes #238): composition-root-swappable `ExchangePort` that wires
  `FillModelPort` (Spec 2a/2b), a `Orderbook`-returning callable, `ClockPort`
  (Spec 4a), and a `FeeSchedule`-returning callable into a single concrete
  exchange adapter serving both backtest and shadow modes without any
  `if mode:` branching. Local state: in-memory balance ledger initialised
  from a `dict[Currency, Decimal]` at construction, and a `lot_ledger:
  list[Lot]` that records every BUY fill as a `Lot.open()` aggregate (Spec 1).
  All limit orders are IOC (immediate-or-cancel); GTC deferred to MVP2.
  The adapter accepts an optional `random.Random` for deterministic replay
  under seeded property tests (M-07 queue-position modeling hook). Tested via
  25 unit tests, 5 property tests (Hypothesis), and 4 integration tests
  verifying round-trips, multi-pair independence, and clock-advancing semantics.
