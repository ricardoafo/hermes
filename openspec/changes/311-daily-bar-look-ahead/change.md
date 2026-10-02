# Change: 311-daily-bar-look-ahead

**SDD change**: `311-daily-bar-look-ahead`
**Date**: 2026-10-02
**Status**: Applied
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/311-daily-bar-look-ahead/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

The calibration driver merged the 4h and 1D bar streams by `open_time`, so
each daily bar reached the swing adapter right after the day's first 4h bar,
20 hours before it closed. The 1D EMA regime filter could then read up to 16
hours of future prices on entry and exit. This change merges by `close_time`
in the driver and, more importantly, makes `SwingStrategyAdapter` ignore any
1D bar that has not closed by the 4h bar being evaluated, so no caller can
reintroduce the bias. Main risk: test fixtures that seeded future daily bars
before 4h bars relied on the leak and had to move their 4h bars later.

## Scope

### In

- `src/hermes/domain/strategy/swing/adapter.py`: regime filter reads only
  1D bars with `close_time <= signal_bar.close_time`.
- `scripts/backtest/run_calibration.py`: `_merge_by_close_time` replaces the
  `open_time` sort (1D before 4h on equal close time, matching #307).
- Regression + property tests; fixture time shifts in two existing swing
  test files.

### Out

- The live bar stream (#307) and serve loop (#309) already order by close
  time; the adapter guard now protects them too, no changes there.
- Buffer eviction counting not-yet-closed 1D bars (only matters if a caller
  delivers 100+ days early; not a realistic path).

## Decisions

### D-1: Enforce in the domain by filtering at evaluation time

- **Decision**: Keep every 1D bar in the buffer, and filter to bars closed by
  the 4h bar's `close_time` whenever the regime is evaluated.
- **Alternatives**: (a) Fix only the driver: leaves the invariant to every
  caller. (b) Reject 1D bars whose `close_time` is after the latest evaluated
  4h bar (the issue's first suggestion): that rejects the correct ordering,
  because the midnight 1D bar legitimately arrives after the 20:00 4h bar
  closes and before the 24:00 one. (c) Raise on early bars: breaks callers
  that are merely early, with no safety gain.
- **Rationale**: The docstring's no-lookahead contract (spec-5 R-8) becomes
  something the adapter guarantees, independent of delivery order.
- **Cost of reversal**: LOW — two private helpers.

### D-2: Equal close time puts 1D first

- **Decision**: The driver sorts by `(close_time, interval != D1)`.
- **Rationale**: The 4h bar closing at midnight should see that day's regime,
  the same as the live stream in #307.
- **Cost of reversal**: LOW.

## Requirements

### R-1: No signal depends on an unclosed 1D bar

- **A-1.1**: `uv run pytest tests/unit/domain/strategy/swing/test_daily_bar_close_time_gate.py`
  passes; its regression tests fail on the pre-fix adapter.
- **A-1.2**: Property: for any delivery that moves 1D bars earlier than
  their close-time position, the signal sequence equals the canonical one.

### R-2: Calibration driver delivers bars in close order

- **A-2.1**: `uv run pytest tests/unit/scripts_backtest/test_run_calibration.py`
  passes, including the merge-order test that fails on the `open_time` sort.

### R-3: Existing swing behaviour unchanged for correctly ordered input

- **A-3.1**: `uv run pytest tests/unit/domain/strategy tests/integration/strategy`
  passes with only fixture time shifts (no assertion changes).

## Design

```python
def _closed_1d_bars(self, as_of: datetime) -> list[Bar]:
    return [b for b in self._bars_1d if b.close_time <= as_of]


def _regime_above_ema(self, as_of: datetime) -> bool:
    closed = self._closed_1d_bars(as_of)
    return bool(closed) and EmaRegimeFilter.above_ema(closed, period=self._ema_period)
```

Entry condition 3 and exit condition C call these with `bar.close_time`.

## Test plan

1. Regression tests fail before the fix, pass after (done locally).
2. Full swing unit + integration suite green.
3. CI green on the PR.

## Risks

- **Fixtures encoded the leak**: integration and property-test fixtures fed
  12-15 daily bars starting on the same day as the 4h bars. Their 4h bars now
  start after the seed closes; assertions are unchanged. Signal-invariant
  tests that only assert when a signal fires were checked to still fire.
- **Per-bar cost**: one list filter over at most 300 bars per 4h bar;
  negligible.

## Dependencies on other changes

- None. Related: #307, #309.
