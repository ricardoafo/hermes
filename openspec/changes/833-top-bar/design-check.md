# Design check: 833-top-bar

What issue #833 (and the console half of issue #815) builds, checked item by
item against the approved design (`docs/design/console-cockpit.md`: the Home
row's top bar, "brand, SHADOW · mainnet off, live dot, last bar ✓, next bar +
countdown meter, release", and the meters line) and against items 1.4 / D10
of the issue #712 design check.

- Snapshots live in `tests/unit/entrypoints/__snapshots__/test_tui_topbar_snapshots/`
  and are named `<view>_<theme>_<cols>x<rows>.svg`. Views: `topbar` (config
  says mainnet off) at all four sizes, `topbar_on` (mainnet on) and
  `topbar_unknown` (config unread) at 160x45. Themes: `neon` and `control`
  (Control room). Sizes: `80x24`, `120x36`, `160x45`, `240x60`.
- All snapshots run with motion off, a frozen clock at 16:00 Madrid (half way
  from the 14:00 check to the 18:00 one, so the meter is half full), demo
  data (scenario ok-two-positions), version `v1.0.0-shadow.1` and release
  `679b676`.
- Colours quoted below were read from the SVG fills and the token sheets.
- Status: **match** = as the spec says; **diff** = differs, with the reason or
  the follow-up; **later** = the spec gives this to another issue.
- Tests live in `tests/unit/entrypoints/test_tui_topbar.py`.

## Summary: every difference

- D1: The badge reads `SHADOW · mainnet off` in Neon's window bar while the
  bar's brand already says "SHADOW CONSOLE", so "SHADOW" shows twice on that
  row (1.1). Kept: the spec names both, and the badge is the part that
  changes with the config.
- D2: In Neon the badge sits at the right of the window bar, before the
  clock, not right after the brand as the spec's order reads (1.2). Kept so
  the feed pulse and the next-bar countdown keep their place after the brand;
  a narrow bar drops the clock before the badge.
- D3: At 160x45 in Control room the meter does not show: the badge (21
  cells) and the demo tag leave no room for it beside the countdown; it shows
  at 240x60 and in Neon from 160x45 (3.2). Without the demo tag (a real
  ledger) there is room.
- D4: On a tight row the version line drops the release, then itself, then
  the demo tag drops its scenario name, so the heartbeat stays whole (2.3).
  At 80x24 in Neon the version is gone; at 120x36 in Control room the
  release is gone (`v1.0.0-shadow.1` only).
- D5: The countdown text keeps its seconds (`in 2h 00m 00s`); the issue
  quotes `next 18:00 in 3h 48m` (3.1). The seconds were already there and
  tick every second; dropping them is a one-line change if wanted.
- D6: The pictures of main's other tests (Home, Briefs, Signals, the
  placeholder tabs) show `mainnet ?`: those apps are built without a config
  flag, and an unread flag is drawn as unknown, never as "off" (4.3).
- D7: "last bar ✓" is the existing `checked 14:00` wording, not a tick
  glyph (1.4). Unchanged by this issue.

Owned by a later issue:

- D8: Feed latency sparkline in the top bar (spec item 6), the run selector
  (multi-run), the detail-screen breadcrumb.
- D9: The status and Telegram half of issue #815 (versioning work).

## 1. Mainnet badge

