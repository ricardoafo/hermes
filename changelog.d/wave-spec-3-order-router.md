- **Spec 3 — Order Router** (`src/hermes/application/order_router/`):
  application-layer orchestrator that composes the full T-1 position-sizing
  pipeline and gates every entry through the Spec 8 risk overlay and Spec 9 kill
  triggers.
  (1) `SizingCalculator` — implements the three-cap `min()` formula from
  pre-reg §5.4: ATR-baseline notional `(equity × risk_pct) / stop_atr_mult`,
  Kelly-cap notional `equity × min(0.25 × f_star, 0.05)`, and a 5% hard cap
  `0.05 × equity`; returns `qty = min(atr, kelly, hard_cap) / entry_px`.
  MVP1 sets `f_star_estimate = 0.20` (OD-4 Kelly floor), making kelly =
  hard_cap so the 5% cap always binds.
  (2) `RiskEngine` — composes a list of `RiskOverlayPort` implementations
  (worst-wins: HALT > WARN > OK); preserves all individual rule decisions for
  audit logging; enforces per-strategy capital budgets (SA-6 `capital_budget`
  dict).
  (3) `OrderRouter` — stateless orchestrator wiring the full pipeline: kill
  circuit breaker (Spec 9 §10) → SA-4 orphan-position guard (max 3 concurrent
  per pre-reg) → risk overlay (HALT → `RiskRejected`, WARN → log + proceed) →
  `SizingCalculator` → `FillModelPort.fill_order`. Returns the discriminated
  union `Fill | RiskRejected | KillCircuitBreaker` — all are expected business
  outcomes, not exceptions.
  Correlation lock (RO-01 via `CorrelationLock`) wired through `RiskEngine`
  refuses new entries when 30-day rolling Pearson ≥ 0.80.
  Full test suite: 3 modules, property tests via Hypothesis for the three-cap
  sizing invariants (≥ 100 examples per property), matrix tests for HALT/WARN/OK
  overlay composition, and router integration tests covering kill, orphan-position,
  and fill-dispatch paths.
  Closes #234.
