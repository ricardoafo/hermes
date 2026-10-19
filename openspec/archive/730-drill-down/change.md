# Change: 730-drill-down

**SDD change**: `730-drill-down`
**Date**: 2026-10-06
**Status**: Shipped
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/730-drill-down/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

The shared drill-down of the Cockpit console (design spec
`docs/design/console-cockpit.md`, round 3, "Drill-down rules"). One module,
`tui_drilldown.py`, holds `DetailPane` (detail beside a list), `DetailScreen`
(full-screen detail with a `Breadcrumb` top bar, back, previous / next),
`Breadcrumb` and `TruncatedText`, all built on #711's `HudPanel`, `key_chips`
and tokens. A small `ListState` / `DrillList` contract lets any list hand its
filters, sort and cursor to a detail screen and get them back. Home's position
detail adopts it with unchanged content, so the framework runs in the app
before the screens of issues #715-#720 use it. On top of issue #712's Home
layout the pane sits in the right column from 100 columns and the screen opens
below 100. The main pick is "stop at the ends" for `[` / `]`.

## Scope

### In

- `src/hermes/entrypoints/tui_drilldown.py`: `DetailMode` + `detail_mode`,
  `ListState` + `DrillList`, `step_item`, `item_position`, `truncate_line`,
  `truncate_items`, `truncated_text`, `breadcrumb_text`, and the widgets
  `Breadcrumb`, `TruncatedText`, `DetailPane`, `DetailScreen`.
- `src/hermes/entrypoints/tui_theme.py`: glyph tokens `BACK_GLYPH` (◂),
  `CRUMB_SEPARATOR` (›), `ELLIPSIS` (…).
- `src/hermes/entrypoints/tui.py`: the Home detail is a `DetailPane` when wide
  and a `DetailScreen` (breadcrumb `◂ Home › Positions › ETH`) otherwise;
  `HomeApp` implements `DrillList`; Backspace also goes back; help names
  `[` / `]`.

### Out

- The drill-downs of the other screens (Releases, History, Signals, Lots,
  Briefs, Logs, Services): issues #715-#720 adopt the components.
- The query bar that fills `ListState.filters` and `ListState.sort`: issue #729.
- Home's per-breakpoint layout itself: issue #712 (merged; this change only
  swaps its detail panel for a `DetailPane`).

## Decisions

### D-1: one drill-down module that extends the #711 widgets

- **Decision**: `tui_drilldown.py` holds the four components; `DetailPane` subclasses `HudPanel`, `DetailScreen` frames its body in a `HudPanel` and draws its footer with `key_chips`; colours come only from the token sheet and the `$hud-*` variables.
- **Alternatives**: add them to `tui_widgets.py` (one 900-line module); a second panel frame for details (two variants of one thing).
- **Rationale**: the spec's rule "never build a second variant; extend the component"; the module stays focused and is tested on its own.
- **Cost of reversal**: LOW.

### D-2: `[` and `]` stop at the ends

- **Decision**: `step_item` returns the neighbour, or the same item at either end; an item that left the list on a refresh steps to the first one still listed. The footer shows "2 of 5".
- **Alternatives**: wrap around, as Home's ↑↓ cursor does.
- **Rationale**: inside a detail screen the list is out of sight; a wrap would jump from the newest item to the oldest without the operator noticing. The position count makes the end visible.
- **Cost of reversal**: LOW (one function).

### D-3: the saved-state contract is a value, handed out and back

- **Decision**: `ListState(filters, sort, descending, cursor)` is a frozen value. The list gives it to `DetailScreen` when it opens; the screen returns it from `dismiss`, with the cursor on the item shown last, and the list's `restore_list_state` is the `push_screen` callback.
- **Alternatives**: the screen reads and writes the list widget directly (couples every detail to every list); keep nothing and rely on the list staying intact (a 5 s refresh can rebuild it).
- **Rationale**: one small protocol (`DrillList`) any list can implement, and the screen never needs to know the list's type.
- **Cost of reversal**: LOW.

### D-4: pane or screen is one function of width and room

