# Change: 1065-trend-slope-candidate

**SDD change**: `1065-trend-slope-candidate`
**Date**: 2026-10-17
**Status**: Shipped
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/1065-trend-slope-candidate/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

Candidate C3 of the first research batch (ADR-026, batch swing-b1):
`swing-b1-trend-slope` is v2's 4h Donchian breakout with one more entry
condition, a rising daily EMA(200). It ships as its own adapter,
`signal: donchian_slope` and a disabled `config/strategies.yaml` entry, so the
batch can bind it by `code_paths` before signing. No backtest is run here.

## Scope

### In

- `src/hermes/domain/strategy/swing/trend_slope.py`: `TrendSlopeAdapter` and
  `SlopeEntryCheck`.
- The `donchian_slope` kind registered with the strategy factory (issue #1074).
- A disabled `swing-b1-trend-slope` entry in `config/strategies.yaml`.
- Unit tests and Hypothesis properties on synthetic bars.

### Out

- Any backtest run, trial line or batch document change (the batch signing PR
  fills `code_paths` and `code_commit`).
- Serve journal fields for the fourth entry condition: serve logs v2's three,
  and the candidate is disabled.

## Decisions

### D-1: Extend v2's adapter, replace only the entry check

- **Decision**: `TrendSlopeAdapter` subclasses `SwingStrategyAdapter` and
  overrides `_entry_check` to add `rising`. Exit, stop posting, restore and
  seed are inherited unchanged.
- **Alternatives**: an optional flag on `SwingStrategyAdapter` (rejected: it
  touches v1/v2's code path); a copy of the adapter (rejected: two copies of
  the exit rules drift).
- **Rationale**: the card says "v2 §5.1 unchanged, plus" and "v2 §5.2
  unchanged"; inheritance makes that literally true.
- **Cost of reversal**: LOW.

### D-2: "S daily closes earlier" reads the same EMA series

- **Decision**: the earlier value is `ema_value(closed[:-S])` over the same
  daily buffer. The engine seeds from the oldest closes it is given, and the
  300-bar buffer fits its 3 × 200 window, so both values share one seed.
- **Alternatives**: keep a history of EMA values computed at each daily close
  (rejected: it depends on how long the process has run, so serve after a
  restart and the backtest would disagree).
- **Rationale**: deterministic from the buffer alone; the current value is
  exactly v2's regime EMA. Warm-up stays at 300 daily bars.
- **Cost of reversal**: LOW.

## Requirements

### R-1: Only the entry differs from v2

With a strictly rising daily EMA, the adapter emits exactly v2's signals on
every bar, entries and exits alike.

### R-2: The filter only removes entries

Every entry comes from a bar where v2's three conditions held and the daily
EMA rose over `ema_slope_lookback` closes.

### R-3: Parameters match the batch

Free parameters `ema_slope_lookback` (20), `donchian_period` (20),
`chandelier_atr_mult` (3.0); every other default is v2's.

## Design

`SlopeEntryCheck(EntryCheck)` adds `rising` and redefines `all_met`. The
adapter's `_trend_rising(as_of)` compares the daily EMA of the closed daily
bars with the EMA of the same bars less the last `ema_slope_lookback`; too
little history is `False`. A lookback below 1 is refused at construction.

## Test plan

Unit: falling-trend block, record contents, rising-trend pass-through, short
history, invalid lookback, defaults. Property: a rising daily trend gives v2's
signals; every entry has all four conditions.

## Risks

- A different reading of "its value S closes earlier" (D-2): raised with the
  Backtesting thread before signing; a change is a code change before the
  batch binds `code_commit`.

## Dependencies on other changes

- Issue #1074 (strategy factory) for the `signal:` key and registration.
