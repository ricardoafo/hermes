# Design check: 713-live-ticker

What issue #713 builds, checked item by item against the approved design
(`docs/design/console-cockpit.md`: the Home row's "Live between bars" part,
the Market panel, the live tag, the flash and the paused state) and the Code
Owner's 2026-10-06 decisions (chart caption, time axis, feed pulse, void
background; change.md D-7).

- Snapshots live in `tests/unit/entrypoints/__snapshots__/test_tui_snapshots/`
  and are named `<view>_<theme>_<cols>x<rows>.svg`. Views used here:
  `home_live` (scenario ok-two-positions with a fake ticker: BTC 62,000,
  ETH 2,700, SOL 150 USDT, then seven more polls, the fourth with SOL
  failed), `home_ok` and `home_empty` (no ticker). Themes: `neon` and
  `control` (Control room). Sizes: `80x24`, `120x36`, `160x45`, `240x60`.
  Further pictures, `test_tui_live_screen/test_svg_snapshot_live*`, cover the
  live screen in the default theme.
- All snapshots run with motion off, a frozen clock (14:12 Madrid) and
  synthetic data; nothing reaches the network.
- Colours quoted below were read from the SVG fills, not guessed.
- Status: **match** = as the spec says; **diff** = differs, with the reason or
  the follow-up issue; **later** = the spec gives this to another issue.
- Where a picture cannot show a behaviour (cadence, flash, paused, motion,
  money visibility) the test that checks it is named instead.

## Summary: every difference

Small, kept on purpose or cheap to change:

- D1: In the snapshots every poll returns the same price, so the Market
  panel's 15-minute change reads `· 0.00%` and its sparkline is a flat
  bottom line (2.1). The moving case is covered by
  `test_market_rows_show_price_change_and_sparkline_per_coin`.
- D2: At 80x24 the Market panel is below the fold (the body scrolls), so the
  live market is not in the 80x24 pictures; the live total, the live
  Portfolio lines and the live Positions header are (2.2).
