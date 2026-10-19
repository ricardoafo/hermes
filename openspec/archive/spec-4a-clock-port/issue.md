# Issue: spec-4a-clock-port

**Type**: feat
**Status**: In Progress
**Branch**: `sdd/spec-4a-clock-port`

## Summary

Introduce `ClockPort` — the time-abstraction `Protocol` that makes domain
logic replayable — plus `WallClock` (production) and `SimulatedClock`
(testing/backtest) concrete adapters.  This is the foundational prerequisite
for backtest determinism and shadow-mode replay (SA-8 §4.1 S-01) and a
G2-gate requirement per success-criteria.md §1.3.  Wiring existing
`datetime.now(UTC)` call sites to inject `ClockPort` is deferred to a
follow-up migration PR.

## Acceptance

See `change.md` §Requirements (A-1.1 through A-7.1).

## References

- `openspec/archive/spec-4a-clock-port/change.md`
- `docs/research/paper-trading/sa8-reproducibility.md` §4.1 S-01
- `docs/experiments/swing-mvp1/success-criteria.md` §1.3 G2 gate
