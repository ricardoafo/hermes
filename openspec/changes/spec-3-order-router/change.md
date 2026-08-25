# Change: spec-3-order-router

**SDD change**: `spec-3-order-router`
**Date**: 2026-08-25
**Status**: Applied
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/spec-3-order-router/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

Implements the application-layer `OrderRouter` that composes the T-1
position-sizing pipeline (ATR-baseline → Kelly cap → 5% hard cap per
pre-reg §5.4), gates every entry through the Spec 8 `RiskOverlayPort`
(HALT → reject, WARN → log + proceed), and surfaces Spec 9
`KillTriggerEvaluator` results to the caller as a `KillCircuitBreaker`
outcome. The main architectural pick is a pure orchestration layer:
`OrderRouter` holds no mutable state — all state flows through immutable
inputs (`SizingInput`, `PortfolioState`, `MarketSnapshot`). The main risk
is threshold drift — the three sizing caps (ATR-baseline, Kelly, 5% hard
cap) come from pre-reg §5.4 and §5.5 and are enforced by property tests
so any future edit is deliberate.

## Scope

### In

- `openspec/changes/spec-3-order-router/change.md` — this spec.
- `openspec/changes/spec-3-order-router/issue.md` — tracking issue #234.
- `src/hermes/application/order_router/__init__.py` — package init + public re-exports.
- `src/hermes/application/order_router/router.py` — `OrderRouter` orchestrator.
- `src/hermes/application/order_router/risk_engine.py` — `RiskEngine` with
  per-strategy `capital_budget` enforcement (SA-6).
- `src/hermes/application/order_router/sizing.py` — `SizingCalculator` for T-1
  composition: ATR-baseline × Kelly cap × 5% hard cap `min()`.
- Tests in `tests/unit/application/order_router/`:
  - `test_sizing.py` — property test: sized notional never exceeds any of the
    three caps (Hypothesis, ≥ 50 examples).
  - `test_risk_engine.py` — matrix test: HALT/WARN/OK overlay outcomes + correlation
    lock refusal (RO-01 via CorrelationLock).
  - `test_router.py` — router integration: orphan-position invariant (SA-4 P0),
    kill circuit-breaker surfacing, end-to-end HALT rejection.
- `changelog.d/wave-spec-3-order-router.md` — changelog fragment.

### Out

- Actually executing orders (live exchange adapter — Spec 5).
- Fractional Kelly f\* estimator (OD-4: activates only after ≥ 30 OOS trades; MVP1
  uses 5% floor).
- Multi-strategy portfolio balancing (SA-6 MVP2).

## Decisions

### D-1: Pure stateless orchestration in OrderRouter

- **Decision**: `OrderRouter` holds no mutable state; all inputs are immutable
  dataclasses injected at call-time.
- **Alternatives**: Stateful router caching portfolio state (rejected — violates
  domain purity and makes the router untestable without time-travel); event-sourced
  router (rejected — overkill for MVP1 scope).
- **Rationale**: Stateless orchestration is trivially testable, trivially
  composable, and aligns with the Ports & Adapters pattern
  (application-imports-only-domain-and-ports import-linter contract). All mutable
  state lives in the persistence layer (Spec 6, ledger).
- **Cost of reversal**: LOW — add state later by injecting a repository port.

### D-2: Three-cap min() composition for T-1 sizing

- **Decision**: Compute `notional = min(atr_notional, kelly_notional, hard_cap_notional)`
  where each cap is computed independently, then divide by entry price.
- **Alternatives**: Apply caps sequentially (rejected — order-sensitive, harder to
  audit); apply only one cap (rejected — violates pre-reg §5.4 binding constraint).
- **Rationale**: The `min()` of three independent caps is idempotent, order-insensitive,
  and trivially expressible as a property: `sized ≤ cap_i ∀ i`. Pre-reg §5.4 requires
  5% hard cap to supersede any other sizing rule — the `min()` shape enforces this
  automatically.
- **Cost of reversal**: LOW — formula change only in `sizing.py`.

### D-3: RiskOverlayPort as a list of rules evaluated left-to-right, worst wins

- **Decision**: `RiskEngine.evaluate()` accepts a `list[RiskOverlayPort]` and
  takes `max(decisions, key=severity)` — HALT > WARN > OK.
- **Alternatives**: Single composite rule (rejected — hides individual rule reasons);
  short-circuit on first HALT (rejected — need to log all WARN decisions even when
  HATLed).
- **Rationale**: Matches the Spec 8 composition pattern. Each rule is tested in
  isolation (Spec 8 tests). The router only needs to compose them.
- **Cost of reversal**: LOW — rule list is a constructor argument.

### D-4: KillCircuitBreaker as a named outcome type, not an exception

- **Decision**: `OrderRouterResult` is a discriminated union:
  `Filled | RiskRejected | KillCircuitBreaker`. `KillCircuitBreaker` carries a
  `KillDecision` and is returned, not raised.
- **Alternatives**: Raise `KillTriggerError` (rejected — kill is an expected business
  outcome per pre-reg §10, not an exceptional failure); return `None` (rejected —
  untyped).
- **Rationale**: Aligns with code-conventions §2 (hybrid Result/exceptions — expected
  outcomes return, unexpected failures raise). The caller can pattern-match on the union.
- **Cost of reversal**: MEDIUM — callers must update their pattern-match.

## Requirements

### R-1: Sizing caps are never violated

- **A-1.1**: For any valid `SizingInput` with `equity > 0`, `atr > 0`,
  `entry_px > 0`, `stop_atr_mult > 0`: `sized_notional ≤ 0.05 * equity` (5% hard cap).