| # | Spec | Build | Evidence | Status |
| --- | --- | --- | --- | --- |
| 1.1 | `SHADOW · mainnet off` in the top bar | `SHADOW · mainnet off` in `muted` (Neon #8FA8D8, Control room #8A94A8) | `topbar_neon_160x45.svg` row 0, `topbar_control_160x45.svg` row 1; `test_mainnet_off_reads_shadow_in_the_muted_label_colour` | match (D1) |
| 1.2 | Neon: in the window bar | Window bar: `▐ HERMES // SHADOW CONSOLE  next bar 02:00:00 … SHADOW · mainnet off  16:00:00` | `topbar_neon_80x24.svg` … `topbar_neon_240x60.svg` row 0; `test_neon_window_bar_shows_the_badge_before_the_clock`, `test_neon_keeps_the_badge_off_the_status_row` | match (D2: right side) |
| 1.3 | Control room: on the status row | The status row starts with the badge | `topbar_control_*` row 1; `test_control_room_status_row_starts_with_the_badge` | match |
| 1.4 | Read from config the way serve does; never hardcoded | `config_allow_mainnet(env)` loads the config with `load_config` and returns `allow_mainnet` | `test_config_with_mainnet_unset_reads_off`, `test_config_with_mainnet_set_in_prod_reads_on`, `test_tui_command_hands_the_app_the_release_label_and_the_config_flag` | match (change.md D-1) |
| 1.5 | Mainnet on: `LIVE · mainnet ON`, down/red style, cannot be missed | `` LIVE · mainnet ON `` bold, void text on the down colour #F07814 | `topbar_on_neon_160x45.svg` row 0, `topbar_on_control_160x45.svg` row 1; `test_mainnet_on_reads_live_reversed_on_the_down_colour`, `test_control_room_status_row_shows_mainnet_on` | match |
| 1.6 | Flag unreadable: `mainnet ?` in amber | `mainnet ?` bold amber #E6BE28 | `topbar_unknown_neon_160x45.svg`, `topbar_unknown_control_160x45.svg`; `test_mainnet_unknown_reads_a_question_mark_in_amber`, `test_config_that_does_not_load_reads_unknown`, `test_neon_window_bar_shows_mainnet_unknown` | match |
| 1.7 | Never cut | A narrow window bar drops the clock, then cuts the status, never the badge | `test_narrow_window_bar_drops_the_clock_before_the_badge` | match |

## 2. Version and release

| # | Spec | Build | Evidence | Status |
| --- | --- | --- | --- | --- |
| 2.1 | Version in its release form (issue #815) | `release_label(hermes.__version__)`: `1.0.0a1` reads `v1.0.0-shadow.1` | `test_package_version_shows_in_its_release_form`, `test_tui_command_hands_the_app_the_release_label_and_the_config_flag` | match |
| 2.2 | Next to the release sha, read through the existing reader | `v1.0.0-shadow.1 · 679b676`; the release is still `read_release().name`, re-read on every redraw | `topbar_neon_160x45.svg` row 2; `test_status_row_shows_the_version_beside_the_release`, `test_version_line_names_the_release_form_and_the_release` | match |
| 2.3 | The heartbeat is never cut (issue #711) | Tight rows drop the release, then the version, then the scenario name | `topbar_neon_80x24.svg`, `topbar_control_80x24.svg`, `topbar_control_120x36.svg`; `test_compact_neon_row_drops_the_version_before_the_heartbeat`, `test_compact_control_row_drops_the_scenario_name_before_the_heartbeat`, `test_status_row_drops_the_release_before_the_version` | diff (D4): by design |

## 3. Countdown meter

| # | Spec | Build | Evidence | Status |
| --- | --- | --- | --- | --- |
| 3.1 | Next bar + countdown, text kept | `● live · checked 14:00 · next 18:00 in 2h 00m 00s` | `topbar_neon_160x45.svg` row 2 | match (D5: seconds kept) |
| 3.2 | A gradient meter for the countdown (shared `Meter`) | `▕█████░░░░░▏` after the countdown: `Meter(share, width=10, caps=True)` in the `gain` gradient (#1B3A6B → #468CE6 → #8BE9FF), empty cells in `line` | `topbar_neon_160x45.svg`, `topbar_neon_240x60.svg`, `topbar_control_240x60.svg`; `test_wide_status_row_draws_the_meter_after_the_countdown`, `test_meter_cells_use_the_gain_gradient_tokens` | match (D3: absent at Control room 160x45 with the demo tag) |
| 3.3 | Fill = elapsed share of the bar | 0, 3, 5, 9, 10, 10 cells at 0 h, 1 h 12 m, 2 h, 3 h 36 m, 4 h, 5 h after the last check | `test_meter_fill_is_the_share_of_the_bar_passed` (6 cases) | match (change.md D-4) |
| 3.4 | Nothing to count while late, down or unknown | No share, no meter | `test_meter_has_no_share_when_hermes_is_late_down_or_unknown` | match |
| 3.5 | Compact width: no meter, text kept | At 80 columns no meter; the footer reads `next 2h 00m 00s · read-only` | `topbar_neon_80x24.svg`, `topbar_control_80x24.svg` last row; `test_compact_width_drops_the_meter_and_keeps_the_countdown_text` | match |

## 4. Tokens, motion, safety and snapshots

| # | Spec | Build | Evidence | Status |
| --- | --- | --- | --- | --- |
| 4.1 | Tokens only | Badge `muted` / `down` / `void` / `warn`; meter `gain` / `line`; no literal colour | `tui_topbar.py`, `tui.py` (`_countdown_meter`) | match |
| 4.2 | Motion off is respected | Nothing new animates; the meter moves with the 1 s clock tick only | all snapshots run with motion off | match |
| 4.3 | The console only reads | The flag is read once at start from config; nothing is written | `config_allow_mainnet` | match (D6 for main's pictures) |
| 4.4 | Snapshots in both themes at the four widths | 12 files, table below | `test_tui_topbar_snapshots.py::test_topbar_svg_snapshot` | match |

Snapshot files (all in `test_tui_topbar_snapshots/`):

| View | Theme | 80x24 | 120x36 | 160x45 | 240x60 |
| --- | --- | --- | --- | --- | --- |
| off | Neon | `topbar_neon_80x24.svg` | `topbar_neon_120x36.svg` | `topbar_neon_160x45.svg` | `topbar_neon_240x60.svg` |
| off | Control room | `topbar_control_80x24.svg` | `topbar_control_120x36.svg` | `topbar_control_160x45.svg` | `topbar_control_240x60.svg` |
| on | Neon / Control room | | | `topbar_on_neon_160x45.svg`, `topbar_on_control_160x45.svg` | |
| unknown | Neon / Control room | | | `topbar_unknown_neon_160x45.svg`, `topbar_unknown_control_160x45.svg` | |

Regenerated on main (top rows only; every other row compared equal):
`test_tui/` (5), `test_tui_briefs/` (8), `test_tui_home_screen/` (8),
`test_tui_live_screen/` (4), `test_tui_signals/` (8),
`test_tui_snapshots/` (32: `home_ok`, `home_empty`, `tab3_placeholder`,
`home_live`; `theme_picker` unchanged).
