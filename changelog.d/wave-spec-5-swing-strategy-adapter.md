# wave-spec-5-swing-strategy-adapter

**Type**: feat
**Tracking issue**: #235
**Branch**: feat/spec-5-swing-strategy-adapter

## What shipped

First real signal-generation layer of the swing-MVP1 experiment.
`SwingStrategyAdapter` implements `StrategyPort` and coordinates five
technical engines against pre-registration v1 §5 binding parameters.

### New modules

- `src/hermes/ports/strategy.py` — `StrategyPort` `@runtime_checkable` Protocol
- `src/hermes/domain/strategy/signal.py` — `StrategySignal` value object
  (`SignalKind`, `SignalSide`, `entry_price`, `stop_price`, `atr_context`,
  `f_star_estimate: None`)
- `src/hermes/domain/strategy/config.py` — `StrategyConfig` dataclass
  (`capital_budget: Money`, `oms_type`, `status`)
- `src/hermes/domain/strategy/swing/adapter.py` — `SwingStrategyAdapter`
  with dual 4h/1D bar buffers, entry/exit state machine
- `src/hermes/domain/strategy/swing/engines/donchian.py` —
  `DonchianEngine.high_break(bars, period=20)` — closed-bar 4h Donchian(20)
- `src/hermes/domain/strategy/swing/engines/chandelier.py` —
  `ChandelierEngine.trailing_stop(bars, period=22, atr_mult=3.0)` — 4h
  Chandelier(22, 3.0) per §5.2
- `src/hermes/domain/strategy/swing/engines/adx.py` —
  `AdxEngine.value(bars, period=14)` — 4h ADX(14) with Wilder smoothing
- `src/hermes/domain/strategy/swing/engines/ema.py` —
  `EmaRegimeFilter.above_ema(bars, period=200)` — 1D EMA(200) regime filter
- `src/hermes/domain/strategy/swing/engines/atr.py` —
  `AtrEngine.value(bars, period=14)` — Wilder ATR helper

### Tests

- `tests/unit/domain/strategy/swing/engines/` — 5 test files, engine-level
  unit tests with hand-computed fixtures (ATR, Donchian, Chandelier, ADX, EMA)
- `tests/unit/domain/strategy/swing/test_property_no_lookahead.py` —
  Hypothesis property tests: no-lookahead invariant, signal invariants,
  determinism, multi-timeframe consistency
- `tests/integration/strategy/swing/test_swing_adapter_integration.py` —
  StrategyPort conformance, 2^3 condition matrix, Chandelier/time-stop/
  regime-break exit scenarios, f_star_estimate=None (OD-4 deferred)

## Deferred

- Running against real market data (Spec 6 calibration harness, #236).
- Kelly f\* estimator — OD-4 deferred until ≥30 OOS trades; signal exposes
  `f_star_estimate: None` as placeholder.
- Alternative strategies / Balancer bucket (MVP2).
- Wiring into OrderRouter (Spec 3).
- Multi-strategy portfolio concurrency (MVP2 per SA-6).
