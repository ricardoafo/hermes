# Design check: 712-cockpit-home

What issue #712 builds, checked item by item against the approved design
(`docs/design/console-cockpit.md`: the Home row, round 3 Look A with C's
parts, the responsiveness table, and the Code Owner's 2026-10-06 text on the
legible big total).

- Snapshots live in `tests/unit/entrypoints/__snapshots__/test_tui_snapshots/`
  and are named `<view>_<theme>_<cols>x<rows>.svg`. Views used here:
  `home_ok` (scenario ok-two-positions) and `home_empty` (scenario empty).
  Themes: `neon` and `control` (Control room). Sizes: `80x24`, `120x36`,
  `160x45`, `240x60`. Further pictures, `test_tui_home_screen/test_svg_snapshot_empty*`,
  cover the empty and the empty-live-ledger scenarios in the default theme.
- All snapshots run with motion off, a frozen clock (14:12 Madrid) and demo
  data. With motion off the big total shows its final value and the shimmer
  never runs.
- Colours quoted below were read from the SVG fills, not guessed.
- Status: **match** = as the spec says; **diff** = differs, with the reason or
  the follow-up issue; **later** = the spec gives this to another issue.
- Where a picture cannot show a behaviour (count-up, shimmer, night, stale,
  money visibility) the test that checks it is named instead.
- Tests live in `tests/unit/entrypoints/`: `test_tui_home_screen.py`,
  `test_tui_home.py`, `test_tui_charts.py`, `test_tui_widgets.py`,
  `test_tui_theme.py`.

## Summary: every difference

Small, kept on purpose or cheap to change:

- D1: The Market panel shows its held "chart" column (placeholder dashes) at
  standard width only; at wide and ultra the panel is too narrow for it
  (inner width under 48), so no chart column shows (3.3).
- D2: The 90-day line is drawn dim as a whole, so the gradient bar is darker
  than the brand gradient (7.2). It also shows no bar before the run starts
  (7.3).
- D3: The empty-state line says "hermes is watching the market" when the
  market watch has no pairs (the demo `empty` scenario); the spec's "BTC ETH
  SOL" appears as soon as pairs exist (6.2). The `↑↓ select · ↵ detail` hint
  stays on the empty Positions panel (6.4).
- D4: At standard width (100-159) the position rows drop the entry → now
  prices; they show from 160 (4.1).
- D5: Under 30 rows the big total is hidden as well as the curve (the spec
  says the curve only); compact width keeps the plain bold total line (2.5).
- D6: At compact width Market and Activity are below the fold of an 80x24
  screen (1.1).

Owned by a later issue:

