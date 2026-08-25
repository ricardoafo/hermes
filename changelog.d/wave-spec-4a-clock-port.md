### Added

- **spec-4a ClockPort + WallClock + SimulatedClock**
  (`src/hermes/ports/clock.py`, `src/hermes/adapters/clock/wall.py`,
  `src/hermes/adapters/clock/simulated.py`,
  `src/hermes/adapters/clock/__init__.py`,
  `openspec/changes/spec-4a-clock-port/`): first hexagonal port
  introduced behind the signed paper-trading pre-registration v1
  (`docs/experiments/swing-mvp1/pre-registration-v1.md`, PR #204).
  `ClockPort` is a `@runtime_checkable` Protocol with `now() -> datetime`
  (tz-aware UTC only). `WallClock` wraps `now_utc()` for production;
  `SimulatedClock` provides `now()`, `advance(delta)`, and `set(t)`
  with a strict monotonicity guard (backwards jumps rejected — SA-8
  replay-determinism requirement) and thread safety via an internal
  lock. Populates the previously-empty
  `src/hermes/adapters/clock/__init__.py` (0 bytes before this
  change), a foundational blocker per
  `docs/research/paper-trading/spec-readiness.md`. 43 tests: 26
  `SimulatedClock`, 8 `WallClock`, 9 structural-conformance. Follow-up
  (out of scope): migrate existing `datetime.now()` call sites through
  the new port — count reported in the SDD change document.
