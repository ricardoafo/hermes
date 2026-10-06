# Change: console-positions-screen

**SDD change**: `console-positions-screen`
**Date**: 2026-10-07
**Status**: Applied
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/console-positions-screen/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

Tab 3 of the Cockpit console becomes the Positions screen: the open
positions beside the selected one's detail, with ↑↓ to choose. It reuses
Home's position rows and position detail, live between bars when the
ticker runs, so the two screens cannot disagree. No read-model or ledger
change. The main risk is the shared selection: Home and tab 3 move the same
cursor, which is the intended behaviour.

## Scope

### In

- `hermes.entrypoints.tui_positions`: layout (beside from 120 columns,
  stacked below), the count title and the hint.
- `tui.py`: `_paint_positions_screen` drawn on every redraw and every live
  repaint while tab 3 shows; `_live_rows` shared with Home's rows; ↑↓ work
  on tab 3.
- Pilot, layout and snapshot tests.

### Out

- Recently closed positions: no closed-positions view yet.
- "Why it opened" as three yes chips, "what would close it" (time limit,
  trend break): the detail shows what the read model has today.
- Enter on a row: the shared drill-down (issue #730).

## Decisions

### D-1: Reuse Home's renderables

- **Decision**: draw `position_rows` and the selected detail from the same
  cached read and live board as Home.
- **Alternatives**: a separate renderer (two screens drifting apart); a new
  read-model view (nothing new to read).
- **Rationale**: one source of truth for values, live or booked.
- **Cost of reversal**: LOW.

### D-2: One selection for Home and tab 3

- **Decision**: ↑↓ on either moves the same cursor.
- **Rationale**: the operator picks a position once and sees it on both.
- **Cost of reversal**: LOW.

## Requirements

### R-1: The screen

- **A-1.1**: `uv run pytest tests/unit/entrypoints/test_tui_positions.py`

## Design

`positions_screen(rows, detail, count, tokens, *, width)` lays out
`Open positions · N`, the rows and the hint, with the detail beside them
(five eighths for the list) from 120 columns or under them below that.
`HomeApp._paint_positions_screen` builds the rows at `list_width(width)`
with `live=self._live_rows()` and the detail with
`_detail_content(cached=True)`.

## Test plan

1. Every acceptance criterion above passes.
2. `uv run pytest tests/unit`, `uv run mypy src`, `uv run ruff check`,
   `uv run lint-imports` are clean.
