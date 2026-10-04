- **USDT as its own FIFO asset** (`src/hermes/domain/lots/quote_leg.py`,
  `src/hermes/adapters/ledger/sqlite_ledger.py`, migration `0006_capital_seeds.sql`):
  `hermes serve` records the shadow start capital once as a `ShadowCapitalSeeded` event and one
  USDT lot, dated by the ledger's earliest bar, signal or trade. From then on every BTC/USDT BUY
  disposes of USDT and every SELL acquires it through the same EUR FIFO replay, so EUR/USD drift
  on held USDT becomes a realized gain or loss. A BUY fee charged in the base asset now reduces
  the lot quantity and is priced in EUR. `hermes fx backfill` prices the pending seed by its seed
  date. Strategy, sizing, signals and fills are unchanged. Closes #405.