- D3: At 120x36 the curve's caption leaves out the high (`today · 6 checks ·
  €1,009 → €1,021`): the 40-cell panel is too narrow, and the high is the
  first part dropped (5.4).
- D4: The demo fixtures carry no sample times, so the demo curve is drawn
  with evenly spaced samples while its axis spans range start to read time;
  on the ledger read model the curve is resampled on the time scale and the
  marks line up (5.6).
- D5: The `home_live` feed history is put in with `apply_ticks`, the same
  call the ticker worker makes, rather than by running eight 10 s polls on
  the clock (6.1).

Owned by a later issue:

- D6: Bar-close toast, row slide-in and checking spinner [issue #721].
- D7: Countdown meter and `mainnet off` in the top bar (carried over from
  #712's D10; no issue yet).

## 1. Live between bars

| # | Spec | Build | Evidence | Status |
| --- | --- | --- | --- | --- |
| 1.1 | Read the ticker between bar checks without blocking the screen | BTC, ETH and SOL read on open, then every 10 s of the console clock, in a thread worker | `test_ticker_is_read_on_open_then_every_ten_seconds_in_a_worker_thread` | match (A-1.1) |
| 1.2 | Big total and range change follow live prices | `€1,025.50 · today ▲ +€16.74 +1.66%` under `● live`, beside `booked as of 14:00 · €1,021.06` | `home_live_neon_80x24.svg` rows 5-7, `home_live_neon_120x36.svg` rows 4-10; `test_big_total_and_rows_follow_the_live_prices_beside_the_booked_values` | match (A-3.2) |
| 1.3 | Position rows follow live prices | ETH `▲ +3.13%  ▲ +€2.77`, SOL `▲ +7.91%  ▲ +€11.02`, stop room recomputed; `booked as of 14:00 · net ETH ▲ +€1.54 · SOL ▲ +€6.05` under the rows | `home_live_neon_160x45.svg`, `home_live_control_160x45.svg` (Positions rows); `test_live_row_values_the_position_at_the_live_price` | match (A-3.1) |
| 1.4 | Position detail leads with the live line | Live line above the booked figures | `test_position_detail_leads_with_the_live_line_above_the_booked_figures` | match |
| 1.5 | Waterfall, tax and Shadow test stay booked | Unchanged with the ticker | `home_live_neon_120x36.svg` (Today / This year block equals `home_ok_neon_120x36.svg`) | match (A-3.3) |
| 1.6 | No ledger write; engine never sees live prices | Ledger bytes equal before and after ticks; import contract `live-prices-are-console-only` | `test_ticks_never_write_the_ledger`, `tests/unit/test_live_import_guard.py` | match (A-4.1, A-4.2) |
| 1.7 | Money follows `pnl_visible` | No live money on a blind surface | `test_blind_surface_gets_no_live_money` | match (A-3.5) |

## 2. Market

| # | Spec | Build | Evidence | Status |
| --- | --- | --- | --- | --- |
| 2.1 | Per coin: live price, change, braille chart, next check | `BTC €53,115 · 0.00% ⣀⣀… 18:00`, ETH `€2,313`, SOL `€128.5` (EUR at the booked rate), next check per pair | `home_live_neon_160x45.svg`, `home_live_control_160x45.svg` (Market panel) | match (A-2.1); D1 for the flat change |
| 2.2 | Timeframe and result columns when wide enough | `coin tf price 15m chart result next` from 64 cells | `home_live_neon_120x36.svg`, `home_live_neon_240x60.svg`; `test_market_rows_add_timeframe_and_result_from_64_cells` | match; D2 at 80x24 |
| 2.3 | A failed coin keeps its last price | Dimmed, "as of HH:MM:SS" | `test_failed_tick_keeps_the_last_price_dimmed_with_as_of`, `test_failed_coin_keeps_its_last_price_dimmed_with_as_of` | match (A-1.2) |

## 3. Live tag

| # | Spec | Build | Evidence | Status |
| --- | --- | --- | --- | --- |
| 3.1 | Live values carry a pulsing `live` tag | `● live` on Portfolio, Positions and Market, the dot in the pulse colour | `home_live_neon_160x45.svg` (three `● live` tags); `test_tag_reads_live_with_the_pulsing_dot_colour` | match (A-3.3) |
| 3.2 | Before the first tick | Tag says it waits | `test_tag_says_it_waits_before_the_first_tick` | match |
| 3.3 | Booked values say when | `booked as of 14:00` (last bar close) | `home_live_neon_120x36.svg`; `test_booked_label_names_the_last_bar_close` | match |

## 4. Flash and paused

| # | Spec | Build | Evidence | Status |
| --- | --- | --- | --- | --- |
| 4.1 | A tick flashes moved cells, blue up, orange down | 0.9 s (`MOTION.flash`), bold void on `up` / `down` | `test_tick_flashes_the_moved_cells_and_the_flash_ends`, `test_flash_is_blue_up_and_orange_down` | match (A-3.4) |
| 4.2 | No flash with motion off | Nothing flashes | `test_motion_off_never_flashes` | match (A-3.4) |
| 4.3 | Over 60 s without a tick: paused | Live values dim, tag `live · paused HH:MM:SS`, big total dims | `test_stale_ticker_pauses_the_live_values`, `test_tag_reads_paused_with_the_last_tick_time`, `test_paused_cells_are_dim_and_never_flash` | match (A-1.3) |
| 4.4 | A ticker that never answers never breaks the UI | App keeps running | `test_a_ticker_that_never_answers_never_raises_into_the_ui` | match (A-1.2) |

## 5. Chart caption and time axis (Code Owner 2026-10-06)

| # | Spec | Build | Evidence | Status |
| --- | --- | --- | --- | --- |
| 5.1 | Caption names the range | `today`, `this week`, `this month`, `this year` | `test_caption_names_the_range_in_words` (4 ranges) | match (A-7.1) |
| 5.2 | Short history: checks and since when | `this week · 18 checks since 3 Oct · flat`; today uses the clock (`since 09:00`) | `test_short_history_says_how_many_checks_and_since_when`, `test_short_history_today_names_the_clock_time`, `test_full_history_gives_the_count_without_since` | match |
| 5.3 | Flat says "flat", else the change: start → end and the high | `this year · 2,190 checks · €4,454 → €5,932 · high €6,612`; `flat` at cent precision; no values where money may not show | `test_moving_series_gives_start_end_and_high_in_whole_euros`, `test_moving_series_without_money_has_no_values`, `test_flat_series_says_flat_even_without_money`, `test_sub_cent_wiggle_is_flat` | match |
| 5.4 | Within the panel width | Drops the high, then the values, then "since", then cuts | `home_live_neon_120x36.svg` (`today · 6 checks · €1,009 → €1,021`); `test_narrow_width_drops_the_high_then_the_values_then_since`, `test_caption_never_exceeds_its_width` (property) | match; D3 |
| 5.5 | Compact short form | `D · 6 checks · €1,009 → €1,021 · high €1,021` under the sparkline; `D · 4 checks · flat` on the empty scenario | `home_live_neon_80x24.svg`, `home_live_control_80x24.svg` row 9, `home_empty_neon_80x24.svg` row 8; `test_compact_uses_the_range_letter_and_no_since`, `test_sparkline_has_its_caption_short_when_compact` | match |
| 5.6 | Dim time axis under the braille curve | `00 06 12` (today, read time 14:12); weekdays, day numbers, month names for W, M, Y; a coarser step when marks would touch | `home_live_neon_120x36.svg`, `home_live_neon_160x45.svg`, `home_live_neon_240x60.svg` (row under the curve); `test_today_axis_marks_six_hourly`, `test_week_axis_marks_weekdays`, `test_month_axis_marks_day_numbers`, `test_year_axis_marks_month_names`, `test_year_axis_thins_to_every_other_month_when_narrow`, `test_curve_ends_with_its_time_axis_and_caption_in_dim` | match (A-7.2); D4 |
| 5.7 | No axis when three marks do not fit | Left out | `test_axis_is_left_out_when_three_marks_do_not_fit`; 80x24 has no curve, so no axis | match |

## 6. Window bar: live-feed pulse (Code Owner 2026-10-06)

| # | Spec | Build | Evidence | Status |
| --- | --- | --- | --- | --- |
| 6.1 | One glyph per recent poll, newest right, 16 wide; `●` live colour, `○` dim | `●●●●○●●●` after eight polls, the failed one hollow; `●` fill `#00f0ff` (Neon `live`) | `home_live_neon_80x24.svg`, `_120x36`, `_160x45`, `_240x60` row 0; `test_feed_pulse_draws_one_glyph_per_poll_newest_right`, `test_feed_pulse_keeps_only_the_last_sixteen_polls`, `test_polls_replace_the_countdown_with_the_feed_pulse` | match (A-7.3); D5 |
| 6.2 | Newest glyph brightens on a poll; not with motion off | Bold, half way to white, for `MOTION.flash` | `test_bright_newest_glyph_is_bold_and_whiter`, `test_motion_on_brightens_the_newest_glyph_then_dims_it`, `test_motion_off_never_brightens_the_feed` | match |
| 6.3 | No ticker: time to the next 4h check, ticking every second | `next bar 03:48:00` | `home_ok_neon_120x36.svg`, `home_empty_neon_80x24.svg` row 0; `test_next_bar_text_counts_down_to_the_check`, `test_without_a_ticker_the_bar_shows_the_next_bar_countdown` | match |
| 6.4 | Binary strip code and token deleted | `binary_strip`, `strip_tick` and the strip timer are gone | `test_motion_on_runs_the_pulse_shimmer_and_radar` (no `strip` timer) | match |
| 6.5 | Control room and night keep no window bar | First row is the tab bar | `home_live_control_120x36.svg` row 0; `test_window_bar_shows_only_in_neon` | match |

## 7. Background (Code Owner 2026-10-06)

| # | Spec | Build | Evidence | Status |
| --- | --- | --- | --- | --- |
| 7.1 | The whole window is the theme's void, every theme | Screen, `#screens`, tabs, `#body`, columns, `#mid-row`, `#top`, `#footer-row` on `$hud-void`: Neon `#0b0420`, Control room `#070b14`, night its own void | Every snapshot: no cell without a background rect, empty space filled `#0b0420` (Neon) / `#070b14` (Control room); `test_whole_window_is_the_theme_void` (3 themes) | match (A-7.4) |

## 8. Snapshot set

| # | Spec | Build | Evidence | Status |
| --- | --- | --- | --- | --- |
| 8.1 | Four sizes, frozen clock, fake ticker | `test_tui_live_screen/test_svg_snapshot_live[80x24 … 240x60].svg` | `test_svg_snapshot_live` | match (A-6.1) |
| 8.2 | Neon and Control room Home with the ticker | `home_live_neon_{80x24,120x36,160x45,240x60}.svg`, `home_live_control_{80x24,120x36,160x45,240x60}.svg` | `test_cockpit_svg_snapshot[home_live_*]` | match (A-6.2) |