- D7: Live Kraken price, change and a braille chart per coin in Market (3.1,
  3.2) [issue #713].
- D8: Engine brief text and yes/no per condition (3.5) [issue #365, the
  supervisor desk].
- D9: Signals heat grid in the third column (8.2) [issue #714]; a live log
  tail (8.3) reads the VPS journal, so the demo shows an explanation.
- D10: Countdown meter and `mainnet off` text in the top bar (1.4); no issue
  owns `mainnet off` yet.
- D11: Live tag, bar-close toast, row slide-in, checking spinner (not part of
  this issue) [issues #713, #721].

## 1. Layout per breakpoint

| # | Spec | Build | Evidence | Status |
| --- | --- | --- | --- | --- |
| 1.1 | Compact < 100 cols: one column; tabs numbers only; charts become sparklines; detail panes become screens | One column (Portfolio, then Positions, then the rest below); tabs `1 2 3 … 8`; the curve is replaced by a one-line sparkline (`▁▂▁▄▆█`) under the net line; the body scrolls, so Market, Activity and Engine brief sit below the fold of 24 rows | `home_ok_neon_80x24.svg`, `home_ok_control_80x24.svg`; `test_compact_draws_the_sparkline_line_instead_of_the_curve`, `test_body_carries_the_breakpoint_classes` | match (D6: below-the-fold content is reached by scrolling) |
| 1.2 | Standard 100-159: two columns | Two columns from 100 (`TWO_COLUMNS_FROM` removed): Portfolio left, Positions / Market / Activity / Engine brief right; Market stacked above Activity | `home_ok_neon_120x36.svg`, `home_ok_control_120x36.svg`; `test_breakpoint_choice`, `test_columns_fill_the_width_with_one_cell_gutters` | match |
| 1.3 | Wide 160-219: "the mock layout" | Two columns, Portfolio left 60 cells; right column has Positions on top, Market beside Activity, then Engine brief | `home_ok_neon_160x45.svg`, `home_ok_control_160x45.svg` | match |
| 1.4 | Home top bar: brand, `SHADOW · mainnet off`, live dot, last bar ✓, next bar + countdown meter, release | Window bar (Neon), status row with DEMO tag, `● live · checked 14:00 · next 18:00 in 3h 48m 00s`, theme chip. The countdown is text, not a meter; no `mainnet off`; release shows when known | `home_ok_neon_160x45.svg` rows 0-2 | diff (D10): the countdown meter and `mainnet off` were proposed for this issue in the #711 check but are not built; follow-up needed (no issue yet) |
| 1.5 | Ultra ≥ 220: third column with Signals heat grid and a live log tail | Third column with a Signals placeholder panel and a Log tail panel | `home_ok_neon_240x60.svg`, `home_ok_control_240x60.svg`; `test_body_carries_the_breakpoint_classes` (only ultra shows `#third-column`) | match for the slot; contents later (see 8) |
| 1.6 | Height < 30 hides the curve and keeps the figures | At 24 rows the curve is hidden and the figures stay | `home_ok_neon_80x24.svg`; `test_curve_doubles_from_45_rows_and_hides_under_30` | match |
| 1.7 | Height ≥ 45 doubles chart heights | Curve rows double from 45 rows: standard 5 rows at 36, wide 6 at 44 and 12 at 45, ultra 12 at 60 | `home_ok_neon_160x45.svg`, `home_ok_neon_240x60.svg` (12 braille rows) vs `home_ok_neon_120x36.svg`; `test_curve_doubles_from_45_rows_and_hides_under_30` | match |
| 1.8 | Breakpoints from Textual's `HORIZONTAL_BREAKPOINTS` / `VERTICAL_BREAKPOINTS` | `home_layout(width, height)` returns one frozen `HomeLayout`; the app sets `-compact/-standard/-wide/-ultra` and `-short/-tall/-towering` on `#body` | `test_breakpoint_choice`, `test_body_carries_the_breakpoint_classes` (4 sizes) | match (change.md D-1) |

## 2. The big total

Spec (latest text, 2026-10-06): the digits are one colour at 7:1 contrast or
more, over a gradient underline that carries the shimmer; night never dims big
numbers.

| # | Spec | Build | Evidence | Status |
| --- | --- | --- | --- | --- |
| 2.1 | Hero figure in `Digits` | `BigNumber(Digits)`: Textual's bold 3x3 glyphs, `€1,021.06` | `home_ok_neon_120x36.svg` rows 4-6 | match |
| 2.2 | Digits in one colour, not a gradient (legibility) | One bold fill: `#c8fbff` in Neon, `#e6ecf5` in Control room | `home_ok_neon_160x45.svg`, `home_ok_control_160x45.svg` (digit row fills); `test_hero_digits_are_one_bold_colour`, `test_big_number_digits_are_one_full_contrast_colour` | match |
| 2.3 | At least 7:1 contrast | Neon 17.0:1, Control room 15.5:1, night 16.3:1 on `panel`; also on `panel-focus` | `test_big_total_digits_read_at_7_to_1_on_their_panel` (3 themes) | match |
| 2.4 | Gradient underline | One row of `▔` as wide as the digits: Neon cyan #00F0FF → violet #8A5CFF → magenta #FF3DF0; Control room #8BE9FF → #9D65FF | `home_ok_neon_160x45.svg`, `home_ok_control_160x45.svg` (the `▔` row); `test_rule_runs_from_the_first_to_the_last_stop` | match |
| 2.5 | Shimmer pass along the total | A bright band moves along the underline only, never over the digits; one pass every `MOTION.shimmer`, 8 fps; off with motion off | `test_shimmer_pass_moves_along_the_underline_and_ends`, `test_shimmer_brightens_only_the_rule_cells_near_it`, `test_big_total_jumps_with_motion_off_and_never_shimmers` | match |
| 2.6 | Total counts up | Reactive count-up over 600 ms with `out_cubic`; set at once with motion off | `test_big_total_counts_up_with_motion_on`, `test_big_total_jumps_with_motion_off_and_never_shimmers` | match |
| 2.7 | Night never dims big numbers | Digits stay `hero-digits` at night (`#E6ECF5`) | `test_big_number_digits_are_one_full_contrast_colour[night]` (23:30 Madrid); no night snapshot (the snapshot set is the two day themes) | match |
| 2.8 | (stale data) | A stale total keeps full digits; only the underline goes flat and dim; the head line says "as of" | `test_big_number_digits_are_one_full_contrast_colour[stale]`, `test_a_stale_rule_is_one_flat_colour_even_while_shimmering` | match (change.md D-4) |
| 2.9 | Big total shows from 30 rows | Hidden under 30 rows and at compact width; a plain bold total line leads the text (`€1,021.06   ▲ +€12.30 +1.22% today`) | `home_ok_neon_80x24.svg` (line 1 of Portfolio); `test_compact_width_keeps_the_plain_bold_total` | match for width; diff D5 for height: the spec hides the curve under 30 rows, the build hides the big total too (the earlier spec line says the total shows from 30 rows) |
| 2.10 | Money visible only where `pnl_visible` allows | A blind surface gets no total, curve or meters | `test_blind_surface_gets_no_total_curve_or_meters`, `test_blind_positions_fall_back_to_the_money_free_table` (SEV-1 class) | match |

## 3. Portfolio and risk meters (Look A with C's parts)

| # | Spec | Build | Evidence | Status |
| --- | --- | --- | --- | --- |
| 3.1 | Portfolio: big total, change + net, braille curve | After the digits: `▲ +€12.30 +1.22% today`, `net ▲ +€10.66 today`, `started €1,000.00 on 27 Sep`, then the curve | `home_ok_neon_160x45.svg` | match |
| 3.2 | Braille area curve with a vertical gradient | Braille area fill, bright at the top, dark at the bottom: gain gradient `#8be9ff` → `#3a76c4` in Neon when the range rises (loss gradient when it falls); curve is exactly the asked rows | `home_ok_neon_160x45.svg` (row fills); `test_rising_series_fills_from_the_bottom_left_to_the_top_right`, `test_curve_shape_and_order_property`, `test_area_chart_rows_go_from_the_bright_stop_down_to_the_dark_one`, `test_rising_range_draws_the_curve_in_the_gain_gradient` | match |
| 3.3 | Risk meters: cash / invested, exposure vs the 15% cap (C's part) | Cash `77.4%` and invested `22.6%` meters (cash in `live` #58D1EB, invested in `up` #468CE6); exposure line `23.1% · cap unknown` because the demo has no roster; with a roster it names the cap and turns amber from 80% of it | `home_ok_neon_160x45.svg`, `home_empty_neon_160x45.svg` (0.0%); `test_exposure_meter_names_the_cap_and_is_amber_near_it`, `test_exposure_meter_uses_the_cap_given_to_the_app`, `test_unknown_cap_is_said_not_guessed`, `test_cash_and_invested_meters_carry_level_and_share` | match for the logic; the demo pictures show "cap unknown" only. Cap comes from `strategies.yaml` (change.md D-2), never from code |
| 3.4 | Exposure basis | Invested over start capital (change.md D-3) | `test_exposure_is_invested_over_the_start_capital` | match (build decision; the spec does not say) |
| 3.5 | Waterfall Today / This year; open + tax if sold now | Table with Gross, Fees, Est. tax, Net for Today and This year; `Open ▲ +€9.36 · tax if sold now ▼ −€1.77` | `home_ok_neon_160x45.svg` | match |
| 3.6 | Subtitle `D W M Y range` | Panel bottom edge | `home_ok_neon_160x45.svg` | match |

## 4. Positions

| # | Spec | Build | Evidence | Status |
| --- | --- | --- | --- | --- |
| 4.1 | Rows with sparkline, change, net, stop-room meter | Wide: `coin tf entry → now trend change net stop room`, for example `▸ETH 4h €2,243 → €2,289 ▂▃▁▄█▆▆▆ ▲ +2.06% ▲ +€1.54 ███████░ stop −3.4%` | `home_ok_neon_160x45.svg`, `home_ok_neon_240x60.svg`; `test_wide_position_row_shows_prices_change_net_and_stop_room` | match |
| 4.2 | Narrower rows | Standard and compact drop the entry → now prices and the word "stop"; the trend sparkline stays at both (the narrowest rows, below the app's width limit, also drop it) | `home_ok_neon_120x36.svg`, `home_ok_neon_80x24.svg` (trend kept, no prices); `test_narrow_position_row_drops_prices_and_trend` | diff (D4): by design so the row fits; the spec does not name a narrower form |
| 4.3 | Stop-room meter | Meter in the gain gradient (`#1b3a6b` → `#77cef8`) for ETH (3.4% to the stop); amber `#e6be28` for SOL (3.0%, within 2 ATR) | `home_ok_neon_160x45.svg` (fills on the two rows); `test_stop_room_is_amber_when_the_stop_is_close` | match |
| 4.4 | Selected row marked, focus chip | `▸` on the selected coin; Positions is the focused panel | `home_ok_neon_160x45.svg`; `test_position_rows_mark_the_selected_coin` | match |
| 4.5 | "now" price and sparkline live from the 10 s ticker | Booked values only | same rows | later: issues #713 and #722 |

## 5. Market, Activity and Engine brief

| # | Spec | Build | Evidence | Status |
| --- | --- | --- | --- | --- |
| 5.1 | Market: live Kraken price, change, sparkline per coin; per-coin braille charts (C's part) | Table of last check, result and next check per pair; no price; at standard width a "chart" column is held with dim dashes; at wide and ultra the column does not fit | `home_ok_neon_120x36.svg` ("chart" column, dashes), `home_ok_neon_160x45.svg` (no column) | later: issue #713 (prices, change, per-coin charts). D1 (the held column is missing at wide and ultra) is cheap to fix with #713 |
| 5.2 | Panel name "Market" | Title "MARKET" with `computed`; the duplicate inner "Market watch" heading is gone | `home_ok_neon_160x45.svg` | match (resolves #711 check item 3.7) |
| 5.3 | Activity grouped by day, with icons | `Today / Yesterday / Mon 28 Sep` groups; one icon per kind: `✓` closed, `▲` stop raised, `⚠` warning, `◆` opened, `•` restart | `home_ok_neon_160x45.svg`; the renderer is shared with the CLI via `render_activity(icons=True)` (change.md D-6) | match |
| 5.4 | Engine brief: AI supervisor, P&L-blind, yes/no per condition | AI variant panel `✦ ENGINE BRIEF`, label `✦ AI supervisor`, text `brief not available yet` and `never sees profit` in the frame | `home_ok_neon_160x45.svg`; `test_engine_brief_is_the_ai_variant_with_no_brief_yet` | later: issue #365 writes the brief; the typewriter is issue #721 |
| 5.5 | Every panel labelled `computed` or `✦ AI supervisor` | Labels on all home panels | all `home_*` snapshots | match |

## 6. Designed empty state

| # | Spec | Build | Evidence | Status |
| --- | --- | --- | --- | --- |
| 6.1 | Radar spinner | A braille radar dot (`⠁`, four frames) in the `live` colour before "No open positions" | `home_empty_neon_160x45.svg` (`⠁ No open positions`); `test_radar_frame_steps_and_the_line_stands_alone_without_a_time` | match |
| 6.2 | "hermes is watching BTC ETH SOL and will look again at HH:MM" | With pairs in the market watch: `hermes is watching BTC ETH SOL and will look again at 18:00`. The demo `empty` scenario has no pairs: `hermes is watching the market and will look again at 18:00` | `home_empty_neon_160x45.svg`, `home_empty_control_80x24.svg`; `test_empty_state_names_the_watched_coins`, `test_empty_state_names_the_coins_and_the_next_look`, `test_empty_positions_show_the_radar_and_the_next_look` | match (D3: the picture shows the no-pairs wording; the named-coin wording is covered by the two tests) |
| 6.3 | Nothing is traded from here | `A position appears here when a rule fires. Nothing is traded from this screen.` | `home_empty_neon_160x45.svg` | match |
| 6.4 | — | The Positions subtitle `↑↓ select · ↵ detail` still shows with no rows | `home_empty_neon_160x45.svg` | diff (D3, small): no rows to select; cheap to hide |
| 6.5 | Market and Activity with nothing yet | `No pairs checked yet.` and `No activity yet.` | `home_empty_neon_160x45.svg` | match |
| 6.6 | Empty portfolio | `€1,000.00` big total, `· €0.00 0.00% today`, empty curve rows, cash 100.0% meter, invested 0.0% | `home_empty_neon_160x45.svg`, `home_empty_neon_120x36.svg`; the empty-live-ledger case: `test_svg_snapshot_empty_live_ledger[...]` | match |

## 7. The 90-day run bar

| # | Spec | Build | Evidence | Status |
| --- | --- | --- | --- | --- |
| 7.1 | Bottom: 90-day run gradient bar | Line above the footer: `Shadow test · day 12 of 90 · on track  ███…░░░  13%`, bar as wide as the line | `home_ok_neon_160x45.svg`, `home_ok_neon_240x60.svg`; `test_run_line_ends_with_the_bar_and_percentage`, `test_run_bar_fills_day_over_length_in_the_brand_gradient` | match |
| 7.2 | Brand gradient | The filled part follows the brand gradient and the empty part is a line colour (`#34186f` Neon); the whole line is drawn dim, so the fills read darker (Neon `#039fb3` → `#1a88b3` over the first 13%, Control room `#5490ab` → `#528eab`) | `home_ok_neon_160x45.svg`, `home_ok_control_160x45.svg` | diff (D2, small): the dim style on the line darkens the gradient; one style change if the Code Owner wants it full strength |
| 7.3 | Before the run starts | Words only: `Shadow test · not started · waits on #353`, no bar | `home_empty_neon_160x45.svg`; `test_run_line_before_the_start_is_the_words_only` | diff (D2, small): by design, there is no day to fill |
| 7.4 | Present at every width | Shown at 80x24 (`…  13%`), 120x36, 160x45, 240x60 | `home_ok_neon_80x24.svg` | match |

## 8. Ultra third column

| # | Spec | Build | Evidence | Status |
| --- | --- | --- | --- | --- |
| 8.1 | Third column at 220+ | A third column with a Signals panel above a Log tail panel | `home_ok_neon_240x60.svg`, `home_ok_control_240x60.svg` | match |
| 8.2 | Signals heat grid | `Heat grid · coming next · issue #714` | `home_ok_neon_240x60.svg` | later: issue #714 |
| 8.3 | Live log tail | Panel with `L logs`; says `Logs come from the VPS journal; demo data has none.` in the demo; shows the last journal lines on the VPS; says when no journal is readable | `home_ok_neon_240x60.svg`; `test_log_tail_shows_the_journal_at_ultra_width`, `test_log_tail_says_when_no_journal_is_readable`, `test_log_tail_explains_demo_and_missing_journal`, `test_log_tail_keeps_the_last_lines` | match for the panel; the picture shows the demo explanation only |

## 9. Safety and snapshot coverage

| # | Spec | Build | Evidence | Status |
| --- | --- | --- | --- | --- |
| 9.1 | The console only reads | Home calls read-model read methods only, at every width | `test_app_only_reads_at_every_width` | match |
| 9.2 | Tokens only, data stays semantic in both themes | Digits, meters and bars take colours from tokens; gains #468CE6, stop-near amber #E6BE28 in both themes | `home_ok_neon_160x45.svg`, `home_ok_control_160x45.svg` | match |
| 9.3 | Snapshot tests at 80x24, 120x36, 160x45, 240x60 | All present for both themes and both scenarios (table below); motion off, frozen clock, demo data | `test_tui_snapshots.py::test_cockpit_svg_snapshot` (ids `home_ok_<theme>_<size>`, `home_empty_<theme>_<size>`) | match |

Home snapshot files (all in `test_tui_snapshots/`):

| Scenario | Theme | 80x24 | 120x36 | 160x45 | 240x60 |
| --- | --- | --- | --- | --- | --- |
| ok | Neon | `home_ok_neon_80x24.svg` | `home_ok_neon_120x36.svg` | `home_ok_neon_160x45.svg` | `home_ok_neon_240x60.svg` |
| ok | Control room | `home_ok_control_80x24.svg` | `home_ok_control_120x36.svg` | `home_ok_control_160x45.svg` | `home_ok_control_240x60.svg` |
| empty | Neon | `home_empty_neon_80x24.svg` | `home_empty_neon_120x36.svg` | `home_empty_neon_160x45.svg` | `home_empty_neon_240x60.svg` |
| empty | Control room | `home_empty_control_80x24.svg` | `home_empty_control_120x36.svg` | `home_empty_control_160x45.svg` | `home_empty_control_240x60.svg` |
