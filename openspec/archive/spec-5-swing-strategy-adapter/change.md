# Change: spec-5-swing-strategy-adapter

**SDD change**: `spec-5-swing-strategy-adapter`
**Date**: 2026-08-25
**Status**: Shipped
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/spec-5-swing-strategy-adapter/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

Implements the first real signal-generation layer of the swing-MVP1 experiment:
`SwingStrategyAdapter` coordinating five technical engines — Donchian channel
breakout, ADX trend filter, EMA regime filter, Chandelier trailing stop, and ATR
— against pre-registration v1 §5 parameters. The adapter implements `StrategyPort`
and is the scientific subject of the experiment; every other spec exists to
run/evaluate/safeguard this one. Multi-timeframe coordination (4h bars for
Donchian/ADX/Chandelier, 1D bars for EMA regime) is resolved via explicit
`on_bar` dispatch: callers feed both timeframes and the adapter routes internally.
The main risk is lookahead bias; property-test coverage enforces the no-future-data
invariant formally.

## Scope

### In

- `openspec/archive/spec-5-swing-strategy-adapter/change.md` + `issue.md`
- `src/hermes/ports/strategy.py` — `StrategyPort` Protocol
- `src/hermes/domain/strategy/swing/adapter.py` — `SwingStrategyAdapter`
- `src/hermes/domain/strategy/swing/engines/donchian.py` — `DonchianEngine`
- `src/hermes/domain/strategy/swing/engines/chandelier.py` — `ChandelierEngine`
- `src/hermes/domain/strategy/swing/engines/adx.py` — `AdxEngine`
- `src/hermes/domain/strategy/swing/engines/ema.py` — `EmaRegimeFilter`
- `src/hermes/domain/strategy/swing/engines/atr.py` — `AtrEngine`
- `src/hermes/domain/strategy/config.py` — `StrategyConfig` dataclass
- `src/hermes/domain/strategy/signal.py` — `StrategySignal` value object
- `changelog.d/wave-spec-5-swing-strategy-adapter.md`
- Unit tests ≥50, property tests ≥10, integration tests ≥5

### Out

- Running against real data (Spec 6 calibration harness)
- Alternative strategies (Balancer bucket = MVP2)
- Kelly f* estimator (OD-4 defers until ≥30 OOS trades)
- Multi-strategy portfolio (MVP2 per SA-6)
- Wiring into OrderRouter (Spec 3)

## Decisions

### D-1: Multi-timeframe closed-bar coordination via dual-buffer in adapter

- **Decision**: `SwingStrategyAdapter` maintains two separate bar buffers —
  `_bars_4h` and `_bars_1d`. `on_bar` dispatches by `bar.interval`, updating
  the appropriate buffer before evaluating signals. Signal evaluation only
  fires on 4h bar arrival (the primary cadence); 1D bar arrival updates the
  EMA buffer but never triggers a signal directly.
- **Alternatives**: (a) single mixed buffer with interval tagging — rejected
  because engines have incompatible lookback periods and mixing causes off-by-one
  bugs; (b) caller pre-computes EMA and passes as argument — rejected because it
  breaks the adapter's self-contained design and leaks engine internals.
- **Rationale**: Explicit dual-buffer keeps each engine's lookback semantics
  clean. Closed-bar rule (SA-4 P1) is enforced: both buffers only contain bars
  that have fully closed before the signal bar's close_time.
- **Cost of reversal**: LOW — buffer split is internal to the adapter.

### D-2: StrategySignal exposes f_star_estimate as Optional[Decimal] = None

- **Decision**: `f_star_estimate` field is present but always `None` until OD-4
  threshold (≥30 OOS trades) is reached. The router reads it and ignores `None`.
- **Alternatives**: Omit field entirely — rejected because it forces an interface
  change later; use a sentinel `Decimal("-1")` — rejected as non-obvious.
- **Rationale**: Future-proofing with zero runtime cost. Option is cheap.
- **Cost of reversal**: LOW — adding a field later is backwards-compatible.

### D-3: Entry signals fire on 4h bar close; 1D EMA uses last closed 1D bar

- **Decision**: The 1D EMA filter uses whichever 1D bar closed most recently
  before the current 4h bar. Partial 1D bars are never peeked at.
- **Alternatives**: Interpolate intra-day — rejected (lookahead).
- **Rationale**: Enforces SA-4 P1 (closed-bar only). Consistent with pre-reg §5.1.
- **Cost of reversal**: LOW — parameter change in adapter.

### D-4: Chandelier stop = highest_high(22) − 3 × ATR(22, 4h)

- **Decision**: Both the lookback window and the ATR period use 22 bars as
  specified in pre-reg §5.2.
- **Alternatives**: Separate window lengths for high/ATR — rejected; pre-reg
  binds them together.
- **Rationale**: Pre-registration binding. Cost of deviation = experiment
  invalidated.
- **Cost of reversal**: HIGH — changing this invalidates pre-registered
  experiment.

## Requirements

### R-1: StrategyPort Protocol exists and is runtime-checkable

- **A-1.1**: `isinstance(SwingStrategyAdapter(...), StrategyPort)` returns True

### R-2: Donchian breakout fires on correct bar

- **A-2.1**: `DonchianEngine.high_break(bars, period=20)` returns True when
  last bar's close > max(high) of prior 20 bars
- **A-2.2**: Returns False with fewer than 20 prior bars (not enough history)

### R-3: ADX(14) ≥ 25 filter required for entry

- **A-3.1**: No signal emitted when ADX < 25, even with Donchian + EMA True
- **A-3.2**: `AdxEngine.value()` returns None with fewer than 15 bars

### R-4: EMA(200) 1D regime filter

- **A-4.1**: No signal when 1D close < EMA(200), even with Donchian + ADX True
- **A-4.2**: Uses closed 1D bars only

### R-5: All three conditions simultaneously true → entry signal

- **A-5.1**: 2^3=8 matrix; only (True,True,True) emits signal

### R-6: Exit conditions implemented

- **A-6.1**: Chandelier stop triggers exit signal
- **A-6.2**: 90-day time stop triggers exit signal
- **A-6.3**: Regime break (1D close < EMA200) triggers exit signal

### R-7: Signal invariants

- **A-7.1**: stop_price < entry_price for all entry signals
- **A-7.2**: atr_context > 0 for all signals
- **A-7.3**: entry_price = bar close of signal bar

### R-8: No lookahead bias (property-tested)

- **A-8.1**: on_bar(bar_T) output identical whether bars[T+1..T+N] exist or not

## Test plan

- Unit tests per engine: DonchianEngine, ChandelierEngine, AdxEngine,
  EmaRegimeFilter, AtrEngine — deterministic fixtures, boundary conditions
- Property tests: no-lookahead invariant, signal invariants, determinism,
  multi-timeframe closed-bar consistency
- Integration tests: regime filter blocks, 2^3 matrix, chandelier exit,
  time-stop, regime-break exit

## Risks

- Lookahead bias in engine implementations — mitigated by property tests
- ATR/Wilder smoothing off-by-one — mitigated by hand-computed fixture tests
- Pre-reg deviation — mitigated by citing §5.x in every parameter
