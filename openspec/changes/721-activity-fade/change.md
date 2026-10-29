# Change: 721-activity-fade

**SDD change**: `721-activity-fade`
**Date**: 2026-10-18
**Status**: Applied
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/721-activity-fade/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

The part of issue #721 that is not built yet: a row that appears in the
Activity card between two reads is highlighted for 2 s, fading out. The rest of
the issue is already in the console (the bar-close toast, the count-up of the
total, the `HERMES_CONSOLE_MOTION=off` switch). The slide-in and the checking
spinner are not part of this change: a terminal row cannot slide, and a
spinner needs a "bar is being processed" signal that the heartbeat does not
carry (it knows when a check is due and when it is late, not when one is
running); both stay with issue #721.

## Scope

### In

- `application/console/render.py`: `render_activity` takes a style per row.
- `entrypoints/tui.py`: the app keeps the rows of the last read, marks the ones that are new, and steps a fade on a timer.
- `entrypoints/tui_theme.py`: `MOTION.activity_fade` (2 s) and `activity_fade_steps` (4).

### Out

- The slide-in motion and the checking spinner (above).

## Decisions

### D-1: new means not in the last read

- **Decision**: a row is new when it equals no row of the previous read; the first read marks none.
- **Alternatives**: compare timestamps with the console clock.
- **Rationale**: the clock and the ledger can disagree, and a folded stop raise (a changed count) should read as new too.
- **Cost of reversal**: LOW.

### D-2: a background tint, stepped

- **Decision**: the row gets a background from the live colour at 40% down to the panel colour in four steps over 2 s, redrawing only the Activity card.
- **Alternatives**: animate a widget style with Textual's animator.
- **Rationale**: the card is one rendered text; four repaints of it cost nothing, and the snapshots stay stable.
- **Cost of reversal**: LOW.

## Requirements

### R-1: highlight

A row that is new in a read is highlighted and fades out in 2 s.

### R-2: motion off

With motion off no row is ever highlighted.

### R-3: first read

The first read after the console opens highlights nothing.

## Design

`_note_fresh_activity` diffs each read against the last and starts an interval of `activity_fade / activity_fade_steps`; `_fade_activity` steps the share down and clears the set on the last step; `_paint_activity` passes the styles to `render_activity`.

## Test plan

- `tests/unit/entrypoints/test_tui_activity_fade.py`: nothing fresh on the first read or with nothing new, the new row fresh, the fade ending, motion off, and `render_activity` styling only the given rows.
