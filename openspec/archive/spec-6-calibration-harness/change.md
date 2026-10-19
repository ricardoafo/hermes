# Change: spec-6-calibration-harness

**SDD change**: `spec-6-calibration-harness`
**Date**: 2026-08-25
**Status**: Shipped
**Tracking issue**: see sibling `issue.md` (#240)

Mirror of engram topic key `sdd/spec-6-calibration-harness/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

Delivers the calibration harness that validates `SimulatedExchangeAdapter`
fill accuracy and drives the 6-state certification lifecycle for `swing-mvp1-v1`.
Seven pure-function metric calculators (M-01..M-07) collect per-fill evidence;
`CalibrationHarness` orchestrates a backtest data window and emits a
`CalibrationReport`; `CertificationLifecycle` implements the state machine that
advances adapters through `UNTRUSTED → BACKTEST_CERTIFIED → SHADOW_CERTIFIED →
OOS_CERTIFIED → LIVE_APPROVED → CERTIFIED`, with `DEAD` as the absorbing error
state. M-07 and M-03 latency are MVP1 stubs — real values come in Spec 6 follow-up
integration. This spec ships a complete testing pyramid: ≥15 unit tests for metric
calculators, ≥8 property tests for the state machine transition matrix, and ≥3
integration tests over a fixture data window.

## Scope

### In

- `openspec/archive/spec-6-calibration-harness/change.md` + `issue.md`
- `src/hermes/domain/calibration/metrics.py` — M-01..M-07 pure-function calculators
- `src/hermes/domain/calibration/__init__.py` — package init
- `src/hermes/application/calibration/harness.py` — `CalibrationHarness` orchestrator
- `src/hermes/application/calibration/lifecycle.py` — `CertificationLifecycle` state machine
- `src/hermes/application/calibration/baseline.py` — baseline stub (interface + fixture)
- `src/hermes/application/calibration/__init__.py` — package init
- `tests/unit/application/calibration/` — unit + property test suite
- `tests/unit/application/calibration/__init__.py`
- `tests/integration/calibration/` — integration tests
- `tests/integration/calibration/__init__.py`
- `changelog.d/wave-spec-6-calibration-harness.md` — fragment

### Out

- Actual OOS certification run (2027-01-01 discipline gate per pre-reg §4).
- Live testnet HTTP calls (Spec 6 follow-up integration).
- Statistical framework (DSR, walk-forward, CPCV) — calculators that consume
  metric values are documented in `docs/experiments/swing-mvp1/statistical-framework.md`.
- Weekly / monthly aggregation reports.
- `M-07` queue-position quality implementation (OD-2 — returns `None` for MVP1).
- `M-03` real latency measurement (MVP1 backtest mocked to 0).

## Decisions

### D-1: CalibrationReport is a frozen dataclass, not a Pydantic model

- **Decision**: `CalibrationReport` is a `@dataclass(frozen=True)` carrying
  all 7 metric values plus metadata (strategy_id, window_start, window_end,
  fill_count, state).
- **Alternatives**: (a) Pydantic — adds dep not in domain layer; (b) TypedDict —
  no field-level validation; (c) namedtuple — no docstring per field.
- **Rationale**: Dataclasses are the project idiom for value objects per
  code-conventions.md §1. Frozen ensures immutability of calibration evidence.
- **Cost of reversal**: LOW — convert to Pydantic when the harness grows serialisation
  requirements.

### D-2: M-07 returns None for MVP1 (OD-2 deferral)

- **Decision**: `compute_m07_queue_quality` always returns `None` for MVP1. The
  function signature is present so downstream code can be written against the real
  interface now.
- **Alternatives**: (a) raise NotImplementedError — breaks integration tests; (b)
  return 0 — misleading metric value.
- **Rationale**: OD-2 deferred per scope agreement. `None` is the explicit sentinel
  for "not computed" in the metrics dataclass, matching pre-reg §Calibration SA-5.
- **Cost of reversal**: LOW — implement the real calculator and change the return type.

### D-3: CertificationLifecycle.advance is a pure function

- **Decision**: `advance(current_state, report) -> CertificationState | str` where
  `str` is a human-readable error when the transition is illegal. No side effects,
  no I/O.
- **Alternatives**: (a) stateful object — harder to test; (b) raise on illegal transition
  — callers cannot distinguish infra errors from expected transition errors.
- **Rationale**: Pure functions are easiest to property-test. The state machine
  transition matrix is finite and well-defined; returning `str` on error follows the
  project's discriminated-union pattern for expected outcomes.
- **Cost of reversal**: LOW — wrap in a stateful shell.

### D-4: M-02 fill-price delta bps uses Decimal arithmetic throughout

- **Decision**: All bps calculations use `Decimal` — no float intermediates — to
  preserve the precision guarantees that make the metric admissible as certification
  evidence.
- **Alternatives**: (a) float — fast but lossy for bps-scale differences; (b) numpy —
  adds dep.
- **Rationale**: pre-registration-v1.md §7 designates M-02 as the primary calibration
  metric. Using float would introduce rounding errors at the 4th decimal place —
  unacceptable for the primary metric. Decimal arithmetic is the project standard per
  code-conventions.md §6.
- **Cost of reversal**: LOW — switch to float if performance bottlenecks emerge.

## Requirements

### R-1: CalibrationReport carries all 7 metric values

- **A-1.1**: `CalibrationReport` has fields `m01_fill_count`, `m02_fill_price_delta_bps`,
  `m03_latency_ms`, `m04_rejection_rate`, `m05_orphan_position_count`,
  `m06_regime_returns`, `m07_queue_quality`.
- **A-1.2**: `m07_queue_quality` is typed `Optional[Decimal]` and defaults to `None`.
- **A-1.3**: `m03_latency_ms` is typed `Decimal` and equals `0` for backtest mode.

### R-2: M-02 fill-price delta bps is computed correctly

- **A-2.1**: For a fill at `avg_price` against reference `mid_price`, the bps delta is
  `(avg_price - mid_price) / mid_price * 10000` for buys (positive = slippage),
  sign-flipped for sells.
- **A-2.2**: `compute_m02_fill_price_delta_bps` returns a dataclass with `avg_bps`,
  `p50_bps`, `p95_bps`, `p99_bps` for a sequence of fills.
- **A-2.3**: With a single fill at mid-price, all percentile values are `0`.

### R-3: CertificationLifecycle enforces valid state transitions only

- **A-3.1**: `advance(UNTRUSTED, report)` with `m01_fill_count >= 1` returns
  `BACKTEST_CERTIFIED`.
- **A-3.2**: `advance(CERTIFIED, report)` with kill trigger active raises / returns
  error and transitions to `DEAD` equivalent.
- **A-3.3**: Skipping a state (e.g. `UNTRUSTED → SHADOW_CERTIFIED`) returns an
  error string, not a state.
- **A-3.4**: `advance(DEAD, ...)` always returns an error — `DEAD` is absorbing.

### R-4: CalibrationHarness runs across a fixture window and emits CalibrationReport

- **A-4.1**: `CalibrationHarness.run(fills, reference_prices)` returns a
  `CalibrationReport` with `m01_fill_count == len(fills)`.
- **A-4.2**: Empty `fills` list returns a report with `m01_fill_count == 0` and
  `m04_rejection_rate == 0.0`.

### R-5: M-05 orphan position count enforces SA-4 P0

- **A-5.1**: `compute_m05_orphan_positions` returns 0 when every open position
  has a preceding fill event.
- **A-5.2**: Returns the count of open positions with NO preceding fill event when
  orphans exist.

## Design

### CalibrationState enum

```python
class CertificationState(StrEnum):
    UNTRUSTED = "UNTRUSTED"
    BACKTEST_CERTIFIED = "BACKTEST_CERTIFIED"
    SHADOW_CERTIFIED = "SHADOW_CERTIFIED"
    OOS_CERTIFIED = "OOS_CERTIFIED"
    LIVE_APPROVED = "LIVE_APPROVED"
    CERTIFIED = "CERTIFIED"
    DEAD = "DEAD"
```

### CalibrationReport

```python
@dataclass(frozen=True, slots=True)
class CalibrationReport:
    strategy_id: str
    window_start: datetime
    window_end: datetime
    m01_fill_count: int
    m02_fill_price_delta_bps: FillPriceDeltaBps
    m03_latency_ms: Decimal  # 0 for backtest MVP1
    m04_rejection_rate: Decimal  # 0..1
    m05_orphan_position_count: int
    m06_regime_returns: RegimeReturns
    m07_queue_quality: Decimal | None  # None for MVP1
```

### M-02 FillPriceDeltaBps

```python
@dataclass(frozen=True, slots=True)
class FillPriceDeltaBps:
    avg_bps: Decimal
    p50_bps: Decimal
    p95_bps: Decimal
    p99_bps: Decimal
```

### M-06 RegimeReturns

```python
@dataclass(frozen=True, slots=True)
class RegimeReturns:
    bull_quiet: Decimal | None
    bull_vol: Decimal | None
    bear_quiet: Decimal | None
    bear_vol: Decimal | None
```

### CertificationLifecycle transitions

| From              | Condition                          | To                  |
|-------------------|------------------------------------|---------------------|
| UNTRUSTED         | m01 >= 1 (any fill)                | BACKTEST_CERTIFIED  |
| BACKTEST_CERTIFIED| operator advances (shadow started) | SHADOW_CERTIFIED    |
| SHADOW_CERTIFIED  | operator advances (OOS started)    | OOS_CERTIFIED       |
| OOS_CERTIFIED     | operator manual gate               | LIVE_APPROVED       |
| LIVE_APPROVED     | 90+ days live, metrics within tol  | CERTIFIED           |
| any               | kill trigger fires                 | DEAD                |
| DEAD              | any                                | error (absorbing)   |

## Test plan

1. All acceptance criteria above pass.
2. `uv run pytest tests/unit/application/calibration/ tests/integration/calibration/ -v` — all green.
3. `bash scripts/dev/pr-preflight.sh` — all checks green.
4. Metric unit tests cover hand-computed fixtures with known bps values.
5. Property tests cover every (state, condition) pair in the transition matrix.

## Risks

- **Risk 1**: Regime detection (M-06) requires tagging fills by regime. The MVP1
  implementation uses a simple placeholder that returns `None` for all regime
  buckets when no regime data is provided. Mitigation: interface is defined now so
  Spec 6 follow-up can inject real regime tags without changing signatures.
- **Risk 2**: Percentile computation (M-02 p95/p99) with fewer than 100 fills may
  produce identical values for consecutive percentiles due to small sample. Mitigation:
  documented in docstring; property tests use `min_value=1` fill to catch edge cases.

## Dependencies on other changes

- Spec 2a (FillModelPort) — merged to main ✅
- Spec 2b (OrderbookWalkFillModel) — landed separately
- Spec 9 (PreRegistration, KillTriggerEvaluator) — merged to main ✅
