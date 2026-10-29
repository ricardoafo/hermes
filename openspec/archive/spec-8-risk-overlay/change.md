# Change: spec-8-risk-overlay

**SDD change**: `spec-8-risk-overlay`
**Date**: 2026-08-25
**Status**: Shipped
**Tracking issue**: see sibling `issue.md` (#228)

Mirror of engram topic key `sdd/spec-8-risk-overlay/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

Wires SA-9's 8 concrete portfolio-shock rules as pluggable implementations
of a new `RiskOverlayPort` Protocol. Each rule is an isolated module in
`src/hermes/domain/risk/` and accepts shared `PortfolioState` +
`MarketSnapshot` inputs, returning a `RiskDecision(action, reason)`.
The main architectural pick is a composable, flat rule registry: callers
iterate over all rules and take the worst outcome (HALT > WARN > OK),
making the overlay trivially extensible. The main risk is threshold
drift — constants come from pre-reg §5.5 and are enforced by tests so
any future edit is deliberate.

## Scope

### In

- `openspec/archive/spec-8-risk-overlay/change.md` + `issue.md` (this
  change).
- `src/hermes/ports/risk_overlay.py` — new `RiskOverlayPort`
  `@runtime_checkable` Protocol; method `evaluate(portfolio_state,
  market_snapshot) -> RiskDecision`. `RiskDecision` dataclass with
  `action: RiskAction` (Halt | Warn | Ok) and `reason: str`.
- `src/hermes/domain/risk/state.py` — shared `PortfolioState` and
  `MarketSnapshot` domain types consumed by all 8 rules.
- `src/hermes/domain/risk/__init__.py` — package init re-exporting
  public symbols.
- `src/hermes/domain/risk/correlation_lock.py` — RO-01: 30-day rolling
  correlation ≥ 0.80 → block new entry in correlated pair.
- `src/hermes/domain/risk/spread_spike.py` — RO-02: bid-ask spread ≥
  10× 1H median → HALT entries.
- `src/hermes/domain/risk/max_slippage.py` — RO-03: realized slippage
  > 0.5% single / 2% cumulative → HALT.
- `src/hermes/domain/risk/stale_book.py` — RO-04: orderbook age > 2s →
  HALT.
- `src/hermes/domain/risk/depeg_warn.py` — RO-05: stablecoin deviation
  ≥ 1.5% from peg → WARN.
- `src/hermes/domain/risk/depeg_halt.py` — RO-06: stablecoin deviation
  ≥ 3% from peg → HALT.
- `src/hermes/domain/risk/macro_atr_halt.py` — RO-07: BTC 1D
  log-return < -10% → HALT all new entries 24h.
- `src/hermes/domain/risk/portfolio_drawdown.py` — RO-08: portfolio DD
  ≥ pre-reg §10 threshold (15%) → HALT + surface to KillTriggerEvaluator
  (Spec 9).
- `src/hermes/domain/errors.py` — add `RiskViolationError` to the
  domain error hierarchy.
- `tests/unit/domain/risk/` — property test per rule (threshold matrix),
  composition test (multiple rules → worst outcome wins).
- `changelog.d/wave-spec-8-risk-overlay.md` — changelog fragment.

### Out

- Wiring rules into `OrderRouter` (Spec 3, not yet landed).
- Live L2 collector for peg/spread signals (Spec 4c).
- Portfolio-state calculators (Spec 3 will compute).
- Kill-decision escalation loop (Spec 9's `KillTriggerEvaluator` —
  already landed in #227).

## Decisions

### D-1: Flat rule registry with worst-outcome composition

- **Decision**: Rules are pure functions (classes implementing
  `RiskOverlayPort`) composed by iterating all and taking the highest
  severity outcome (HALT > WARN > OK).
- **Alternatives**: (a) rule chains with short-circuit on first HALT —
  rejected because it hides downstream violations from logging; (b)
  weighted scoring — rejected as too implicit for a pre-reg artefact.
- **Rationale**: Explicit iteration means every rule fires and its
  result is observable; worst-wins is deterministic and trivially
  testable.
- **Cost of reversal**: LOW — swap composition function without touching
  individual rule modules.

### D-2: Thresholds as module-level constants, not config

- **Decision**: Pre-reg §5.5 constants (0.80 correlation, 10× spread,
  0.5%/2% slippage, 2s stale, 1.5%/3% depeg, -10% macro log-return,
  15% DD) live as typed module constants, not in any config collection.
- **Alternatives**: DB-backed config (rejected — pre-reg signing forbids
  live overrides); environment variables (rejected — no audit trail).
- **Rationale**: Changes to a signed pre-reg document must be intentional
  and traceable; freezing them in code means every change goes through
  code review.
- **Cost of reversal**: MEDIUM — must plumb a config read into 8 rule
  modules.

## Requirements

### R-1: Port protocol is `@runtime_checkable`

- **A-1.1**: `python -c "from hermes.ports.risk_overlay import RiskOverlayPort; assert hasattr(RiskOverlayPort, '__protocol_attrs__')"` exits 0.
- **A-1.2**: Each rule class satisfies `isinstance(rule, RiskOverlayPort)` at runtime.

### R-2: Each rule fires correctly at boundary values

- **A-2.1**: `pytest tests/unit/domain/risk/ -q` → all 8 rule tests pass.
- **A-2.2**: Property tests cover at-threshold and beyond-threshold inputs for every constant.

### R-3: Composition returns worst outcome

- **A-3.1**: When one rule returns HALT and another returns WARN, the composed result is HALT.
- **A-3.2**: When all rules return OK, the composed result is OK.

### R-4: `RiskViolationError` is in the domain error hierarchy

- **A-4.1**: `isinstance(RiskViolationError("msg"), DomainError)` is `True`.

## Design

```text
src/hermes/
  ports/
    risk_overlay.py          # RiskOverlayPort Protocol + RiskAction + RiskDecision
  domain/
    errors.py                # +RiskViolationError
    risk/
      __init__.py            # re-exports
      state.py               # PortfolioState, MarketSnapshot
      correlation_lock.py    # RO-01
      spread_spike.py        # RO-02
      max_slippage.py        # RO-03
      stale_book.py          # RO-04
      depeg_warn.py          # RO-05
      depeg_halt.py          # RO-06
      macro_atr_halt.py      # RO-07
      portfolio_drawdown.py  # RO-08
tests/unit/domain/risk/
  __init__.py
  test_correlation_lock.py
  test_spread_spike.py
  test_max_slippage.py
  test_stale_book.py
  test_depeg_warn.py
  test_depeg_halt.py
  test_macro_atr_halt.py
  test_portfolio_drawdown.py
  test_composition.py
```

Key interfaces:

```python
class RiskAction(enum.Enum):
    OK = "ok"
    WARN = "warn"
    HALT = "halt"


@dataclass(frozen=True, slots=True)
class RiskDecision:
    action: RiskAction
    reason: str


@runtime_checkable
class RiskOverlayPort(Protocol):
    def evaluate(
        self,
        portfolio_state: PortfolioState,
        market_snapshot: MarketSnapshot,
    ) -> RiskDecision: ...
```

## Test plan

1. Every acceptance criterion above passes.
2. `bash scripts/dev/pr-preflight.sh` → all checks green.
3. All CI jobs green on the feature PR.
4. Code-owner review by `ricardoafo`.

## Risks

- **Threshold drift**: Pre-reg constants copied to code could diverge
  from the signed document. Mitigation: tests assert exact constant values
  so any edit is caught in CI.
- **Stale `MarketSnapshot` in production**: Rules like RO-04 check
  orderbook age, but the snapshot itself carries an age field computed
  upstream — if the feed freezes, age won't advance. Mitigation: deferred
  to Spec 4c (live L2 collector); for now, the rule is correct given a
  well-formed snapshot.
- **Correlation matrix size**: RO-01 uses a dict of pair→correlation
  floats; at 500+ pairs this could be memory-heavy. Mitigation: deferred
  to Spec 3 (portfolio-state calculators) which will bound the matrix.

## Dependencies on other changes

- Spec 9 KillTriggerEvaluator (#227) — already landed; RO-08 surfaces to
  it via a flag in `PortfolioState.dd_halt_required`.
- Spec 3 OrderRouter (future) — will call `RiskOverlayPort.evaluate`
  before placing orders; that wiring is explicitly out of scope here.
