# Design check: 730-drill-down

What issue #730 builds, checked item by item against the approved design
(`docs/design/console-cockpit.md`, round 3, "Drill-down rules" and its
inventory, plus the component table and the navigation rules).

## Differences, in one list

Not built here, no issue owns them yet (proposed home in brackets):

- D1: Ctrl+P opens the command palette (screens and themes), not a search of
  everything (2.6) [issue #729, the shared query bar].
- D2: A detail screen hides the status row, so a demo run shows its figures
  without the DEMO DATA tag; the footer still says `read-only` (2.8)
  [issue #715, when the Positions screen adopts the detail screen].

Owned by a later issue:

- D3: Every inventory row except the Home position detail: Releases and
  Services (#717), Signals (#714), Lots (#719), Briefs (#720), Logs and its
  trace screen (#716), History and the Home Activity row (MVP2, no issue) (4.1,
  4.2, 4.4-4.9).
- D4: `ListState.filters` and `ListState.sort` are carried and restored, but
  no list fills them yet; Home's positions list hands over only its cursor
  (2.3) [issue #729].
- D5: The Home position detail wraps long lines instead of cutting them with
  "…"; `TruncatedText` is built and tested but no Home text uses it (3.4)
  [issues #715-#720 adopt it in their lists].

Deliberate and recorded:

- D6: `[` / `]` stop at the ends instead of wrapping (2.4, change.md D-2).
- D7: A breadcrumb that does not fit gives up its leading crumbs first and
  always keeps the item (2.2); the spec does not say which part goes.
- D8: The detail screen replaces all of the chrome (window bar, tabs, status
  row, 90-day line) with the breadcrumb, not just the top row (2.1).
- D9: At 80 columns the screen footer uses 79 of 80 cells, so the position
  count sits one space after the last chip (2.7; change.md Risks).

Resolved by the merge with main:

- D10: The pane opens from 100 columns. Before issue #712 Home stacked up to
  119 columns and opened a screen there; it now follows the spec's compact
  rule exactly (1.2, change.md D-4).

## How to read this

- Snapshots live in `tests/unit/entrypoints/__snapshots__/test_tui_snapshots/`
  and are named `<view>_<theme>_<cols>x<rows>.svg`. Views:
  `drill_position` (Enter on the first position, ETH: the detail pane from 100
  columns, the detail screen at 80) and `drill_breadcrumb` (the detail screen
  after `]`, showing SOL; opened at 80x24 and then widened to the size in the
  name). Themes: `neon` and `control` (Control room). Sizes: `80x24`,
  `120x36`, `160x45`, `240x60`.
- All snapshots run with motion off, a frozen clock (14:12 Madrid) and the
  `ok-two-positions` demo scenario, and never read the real settings file.
- Status: **match** = as the spec says; **diff** = differs, with the reason
  or the follow-up issue; **later** = the spec gives this to another issue.
- Behaviour a picture cannot show is named by its test in
  `tests/unit/entrypoints/test_tui_drilldown.py` unless another file is named.

## 1. Pane or screen

| # | Spec | Build | Evidence | Status |
| --- | --- | --- | --- | --- |
| 1.1 | Enter opens; a detail that fits opens as a `DetailPane` beside the list | `DetailPane` is a `HudPanel` shown only while open; on Home it sits in the right column under Positions | `drill_position_neon_120x36.svg`, `drill_position_neon_160x45.svg`, `drill_position_neon_240x60.svg`; `test_home_wide_enter_opens_the_pane_beside_the_list` | match |
| 1.2 | Compact (< 100 cols): detail panes become screens | `detail_mode(width)` returns SCREEN below `COMPACT_BELOW` (100), PANE from 100 | `drill_position_neon_80x24.svg` (screen) vs `drill_position_neon_120x36.svg` (pane); `test_compact_width_opens_a_screen_instead_of_a_pane`, `test_home_compact_enter_opens_a_detail_screen`, `test_home_from_100_columns_opens_the_pane_not_a_screen` | match (D10) |
| 1.3 | A detail that needs room opens as a `DetailScreen` | `detail_mode(..., beside=False)` returns SCREEN for a host with no room beside its list | `test_a_host_without_room_beside_the_list_opens_a_screen` | match |
| 1.4 | One implementation of each component, styled only through tokens | `DetailPane` subclasses `HudPanel`; `DetailScreen` frames its body in a `HudPanel` and draws its footer with `key_chips`; colours come from the token sheet and `$hud-*` | `drill_position_control_160x45.svg` vs `drill_position_neon_160x45.svg` (same frame, theme colours); `test_breadcrumb_item_is_styled_by_tokens` | match |
| 1.5 | Esc goes back from detail panes | Esc (and Backspace) close the pane before leaving Home; the pane subtitle reads `Esc close` | `drill_position_neon_160x45.svg`; `test_tui_cockpit.py::test_escape_closes_the_detail_pane_before_leaving_home`, `test_backspace_closes_the_home_pane_like_escape` | match |
| 1.6 | — | With the pane open, ↑ ↓ move the positions cursor and the pane follows | `test_tui.py::test_down_key_moves_the_cursor_and_the_detail_follows` | match (kept from main) |

## 2. Detail screen

| # | Spec | Build | Evidence | Status |
| --- | --- | --- | --- | --- |
| 2.1 | The top bar becomes a breadcrumb (`◂ System › Releases › 2bc2798`) | Row 0 is `◂ Home › Positions › ETH`; the window bar, tabs, status row and 90-day line are not drawn on the screen | `drill_position_neon_80x24.svg`, `drill_breadcrumb_neon_160x45.svg` row 0; `test_detail_screen_top_bar_is_the_breadcrumb`, `test_breadcrumb_text_reads_back_trail_and_item` | match; hiding the rest of the chrome is D8 |
| 2.2 | — | A breadcrumb wider than the screen gives up its leading crumbs first and keeps the item | `test_breadcrumb_gives_up_leading_crumbs_first`, `test_breadcrumb_widget_repaints_on_a_new_trail` | diff: implementer choice, the spec is silent (D7) |
| 2.3 | Esc or Backspace goes back one level and keeps the list's filters, sort and cursor | The screen returns the list's `ListState` from `dismiss`, cursor on the item shown last; the list restores it even after a refresh reset it | `test_back_restores_filters_sort_and_cursor`, `test_backspace_goes_back_like_escape`, `test_back_puts_the_cursor_on_the_item_shown_last`, `test_home_back_from_the_screen_keeps_the_cursor_on_the_item_shown`, `test_home_list_state_carries_the_cursor` | match; on Home only the cursor exists today (D4, issue #729) |
| 2.4 | `[` / `]` go to the previous / next item without going back | One item each way; both stop at the ends; an item that left the list steps to the first | `drill_breadcrumb_neon_80x24.svg` (SOL, `2 of 2`); `test_brackets_move_through_items_and_stop_at_the_end`, `test_previous_and_next_stop_at_the_ends`, `test_step_never_leaves_the_list_and_moves_at_most_one` (property) | match; no wrap is D6 |
| 2.5 | Number keys still jump between screens | `1`-`8` stay app bindings; the screen closes and the tab shows | `test_number_keys_still_jump_from_a_detail_screen` | match |
| 2.6 | Ctrl+P searches everything | Ctrl+P opens the command palette (screens and themes) from the screen | `test_ctrl_p_still_opens_the_palette_from_a_detail_screen` | diff: no search of data yet (D1) |
| 2.7 | Footer | Key chips `Esc back`, `[ ] prev/next`, `1-8 screens`, `^P palette`, `Q quit`, then `N of M · read-only` at the right | `drill_breadcrumb_neon_160x45.svg` last row; `drill_position_neon_80x24.svg` (79 of 80 cells); `test_detail_screen_body_and_footer_follow_the_item`, `test_item_position_names_the_place_in_the_list` | match; the tight 80-column footer is D9 |
| 2.8 | — | The status row (and its DEMO DATA tag) is not on the screen | `drill_position_neon_80x24.svg` | diff (D2) |
| 2.9 | Help lists the keys | Help names `Esc or Backspace back` and `[ ] previous / next item inside a detail screen` | `test_help_names_the_drill_down_keys` | match |

## 3. Truncation

| # | Spec | Build | Evidence | Status |
| --- | --- | --- | --- | --- |
| 3.1 | Long text truncates with "…" at the panel edge | `truncate_line` cuts with `ELLIPSIS` at the width and marks hidden lines | `test_truncate_line_cuts_with_an_ellipsis_at_the_edge`, `test_truncate_line_marks_hidden_lines`, `test_truncate_line_never_overflows` (property) | match |
| 3.2 | A "+N" count when a list is cut | `truncate_items` ends a cut list with `+N` | `test_truncate_items_counts_what_was_cut`, `test_truncate_items_fits_and_counts` (property) | match |
| 3.3 | The full text is always one Enter away | `TruncatedText` shows one cut line, expands on Enter (or a click), folds on a second Enter | `test_truncated_text_shows_one_cut_line`, `test_truncated_text_expands_on_enter`, `test_truncated_list_shows_the_count_then_every_item_on_enter` | match |
| 3.4 | — (applies to every panel) | The Home position detail wraps its long lines (`Why: … atr` / `€25.79`) | `drill_position_neon_80x24.svg` rows 4-7 | later: D5 |

## 4. Inventory

| # | From | Opens | As | Build | Status |
| --- | --- | --- | --- | --- | --- |
| 4.1 | System › Releases row | release detail | DetailScreen | — | later: issue #717 |
| 4.2 | History row | the trade's full story | DetailScreen | — | later: MVP2 (History has no issue) |
| 4.3 | Positions row | position detail | DetailPane (wide) / DetailScreen (compact) | Home's positions list: the pane from 100 columns, the screen below; the same `render_position_detail` content either way | match: `drill_position_*`, `drill_breadcrumb_*`; `test_home_detail_content_is_the_same_as_a_pane_or_a_screen`. The Positions screen (tab 3) adopts it in issue #715 |
| 4.4 | Signals bar cell or row | that bar check | DetailScreen | — | later: issue #714 |
| 4.5 | Lots row | lot FIFO detail | DetailPane | — | later: issue #719 |
| 4.6 | Briefs row | brief | DetailPane (wide) / DetailScreen (compact) | — | later: issue #720 |
| 4.7 | Logs line | JSON + trace | DetailPane; T opens the trace as a DetailScreen | — | later: issue #716 |
| 4.8 | Activity row (Home) | jumps to History detail | DetailScreen | — | later: MVP2 (needs the History detail) |
| 4.9 | Services row | unit status + journal lines | DetailPane | — | later: issue #717 |

## 5. Snapshots

| # | Spec | Build | Evidence | Status |
| --- | --- | --- | --- | --- |
| 5.1 | Snapshots per screen at 80x24, 120x36, 160x45 and 240x60 | 16 new: 2 views × 2 themes × 4 sizes, next to main's 32 | `test_tui_snapshots.py -k drill` | match |
| 5.2 | — | Main's `home_*`, `tab2_placeholder_*` and `theme_picker_*` snapshots are unchanged by this change | `test_tui_snapshots.py -k "not drill"` (32 passed) | match |
