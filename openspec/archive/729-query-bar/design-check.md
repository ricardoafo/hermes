# Design check: 729-query-bar

What issue #729 builds, checked item by item against the approved design
(`docs/design/console-cockpit.md`): the "Reusable components" row for
`QueryBar` + `FacetFilter` + `SortableTable`, the shared facets and the query
syntax.

- Snapshots live in `tests/unit/entrypoints/__snapshots__/test_tui_snapshots/`
  and are named `query_activity_<theme>_<cols>x<rows>.svg`. Themes: `neon` and
  `control` (Control room). Sizes: `80x24`, `120x36`, `160x45`, `240x60`.
- Every snapshot shows the activity harness (`activity_query_app`) inside a
  `HudPanel`, with `since:2d` typed, 6 of 10 demo rows kept, and the facet
  counts under the bar. Motion is off (the input cursor does not blink) and the
  clock is frozen at 14:12 Madrid (12:12 UTC, 3 Oct 2026). The rows are
  synthetic and include restart, deploy, warning, error, stop-raised and a
  folded check line.
- Status: **match** = as the spec says; **diff** = differs, with the reason or
  the follow-up; **later** = the spec gives this to another issue.

## Differences, in one list

- D1: Facet dropdowns (1.2): the build shows facet counts as text lines under
  the bar, with no dropdown to pick a value. Typing `kind:closed` does the same.
  Proposed home: the first list screen that needs a mouse-first filter
  (issue #716 or #719); not owned today.
- D2: Facet counts are cut at 4 values with `+N more` (1.3), and are not
  clickable.
- D3: Kind facet shows the internal kind names (`bar`, `stop_raised`), so the
  folded 4h check line reads `bar` in the Kind column (3.4). Display labels are
  not built.
- D4: No `Enter` drill-down from a row (1.5) [issue #730].
- D5: Not wired into `hermes tui`: the screens that will use it (Logs, Lots,
  Briefs, History, Releases, Signals) are issues #714-#720; only the harness
  shows it (1.6).
- D6: The harness panel label says `computed` and its subtitle is the harness's
  own key hints; the real screens set their own (2.2).
- D7: Long cell text is clipped by the table at the panel edge, not cut with
  `…` and a `+N` count (3.5). Not exercised by the snapshots; `TruncatedText`
  is a separate component in the spec's list.
- D8: Only field names complete with Tab; values do not (1.4, change.md Out).
- D9 (addition): sort can be multi-level (`sort kind ▲ · when ▼`) and a
  header click or `S` both work; the spec names the click and `S` only.

## 1. Query bar, facets and syntax

| # | Spec | Build | Evidence | Status |
| --- | --- | --- | --- | --- |
| 1.1 | `QueryBar`: free-text query, result count | One input line; the status line under it reads `N of M · sort <column> ▼` | `query_activity_neon_160x45.svg` (`6 of 10 · sort when ▼`); `test_typing_a_query_filters_the_rows` | match |
| 1.2 | `QueryBar`: facet dropdowns | Facet counts as text (`kind  bar 1 · closed 1 · …`, `coin  BTC 2 · ETH 1`) | `query_activity_neon_160x45.svg` | diff: D1 |
| 1.3 | `FacetFilter` | Counts per facet value over the current result, most common first | `test_facet_lines_count_values_and_fold_the_rest`, `test_typing_a_query_filters_the_rows` | match; D2 |
| 1.4 | Tab completes field names | Tab completes the field being typed (or the common prefix) and keeps the focus in the input | `test_tab_completes_the_field_name_and_keeps_focus` | match; D8 |
| 1.5 | Enter opens the detail | Enter moves from the query line to the table | `test_enter_moves_to_the_table` | later: issue #730 (D4) |
| 1.6 | Used by Lots, Briefs, History, Logs, Releases, Signals bar list, trade timeline | Used by the activity harness only | `test_tui_query.py` | later: issues #714-#720 (D5) |
| 1.7 | A bad query is shown, not swallowed | The status line becomes the parse error in `hud-warn`; the last good result stays | `test_bad_query_shows_the_error_and_keeps_the_last_result` | match (an addition the spec does not mention) |
| 1.8 | Shared facets: year, month, day, coin, kind/state/level | Activity query fields are `year`, `month`, `day`, `coin`, `kind` (plus `since`); every field is a row key | `test_query_fields_are_shared_facets_the_rows_carry`, `test_query_rows_cover_the_journal_row_kinds_and_never_carry_money` | match |
| 1.9 | Same syntax everywhere: `field:value`, `field>=value`, free text, `since:7d` | One parser (`application/console/query.py`) for all lists; `since:24h` also accepted | `test_since_and_year_combine`, `test_free_text_searches_every_value`; `query_activity_*` show `since:2d` | match |
| 1.10 | Time facets in the operator's time | Local-time `year`, `month`, `day` and `when` (23:00 UTC on 31 Dec is the next year in Madrid) | `test_query_rows_use_local_time_facets_newest_first` | match |

## 2. Look and tokens

| # | Spec | Build | Evidence | Status |
| --- | --- | --- | --- | --- |
| 2.1 | Components are styled only through tokens | `DEFAULT_CSS` reads only `$hud-*` variables; no raw colour in `tui_query.py` | `test_query_widgets_use_theme_tokens_and_no_raw_colour` | match |
| 2.2 | `HudPanel` frames every panel | The harness sits in a `HudPanel` titled Activity (bracket corners, title bar, label, key-hint subtitle) | `query_activity_neon_160x45.svg`, `query_activity_control_160x45.svg` | match; D6 |
| 2.3 | Neon and Control room from the same tokens | Same widgets, Neon indigo surfaces and Control room navy ones, the cyan `live` header and cursor | `query_activity_neon_*`, `query_activity_control_*`; `test_the_harness_takes_its_colours_from_the_chosen_theme` | match |
| 2.4 | Focus state | The focused input takes `panel-focus`; the table cursor is `live` on `void` | `query_activity_neon_80x24.svg` | match |
| 2.5 | Read-only | The module imports no adapter and no port | `test_query_widgets_import_no_adapter_or_port` | match |

## 3. Table and sort

| # | Spec | Build | Evidence | Status |
| --- | --- | --- | --- | --- |
| 3.1 | Sort by clicking a column header | A header click sorts by that column; again flips; arrow on the header | `test_header_click_sorts_by_that_column` | match |
| 3.2 | Sort by pressing S | `S` sorts by the column under the cursor | `test_s_on_the_sorted_column_flips_it`, `test_s_on_another_column_makes_it_primary` | match; D9 |
| 3.3 | Newest first by default for the activity list | `when ▼` on start | `test_harness_starts_with_every_row_newest_first` | match |
| 3.4 | The feed shows restarts, deploys, warnings, errors and the one-line 4h check (#653) | All five are query rows (no money, no stop level; a folded check keeps its coin `count`); the folded check line is kind `bar` | `test_query_rows_cover_the_journal_row_kinds_and_never_carry_money`; `query_activity_neon_160x45.svg` | match; D3 |
| 3.5 | Long text truncates with `…` and a `+N` count | Cells clipped by the table | — | diff: D7 |
| 3.6 | Breakpoints 80x24, 120x36, 160x45, 240x60 | The list fills the panel at every size; columns When, Kind, Coin, Text | `query_activity_{neon,control}_{80x24,120x36,160x45,240x60}.svg` | match |
