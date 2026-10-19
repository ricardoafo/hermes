# Change: 1097-text-free-window-bar

**SDD change**: `1097-text-free-window-bar`
**Date**: 2026-10-18
**Status**: Shipped
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/1097-text-free-window-bar/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

The console's window bar leads every theme (Neon, Control room, night) as one row of the
theme's `chrome` gradient with no characters (Code Owner, 2026-10-17). Everything the Neon bar
carried moves to the status row or is dropped where the status row already says it. The main
pick is to give the feed pulse and the clock only the status row's spare cells, so they never
push out the heartbeat, the mainnet badge or the countdown. The main risk is losing the feed and
the clock at compact width, which is accepted: the parts that matter for safety stay.

## Scope

### In

- `src/hermes/entrypoints/tui_widgets.py`: `window_bar_text(width, tokens)` and a `WindowBar`
  that only paints the gradient.
- `src/hermes/entrypoints/tui.py`: the mainnet badge on the status row in every theme; `#feed`
  and `#clock` on the status row; `next_bar_text` removed.
- `src/hermes/entrypoints/tui_theme.py`: `WINDOW_MARK` and `WINDOW_TITLE` removed.
- `docs/design/console-cockpit.md`: the window-bar sentence.
- Pilot tests and every SVG snapshot.

### Out

- Any other status-row content or layout; the Lab tab (#1056) builds on this.

## Decisions

### D-1: The next-bar countdown is dropped

- **Decision**: Drop the bar's "next bar 01:12:05" text.
- **Alternatives**: Move it to the status row (it would say the same thing twice).
- **Rationale**: The status row's countdown ("in 3h 48m 00s") already gives the time to the
  next check, every second; the issue allows dropping what the status row already says.
- **Cost of reversal**: LOW.

### D-2: Feed pulse and clock use spare cells only, clock first to go

- **Decision**: `#feed` and `#clock` sit before the theme chip and fill only the cells left
  after the badge, demo tag, version, heartbeat, countdown and meter; the clock drops before the
  feed, and neither shows below `COMPACT_BELOW`.
- **Alternatives**: A second status row (one more row of chrome on every screen); fixed slots
  that cut the countdown (the countdown is what the operator acts on).
- **Rationale**: The design spec never cuts the heartbeat or the mainnet badge (#833); the feed
  says whether prices are flowing, which is worth more than the wall clock.
- **Cost of reversal**: LOW.

### D-3: The feed keeps its fixed 16-cell width

- **Decision**: Keep `feed_pulse` right-aligned in `FEED_WIDTH` cells.
- **Alternatives**: Trim the leading blanks (the row would shift on each of the first 16 polls).
- **Rationale**: A stable row; the 2026-10-06 spec for the pulse stays as written.
- **Cost of reversal**: LOW.

## Requirements

### R-1: A text-free bar in every theme

- **A-1.1**: `uv run pytest -q tests/unit/entrypoints/test_tui_window_bar.py -k leads_every_theme`
- **A-1.2**: `uv run pytest -q tests/unit/entrypoints/test_tui_cockpit.py -k window_bar_leads_every_theme`

### R-2: The mainnet badge on the status row in every theme

- **A-2.1**: `uv run pytest -q tests/unit/entrypoints/test_tui_topbar.py -k every_theme_status_row_starts_with_the_badge`

### R-3: Feed pulse and clock on the status row

- **A-3.1**: `uv run pytest -q tests/unit/entrypoints/test_tui_window_bar.py`

## Design

- `window_bar_text(width, tokens) -> Text`: `width` spaces on `gradient(tokens.chrome, i / last)`.
- `HomeApp._status_row_spare(countdown, meter, *, full_chip) -> int`, computed each tick.
- `HomeApp._paint_feed_and_clock()`: feed if `spare >= FEED_WIDTH + 1`, then the clock if
  `spare >= 9`; called from `_tick` and on each poll.

## Test plan

1. Every acceptance criterion above passes.
2. `uv run pytest -q tests/unit/entrypoints/` passes with the regenerated snapshots.
3. `bash scripts/dev/pr-preflight.sh` passes.

## Risks

- Compact Neon loses the clock and the feed; the badge and heartbeat stay. Accepted in the PR.
- Tests that pinned the old bar were rewritten because the behaviour changed on purpose; the PR
  lists each one.
