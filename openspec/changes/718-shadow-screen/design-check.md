# Design check: 718-shadow-screen

What issue #718 builds, checked item by item against the approved design
(`docs/design/console-cockpit.md`: the Shadow test row of the screen table,
the hard rules, the tokens, the reusable components table, the
responsiveness table and the legible big-number rule of 2026-10-06), and
against ADR-021 §2 and §3 for day 0.

- Snapshots live in `tests/unit/entrypoints/__snapshots__/test_tui_snapshots/`
  and are named `<view>_<theme>_<cols>x<rows>.svg`. Views used here:
  `shadow_counting` (scenario ok-two-positions: a day-0 record, day 12) and
  `shadow_warmup` (scenario empty: no record). Themes: `neon` and `control`
  (Control room). Sizes: `80x24`, `120x36`, `160x45`, `240x60`.
- All snapshots run with motion off, a frozen clock (14:12 Madrid) and
  synthetic demo data.
- Colours quoted below were read from the SVG fills, not guessed.
- Status: **match** = as the spec says; **diff** = differs, with the reason or
  the follow-up; **later** = the spec gives this to another issue.
- Tests live in `tests/unit/entrypoints/test_tui_shadow.py`,
  `tests/unit/application/console/test_shadow_screen.py`,
  `tests/unit/adapters/console/test_shadow_records.py` and
  `test_ledger_read_model_home.py`.

## Summary: every difference

Small, kept on purpose or cheap to change:

