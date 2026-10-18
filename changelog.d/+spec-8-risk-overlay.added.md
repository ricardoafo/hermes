- **Spec 8 — risk overlay** (`src/hermes/ports/risk_overlay.py`,
  `src/hermes/domain/risk/`): the eight portfolio-shock rules as stateless
  implementations of the new `RiskOverlayPort`, each returning a
  `RiskDecision` (OK, WARN or HALT) from shared `PortfolioState` and
  `MarketSnapshot` inputs: RO-01 correlation lock, RO-02 spread spike, RO-03
  max slippage, RO-04 stale book, RO-05/RO-06 stablecoin depeg warn and halt,
  RO-07 BTC macro drop and RO-08 portfolio drawdown (15%, pre-registration
  §10). Adds `RiskViolationError` to the domain error hierarchy. Closes #228.
