# wave-spec-8-risk-overlay

**Type**: feat
**Tracking issue**: #228
**Branch**: feat/spec-8-risk-overlay

## What shipped

Wires SA-9's 8 portfolio-shock rules as pluggable implementations of a new
`RiskOverlayPort` `@runtime_checkable` Protocol. Each rule is an isolated,
stateless domain module that accepts shared `PortfolioState` + `MarketSnapshot`
inputs and returns a `RiskDecision(action, reason)`.

### New modules

- `src/hermes/ports/risk_overlay.py` — `RiskOverlayPort` Protocol,
  `RiskAction` enum (OK / WARN / HALT), `RiskDecision` dataclass.
- `src/hermes/domain/risk/state.py` — `PortfolioState` and `MarketSnapshot`
  shared domain types.
- `src/hermes/domain/risk/correlation_lock.py` — RO-01: 30-day rolling
  correlation ≥ 0.80 → HALT.
- `src/hermes/domain/risk/spread_spike.py` — RO-02: spread ≥ 10× 1H median → HALT.
- `src/hermes/domain/risk/max_slippage.py` — RO-03: single > 0.5% or
  cumulative > 2% slippage → HALT.
- `src/hermes/domain/risk/stale_book.py` — RO-04: orderbook age > 2s → HALT.
- `src/hermes/domain/risk/depeg_warn.py` — RO-05: stablecoin deviation ≥ 1.5% → WARN.
- `src/hermes/domain/risk/depeg_halt.py` — RO-06: stablecoin deviation ≥ 3.0% → HALT.
- `src/hermes/domain/risk/macro_atr_halt.py` — RO-07: BTC 1D log-return < -10% → HALT.
- `src/hermes/domain/risk/portfolio_drawdown.py` — RO-08: portfolio DD ≥ 15%
  (pre-reg §10) → HALT + surfaces to KillTriggerEvaluator (Spec 9, #227).

### Modified

- `src/hermes/domain/errors.py` — adds `RiskViolationError(DomainError)` to the
  error hierarchy.

### Tests

- `tests/unit/domain/risk/` — 9 test files: one property-test file per rule
  (threshold matrix, boundary checks, Protocol conformance) + one composition
  test file (worst-outcome-wins, `RiskAction` severity ordering).

## Deferred

- Wiring rules into `OrderRouter` (Spec 3).
- Live L2 collector for peg/spread signals (Spec 4c).
- Portfolio-state calculators (Spec 3).
- Kill-decision escalation loop (Spec 9 `KillTriggerEvaluator` already landed in #227).