- **A-1.2**: For any valid `SizingInput`: `sized_notional ≤ atr_baseline_notional`.
- **A-1.3**: For any valid `SizingInput`: `sized_notional ≤ kelly_notional`.
- **A-1.4**: Property test covers ≥ 50 Hypothesis examples.

### R-2: Risk overlay HALT blocks dispatch

- **A-2.1**: When any rule in the overlay list returns `RiskAction.HALT`, the router
  returns `RiskRejected` and does NOT call `FillModelPort.fill_order`.
- **A-2.2**: When overlay returns `RiskAction.WARN`, the router logs the reason and
  proceeds to dispatch.
- **A-2.3**: When overlay returns `RiskAction.OK`, the router proceeds normally.

### R-3: Correlation lock (RO-01) refusal

- **A-3.1**: When `PortfolioState.correlations` contains a pair with the candidate
  pair where the coefficient ≥ 0.80, the router returns `RiskRejected`.

### R-4: Kill circuit breaker surfaces to caller

- **A-4.1**: When `KillTriggerEvaluator.evaluate()` returns `KillDecision(triggered=True)`,
  the router returns `KillCircuitBreaker` before attempting sizing or dispatch.

### R-5: Orphan-position invariant (SA-4 P0)

- **A-5.1**: The router never dispatches an order when the `open_position_count`
  in `PortfolioState` equals or exceeds `max_concurrent_positions` (3 per pre-reg).

## Design

### File tree

```text
src/hermes/application/order_router/
    __init__.py           # re-exports: OrderRouter, RiskEngine, SizingCalculator,
                          #             SizingInput, OrderRouterResult, RiskRejected,
                          #             KillCircuitBreaker
    router.py             # OrderRouter
    risk_engine.py        # RiskEngine
    sizing.py             # SizingCalculator, SizingInput

tests/unit/application/order_router/
    __init__.py
    test_sizing.py
    test_risk_engine.py
    test_router.py
```

### Key types

```python
# sizing.py
@dataclass(frozen=True, slots=True)
class SizingInput:
    equity: Decimal  # total portfolio equity (quote currency)
    entry_px: Decimal  # entry price (quote per base unit)
    atr: Decimal  # ATR value (quote currency)
    stop_atr_mult: Decimal  # ATR multiplier for stop (pre-reg §5.4 default 1.5)
    risk_pct: Decimal  # risk per trade fraction (pre-reg §5.4 = 0.01)
    f_star_estimate: Decimal  # Kelly fraction estimate (MVP1 = 0.20, giving 5% floor)


# T-1 sizing formula:
#   atr_notional   = (equity * risk_pct) / stop_atr_mult   [ATR-baseline]
#   kelly_notional = equity * min(0.25 * f_star_estimate, 0.05)  [Kelly cap, 5% floor]
#   hard_cap       = 0.05 * equity                          [§5.4 absolute cap]
#   notional       = min(atr_notional, kelly_notional, hard_cap)
#   qty            = notional / entry_px


# router.py
@dataclass(frozen=True, slots=True)
class RiskRejected:
    reason: str
    rule: str


@dataclass(frozen=True, slots=True)
class KillCircuitBreaker:
    decision: KillDecision


# OrderRouterResult = Fill | RiskRejected | KillCircuitBreaker
```

### OrderRouter.route() flow

```text
signal arrives
  → KillTriggerEvaluator.evaluate(experiment_state)
      → triggered=True  → return KillCircuitBreaker
  → open_position_count >= max_concurrent → return RiskRejected("orphan-position cap")
  → RiskEngine.evaluate(rules, portfolio_state, market_snapshot)
      → HALT → return RiskRejected
      → WARN → log, continue
  → SizingCalculator.compute(sizing_input)
  → build Order
  → FillModelPort.fill_order(order, orderbook, clock, fees)
  → return Fill | RiskRejected (from Rejection)
```

## Test plan

1. Every acceptance criterion above passes.
2. `bash scripts/dev/pr-preflight.sh` → all checks green.
3. All CI jobs green on the PR.
4. Human review by `ricardoafo`.

## Risks

- **Threshold drift**: sizing constants (1% risk, 5% hard cap, 0.80 correlation,
  3 concurrent max) copied from pre-reg. Mitigated by property tests that verify
  the formula at test time, not at write time.
- **Kelly floor degeneracy**: MVP1 sets `f_star_estimate = 0.20`, giving
  `kelly_notional = equity * 0.05` which equals the hard cap — effectively the
  hard cap always wins. This is intentional (OD-4 defers live Kelly estimation).
  The formula is correct for post-OD-4 values of `f_star_estimate`.
- **RiskOverlayPort list ordering**: worst-wins composition is order-insensitive
  for the final action. However, when multiple WARN rules fire, only the worst
  WARN reason is surfaced. Mitigated by logging all individual rule decisions.

## Dependencies on other changes

- Spec 2a fill-model-port (#199 — merged) — `FillModelPort`, `Order`, `Fill`, `Rejection`.
- Spec 8 risk-overlay (#228 — merged) — `RiskOverlayPort`, `RiskDecision`, `RiskAction`,
  `PortfolioState`, `MarketSnapshot`, `CorrelationLock`.
- Spec 9 strategy-development-protocol (#226 — merged) — `KillTriggerEvaluator`,
  `ExperimentState`, `KillDecision`.
- Spec 1 domain-aggregates — `StrategyId`, `Money`, `Lot`.
