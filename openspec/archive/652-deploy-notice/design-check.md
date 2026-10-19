# Design check: 652-deploy-notice

What issue #652 builds, checked item by item against the approved design
(`docs/design/console-cockpit.md`, Refresh model: "After a deploy, show a
'new version, restart the console' notice (issue #652)").

## Differences, in one list

Deliberate and recorded:

- D1: The notice text is "new version deployed · press R to restart", not the
  spec's wording "new version, restart the console" (1.1). The spec gives the
  meaning, not the copy; the build names the key that does it.
- D2: The notice is a persistent row, not a toast (1.2). A toast fades; the
  console keeps running old code until it restarts, so the notice stays.
- D3: `R` restarts the console process only (2.1). The 2026-10-05 decision that
  console actions changing the VPS (bootstrap, restart, rollback) wait for MVP2
  is about the services, not this read-only process; `R` touches neither the
  ledger nor any service.
- D4: On Windows `R` exits with "start hermes again to use it" instead of
  re-executing (2.3). The console runs on the VPS (Linux), where it re-execs.
- D5: A missing (`unknown`) or failing release read is never a change (1.5).

Owned by a later issue:

- D6: The System screen's MVP2 action keys `B/R/K` (issue #717) will need `R`
  scoped to that screen, or this notice's key moved, when they land (2.4).

## Notes

- Snapshots live in `tests/unit/entrypoints/__snapshots__/test_tui_snapshots/`
  and are named `notice_new_version_<theme>_<cols>x<rows>.svg`. Themes: `neon`
  and `control` (Control room). Sizes: `80x24`, `120x36`, `160x45`, `240x60`.
- All snapshots run with motion off, a frozen clock (14:12 Madrid), the
  `ok-two-positions` demo scenario and a fake release reader that changes from
  `1.0.0-shadow.1` to `1.0.0-shadow.2` after the first draw.
- Status: **match** = as the spec says; **diff** = differs, with the reason
  or the follow-up issue; **later** = the spec gives this to another issue.

## 1. The notice

| # | Spec | Build | Evidence | Status |
| --- | --- | --- | --- | --- |
| 1.1 | "new version, restart the console" | "new version deployed · press R to restart" | `notice_new_version_neon_80x24.svg`; `test_notice_shows_after_the_release_changes` | diff (D1): wording names the key |
| 1.2 | A notice after a deploy | A persistent one-row notice; it stays until the console restarts or quits | `notice_new_version_*`; `test_notice_shows_after_the_release_changes` | diff (D2): row, not toast |
| 1.3 | Warning = amber (colour-blind palette) | Text in `$hud-warn` (#E6BE28, the same in Neon, Control room and night), bold, on `$hud-panel`, with a `$hud-warn` edge on the left; no raw colour in the code | `notice_new_version_neon_160x45.svg`, `notice_new_version_control_160x45.svg`; `test_notice_is_drawn_in_the_theme_warn_token` | match |
| 1.4 | Every panel and row in the HudPanel style | Same panel surface as `HudPanel`; one row, no frame, so it costs one row of the screen | `notice_new_version_*` | match (a full frame would cost three rows at 80x24) |
| 1.5 | "After a deploy" | The first known release name is the baseline; a different name on the 5 s refresh raises the notice; `unknown` or a reader that raises shows nothing | `test_unknown_release_is_not_a_change`, `test_reader_error_neither_crashes_nor_shows_the_notice` (2 cases), `test_no_notice_when_the_release_is_unchanged` | match; D5 |
| 1.6 | — | The notice sits under the status row (and under the down row when the engine is down), above the screens, so every tab shows it | `test_notice_and_restart_work_on_another_tab` | match |
| 1.7 | Neon window bar and status row stay as they are | At 80x24 Neon: window bar row 0, tabs 1, status row 2, notice 3, screens from 4. Control room has no window bar, so the notice is row 2 | `notice_new_version_neon_80x24.svg`, `notice_new_version_control_80x24.svg`; `test_notice_row_sits_below_the_neon_window_bar_and_the_status_row` | match |
| 1.8 | Refresh model: poll every 5 s | The check rides the existing 5 s redraw; no new timer or thread | `test_notice_shows_after_the_release_changes` | match |

## 2. The R key

| # | Spec | Build | Evidence | Status |
| --- | --- | --- | --- | --- |
| 2.1 | "restart the console" | `R` (either case) quits Textual cleanly, then `run_home` re-execs the same interpreter with the same arguments | `test_r_after_a_change_asks_for_a_restart_and_quits`, `test_restart_execs_the_interpreter_with_the_original_argv` | match; D3 |
| 2.2 | — | Without the notice `R` does nothing; under help, logs or the theme picker (any screen above the console) it does nothing | `test_no_notice_when_the_release_is_unchanged`, `test_reader_error_neither_crashes_nor_shows_the_notice`, `test_r_under_the_help_screen_does_not_restart` | match |
| 2.3 | — | Windows: prints "New version deployed: start hermes again to use it." and exits | `test_windows_exits_with_a_message_instead_of_exec` | diff (D4) |
| 2.4 | System screen MVP2 keys `B/R/K` (issue #717) | `R` is a console-wide key today | — | later (D6): issue #717 |
| 2.5 | `?` is help | Help lists "R restart onto a new version (only while its notice shows)"; no footer chip, since `R` only matters while the notice shows | help screen | match |