- D1: Day 0 is the `hermes shadow-start` record (ADR-021 §2-§3, the
  coordinator's update of 2026-10-07), not the capital seed. Without a record
  the hero reads "warm-up · not counting" and the checks are all pending. The
  spec row predates ADR-021 and does not name a warm-up state (1.4).
- D2: The bottom 90-day line on every tab still reads the seed-based
  `shadow_test()` view, so with a seed and no record it says "day N of 90"
  while tab 5 says warm-up. Follow-up needed (no issue yet; fits issue #731's
  run-aware work).
- D3: Standard width (100-159) stacks the four panels in one column; the
  spec's "two columns, as today" applies from 160 here, because the check
  details wrap in a half-width column (1.2).
- D4: The kill-trigger check stays pending ("not run by serve yet · issue
  #796") because `hermes serve` records no verdict yet (2.4).
- D5: "Lab notebook signed" passes on an operator `shadow` entry dated day 90
  or later, and fails on a logged §11 deviation after day 0. The spec does not
  say how "signed" is read; this is the build's reading (2.5).
- D6: The month ticks show month names when counting and day marks (30d,
  60d, 90d) during the warm-up, when no calendar month applies (1.3).

Owned by a later issue:

- D7: The day-0 record itself and the warm-up archive [issue #797].
- D8: A recorded kill-trigger check verdict [issue #796].

## 1. Hero

| # | Spec | Build | Evidence | Status |
| --- | --- | --- | --- | --- |
| 1.1 | Big "N / 90" | `BigNumber` with `12 / 90`; `/` drawn as a one-cell `╱` | `shadow_counting_neon_160x45.svg`; `test_counting_hero_shows_the_big_day_and_the_record`, `test_day_figure` | match |
| 1.2 | Legible big number: one colour, ≥ 7:1, never dimmed | Digits one bold `hero-digits` fill: `#c8fbff` in Neon, `#e6ecf5` in Control room (17.0:1 and 15.5:1 on `panel`, measured in issue #712); the same component, so night never dims it | `shadow_counting_neon_160x45.svg`, `shadow_counting_control_160x45.svg`; `test_counting_hero_shows_the_big_day_and_the_record` (style `bold #C8FBFF`) | match |
| 1.3 | Gradient progress bar with month ticks | The shared run bar (`run_bar`, brand gradient) with the share, then `╵Oct ╵Nov ╵Dec`; warm-up shows `╵30d ╵60d 90d╵` | `shadow_counting_neon_160x45.svg`, `shadow_warmup_neon_160x45.svg`; `test_hero_bar_is_the_shared_run_bar`, `test_month_ticks_mark_each_first_of_a_month`, `test_run_bar_fill_is_monotonic_in_the_day` | match (D6) |
| 1.4 | Day 0 follows the Code Owner's pick | One switch, `DAY_ZERO_SOURCE`, default the shadow-start record; day 0 line `day 0 21 Sep 2026 02:00 · day 90 on 20 Dec 2026` | `test_the_switch_defaults_to_the_shadow_start_record`, `test_day_zero_follows_the_chosen_source` | diff (D1) |
| 1.5 | (warm-up) | `WARM-UP · NOT COUNTING`, warm-up days since the seed in dim, an empty bar, "Day 0 starts with hermes shadow-start (issue #797) …" | `shadow_warmup_*`; `test_warm_up_has_no_big_number_and_says_not_counting` | match (D1) |
| 1.6 | Compact and short terminals | Under 100 columns or 30 rows a bold `day 12 / 90` line replaces the big number, as Home does | `shadow_counting_neon_80x24.svg`; `test_hero_without_the_big_number_leads_with_a_bold_day_line` | match |

## 2. Certification checks

| # | Spec | Build | Evidence | Status |
| --- | --- | --- | --- | --- |
| 2.1 | 90 days in a row | Fails on the first local day with no bar check after day 0; passes at day 90 | `test_days_in_a_row` (4 cases) | match |
| 2.2 | M-02 slippage median ≤ 30 bps | Median of the live-book `FillQualityRecorded.drift_bps` since day 0, judged at day 90; "pending · not enough fills" without one | `test_m02_slippage_median` (5 cases), `test_shadow_evidence_reads_checks_drifts_and_kills_since_day_zero` | match |
| 2.3 | No kill trigger fired | A recorded `StrategyPaused` since day 0 fails; none by day 90 passes | `test_no_kill_trigger_fired` | match |
| 2.4 | Kill-trigger check clean | Pending until serve records a verdict | `test_kill_trigger_check_clean` | diff (D4) |
| 2.5 | Lab notebook signed | Closing operator entry from day 90 passes; a §11 deviation fails | `test_notebook_signed_passes_on_a_closing_operator_entry`, `test_notebook_logged_deviation_fails`, `test_notebook_pending` | match (D5) |
| 2.6 | Pass / fail / pending only, no number | Glyph, name, state word, short words; pass `up` #468ce6, fail `down` #f07814, pending `muted` | `test_check_rows_use_the_data_colours_and_only_three_words`, `test_every_check_answers_only_pass_fail_or_pending` | match |
| 2.7 | "mechanical: pass or fail stands" | Under the rows, dim | `shadow_counting_neon_160x45.svg` | match |

## 3. Frozen settings, Day 0 and the notebook

| # | Spec | Build | Evidence | Status |
| --- | --- | --- | --- | --- |
| 3.1 | Frozen settings | `swing_parameters()` (what serve trades and certification fingerprints), shown read-only | `test_frozen_settings_are_what_serve_trades_with` | match |
| 3.2 | (day-0 record) | Run id, day 0, release, the seven Ready rules with ✓ / ✗ / ? | `shadow_counting_*`; `test_fixture_record_parses_to_the_shown_fields` | match (ADR-021 §2) |
| 3.3 | Lab notebook entries | Newest first, one line each with an ellipsis, `+N older`; "not available here" when the release has none | `test_notebook_missing_says_not_available_here`, `test_notebook_file_reads_its_entries` | match |

## 4. Layout per breakpoint

| # | Spec | Build | Evidence | Status |
| --- | --- | --- | --- | --- |
| 4.1 | Compact < 100 | One column, body scrolls | `shadow_*_80x24.svg` | match |
| 4.2 | Standard 100-159 | One column under the hero | `shadow_*_120x36.svg` | diff (D3) |
| 4.3 | Wide 160-219 / ultra ≥ 220 | Hero full width; checks + Day 0 left, settings + notebook right | `shadow_*_160x45.svg`, `shadow_*_240x60.svg` | match |
| 4.4 | Height | Notebook 3 / 5 / 10 entries by height class | `shadow_counting_neon_240x60.svg` | match |

## 5. Hard rules and components

| # | Spec | Build | Evidence | Status |
| --- | --- | --- | --- | --- |
| 5.1 | Read-only | No write anywhere; the ledger is opened `mode=ro` | `test_reading_the_home_views_leaves_the_file_unchanged` (existing), code review | match |
| 5.2 | Shadow test stays booked-only; no P&L | No money view is read; no `€` or `$` on any scenario | `test_the_shadow_screen_shows_no_money` (4 scenarios) | match |
| 5.3 | Reuse `HudPanel`, `BigNumber`, `Meter`, tokens | Four `HudPanel`s; the shared `BigNumber`; `run_bar` wraps the one `Meter` | code | match |
| 5.4 | Colour-blind palette | Pass blue, fail orange, "confirm by hand" amber | `test_check_rows_use_the_data_colours_and_only_three_words` | match |
| 5.5 | No new library | Textual 8 + Rich 15 only | `pyproject.toml` unchanged | match |
