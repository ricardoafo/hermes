# Change: 1162-live-meters

**SDD change**: `1162-live-meters`
**Date**: 2026-10-18
**Status**: Applied
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/1162-live-meters/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

While the live layer is live, the Portfolio panel's invested meter and the
exposure meter use the open positions at their live prices (the third item of
issue #722). The live total already values every position at its live price;
it now also carries the invested figure, and the meters repaint on every tick
from it. Cash does not move between bars, so it keeps its booked value; its
share moves because the total does. The main risk is mixing live and booked in
one panel, so the meters take either the whole live view or the whole booked
one.

## Scope

### In

- `application/console/live.py`: `LiveTotal.invested`.
- `entrypoints/tui.py`: the meters repaint on every portfolio paint, from the live total while live.
- The live Home snapshots.

### Out

- Live "tax if sold now" (item 4) and the other items of issue #722.

## Decisions

### D-1: whole live or whole booked

- **Decision**: with a live total and a live (not paused) layer the meters take the live total and the live invested figure; otherwise the booked summary.
- **Alternatives**: meters live per coin, mixing a priced coin with a booked one.
- **Rationale**: the live total itself exists only when every open position has a live price, so a half-live meter would disagree with the big number.
- **Cost of reversal**: LOW.

### D-2: paused goes back to booked

- **Decision**: a paused layer shows the booked meters.
- **Alternatives**: keep the last live meters dimmed.
- **Rationale**: the meters have no dim state, and a stale exposure share beside a dimmed total would read as current.
- **Cost of reversal**: LOW.

## Requirements

### R-1: live meters

While live, the invested figure and the exposure share are computed at the live prices; the cash figure is the booked one.

### R-2: fallback

With no live total (no ticker, a coin without a price, EUR pending, a surface that hides money) or while paused, the meters are the booked ones.

### R-3: display only

Nothing is written; the live module's import-linter contract holds.

## Design

`live_total` adds the price change of the open positions to `invested` as it does to the total. `_paint_portfolio` builds the meters from the summary with the total and the invested figure replaced by the live ones.

## Test plan

- `tests/unit/application/console/test_live.py`: a worked example, and a property test that cash plus the live invested figure is the live total.
- `tests/unit/entrypoints/test_tui_live_screen.py`: the meters at live prices, and back to booked when paused.
- The live Home snapshots, reviewed in both themes and Night.