- **Decision**: `detail_mode(width, beside=...)` returns SCREEN below `COMPACT_BELOW` (100) columns or when the host has no room beside the list, else PANE. Since issue #712 Home has two columns from 100, so it calls `detail_mode(width)` and opens the pane from 100 columns; `beside` stays for a later host whose layout stacks wider than the compact width.
- **Alternatives**: a Home-only width check (a second rule for one screen).
- **Rationale**: the compact rule is shared by every screen and lives in one function.
- **Cost of reversal**: LOW.

### D-5: number keys and Ctrl+P stay app bindings

- **Decision**: `DetailScreen` binds only Esc / Backspace and `[` / `]`; `1`-`8`, Tab, Ctrl+P and Q fall through to the app, which closes any screen before switching tabs.
- **Alternatives**: rebind them on the screen (duplicated key map).
- **Rationale**: one key map; the existing "no key bound twice" test still holds.
- **Cost of reversal**: LOW.

## Requirements

### R-1: pane or screen

- **A-1.1**: `detail_mode` returns SCREEN at 80 and 99 columns and PANE at 100 and 160; SCREEN when `beside=False`.
- **A-1.2**: on Home at 80x24, Enter pushes a `DetailScreen` whose first row reads `◂ Home › Positions › ETH`; at 100x30 and 160x45 Enter opens the `DetailPane` and pushes no screen.

### R-2: back restores the list

- **A-2.1**: Esc and Backspace close the screen; the list gets back its filters, sort and cursor even when a refresh reset them meanwhile.
- **A-2.2**: after `]` and Esc the cursor is on the item shown last (Home: SOL).

### R-3: previous / next

- **A-3.1**: `]` and `[` move one item; both stop at the ends; the breadcrumb and the footer ("1 of 3 · read-only") follow.
- **A-3.2**: `3` from a detail screen shows the Positions tab; Ctrl+P opens the command palette.

### R-4: truncation

- **A-4.1**: `truncate_line` cuts with "…" at the width and marks hidden lines; `truncate_items` ends a cut list with "+N".
- **A-4.2**: `TruncatedText` shows one cut line, the full text on Enter, and folds again on a second Enter.

### R-5: snapshots

- **A-5.1**: SVG snapshots in Neon and Control room at 80x24, 120x36, 160x45 and 240x60, with motion off, a frozen clock and demo data: `drill_position_<theme>_<size>` (Enter on the first position: a pane from 100 columns, a screen at 80) and `drill_breadcrumb_<theme>_<size>` (the detail screen after `]`, opened at 80x24 and widened). The existing console snapshots are unchanged.

R-1..R-4: `uv run pytest -q tests/unit/entrypoints/test_tui_drilldown.py`; R-5: `tests/unit/entrypoints/test_tui_snapshots.py -k drill`. Item-by-item check against the spec: `design-check.md`.

## Design

```text
src/hermes/entrypoints/
  tui_theme.py      + BACK_GLYPH, CRUMB_SEPARATOR, ELLIPSIS
  tui_drilldown.py  DetailMode, detail_mode, ListState, DrillList, step_item,
                    item_position, truncate_line, truncate_items, truncated_text,
                    breadcrumb_text, Breadcrumb, TruncatedText, DetailPane, DetailScreen
  tui.py            HomeApp: DetailPane on Home, DetailScreen on compact,
                    list_state / restore_list_state
```

`DetailScreen` layout, top to bottom: `Breadcrumb` (trail + item label),
a scrolling `HudPanel` with the item's detail (subtitle `[ ] prev/next · Esc
back`), and a footer row of key chips plus "N of M · read-only". The app's
5 s refresh calls `refresh_panel()` on it, which re-reads the item keys and
rebuilds the body, so a closed position drops out of previous / next.

## Test plan

1. Every acceptance criterion above passes.
2. `bash scripts/dev/pr-preflight.sh` → all checks green.
3. All CI jobs green on the PR.

Test tiers touched: unit, property

## Risks

- **Footer width at 80 columns**: the chips and the position count use 79 of 80 cells; longer chips must drop labels, as the compact Home footer does.
- **Components need the `$hud-*` variables**: an app that does not provide them fails at CSS parse; every hermes app does through `theme_variables`.

## Dependencies on other changes

- Builds on issue #711 (`openspec/archive/711-cockpit-theme/change.md`). Follow-ups: issues #712, #715-#720, #729.
