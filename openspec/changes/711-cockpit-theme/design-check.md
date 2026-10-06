# Design check: 711-cockpit-theme

What issue #711 builds, checked item by item against the approved design
(`docs/design/console-cockpit.md`, Look A plus round 3).

- Snapshots live in `tests/unit/entrypoints/__snapshots__/test_tui_snapshots/`
  and are named `<view>_<theme>_<cols>x<rows>.svg`. Views: `home_ok`
  (scenario ok-two-positions), `home_empty` (scenario empty),
  `tab2_placeholder` (Signals) and `theme_picker`. Themes: `neon` and
  `control` (Control room). Sizes: `80x24`, `120x36`, `160x45`, `240x60`.
- All snapshots run with motion off, a frozen clock (14:12 Madrid) and demo
  data. With motion off the live dot shows its first pulse step (full `live`)
  and the binary strip shows tick 0.
- Status: **match** = as the spec says; **diff** = differs, with the reason
  or the follow-up issue; **later** = the spec gives this to another issue.
- The test that checks each behaviour is named after the snapshot where a
  picture alone cannot show it (keys, timers, night).

## 1. Tokens and colours

| # | Spec | Build | Evidence | Status |
| --- | --- | --- | --- | --- |
| 1.1 | Base sheet: void #070B14, panel #0C1424, panel-focus #0F1B33 + border #2C4F86, overlay #18233A, line #1A2740, live #58D1EB, ai #9D65FF, up #468CE6, down #F07814, warn #E6BE28, text/muted/dim #E6ECF5/#8A94A8/#5D6780 | `BASE` in `tui_theme.py` has exactly these values | `home_ok_control_160x45.svg` (panel #0C1424, void #070B14, focused Positions #0F1B33) | match |
| 1.2 | Gradients brand, gain, loss, ai | Same stops in `BASE.brand/gain/loss/ai_gradient` | `test_tui_theme.py` (no screen draws them yet) | match (drawn from issue #712) |
| 1.3 | Neon overrides: void #0B0420, panel #120731, panel-focus #170A3D, line #4A2399, live #00F0FF, ai #FF3DF0, frame #8A5CFF, text #C8FBFF, muted #8FA8D8, dim #5A5F9A; chrome #FF3DF0→#8A5CFF→#00F0FF; brand #00F0FF→#8A5CFF→#FF3DF0 | `NEON` has exactly these values | `home_ok_neon_160x45.svg` | match |
| 1.4 | Data colours stay semantic in both themes: up blue, down orange, warn amber; no neon on the data | Rich content is drawn from `BASE` in Neon and Control room (`data_tokens`) | `home_ok_neon_160x45.svg`: gains #468CE6, stop-near amber #E6BE28; `home_ok_control_160x45.svg` same | match |
| 1.5 | Tokens defined once in a Textual `Theme(..., variables=...)` | Three themes `hermes-neon`, `hermes-control-room`, `hermes-night`, each with `hud-*` variables; TCSS reads only `$hud-*` | `test_tui_theme.py` | match |
| 1.6 | `frame` and `chrome` are Neon tokens only | The base sheet also defines `frame` #2C4F86 and `chrome` #58D1EB→#468CE6→#9D65FF so Control room can draw the same chrome | `home_ok_control_160x45.svg` (title bars) | diff: build-defined values for Control room; the spec gives none. Needs the Code Owner's look |
| 1.7 | Neon `panel-focus` border not given | Neon `focus_border` = #8A5CFF (the Neon `frame`) | `home_ok_neon_160x45.svg` (Positions rule) | diff: build choice, spec silent |
| 1.8 | Neon `overlay` not overridden | Neon keeps the base overlay #18233A, so the picker is a navy box on indigo | `theme_picker_neon_120x36.svg` | match (follows the spec literally); worth a look by eye |
| 1.9 | — | The DEMO DATA tag is `black on yellow` and exports as pure #FFFF00, not the `warn` token | `home_ok_neon_80x24.svg`, `home_ok_control_80x24.svg` | diff: pre-#711 styling kept; small follow-up (tokenise the tag) |

## 2. Neon chrome

| # | Spec | Build | Evidence | Status |
| --- | --- | --- | --- | --- |
| 2.1 | Gradient window bar "▐ HERMES // SHADOW CONSOLE" | Row 0: the title on a per-cell chrome gradient, clock at the right | `home_ok_neon_160x45.svg` row 0 | match |
| 2.2 | Ticking binary strip | 16-digit strip after the title, one step per second; off at night and with motion off | snapshot shows tick 0; `test_motion_on_runs_the_pulse_and_the_strip`, `test_entering_night_slows_the_pulse_and_stops_the_strip` | match |
| 2.3 | Magenta→violet→cyan title bars | Panel titles drawn per cell on the chrome gradient | `home_ok_neon_160x45.svg` (PORTFOLIO, POSITIONS, MARKET WATCH, ACTIVITY) | match |
| 2.4 | Cyan/magenta bracket corners | `┏━` in `live` top-left, `━┛` in `ai` bottom-right | `home_ok_neon_160x45.svg` | match |
| 2.5 | Neon only restyles the chrome; Control room is calmer | Control room draws the same window bar and binary strip in calmer colours | `home_ok_control_160x45.svg` row 0 | diff: the spec names the window bar and strip under Neon; the build shows them in Control room too. Needs the Code Owner's call (hide them in Control room, or keep) |
| 2.6 | Aurora rule: 1-row gradient under the top bar, drifting | Not built | — | diff: no issue owns it in the build order; proposed for issue #721 (motion polish) |
| 2.7 | No scanlines, blur or glow | None | all snapshots | match |

## 3. Panel frames and labels (`HudPanel`)

| # | Spec | Build | Evidence | Status |
| --- | --- | --- | --- | --- |
| 3.1 | One `HudPanel` for every panel (frame, title bar, subtitle, focus, AI variant) | Every home card and every placeholder screen sits in a `HudPanel` | `home_ok_neon_120x36.svg`, `tab2_placeholder_neon_120x36.svg`; `test_every_home_card_sits_in_a_computed_hud_panel` | match |
| 3.2 | Titles UPPERCASE with the accent | Upper case on the chrome gradient | `home_ok_neon_160x45.svg` | match |
| 3.3 | `border_subtitle` holds key hints (e.g. D W M Y) | Bottom edge: `D W M Y range` (Portfolio), `↑↓ select · ↵ detail` (Positions), in `dim` | `home_ok_neon_160x45.svg` | match |
| 3.4 | Label each panel: `computed` or `✦ AI supervisor` | `computed` right-aligned on the top edge; Briefs uses the AI variant | `home_ok_neon_160x45.svg`; `test_briefs_screen_is_the_ai_variant` (no Briefs snapshot) | match |
| 3.5 | Focused panel: panel-focus surface, title becomes a cyan chip | Positions (focus on Home) gets `panel_focus` and a solid `live` title chip | `home_ok_neon_160x45.svg`, `home_ok_control_160x45.svg` | match |
| 3.6 | Look A base: `border: round` panels | Custom top and bottom edge rows; no side borders | all `home_*` snapshots | diff: by design (change.md D-3, the corner-bracket signature element); the spec names `border: round` only as the fallback |
| 3.7 | Panel name "Market" | Panel title "Market watch", and the renderer prints its own "Market watch" heading inside it | `home_ok_neon_160x45.svg` (heading twice) | diff: duplicate heading; follow-up in issue #713 (Market panel rebuild) |
| 3.8 | Empty positions: radar spinner + "hermes is watching BTC ETH SOL and will look again at HH:MM" | "No open shadow positions." and the `↑↓ select · ↵ detail` hint still shows | `home_empty_neon_120x36.svg` | later: issue #712 (designed empty state) |

## 4. Tab bar and navigation

| # | Spec | Build | Evidence | Status |
| --- | --- | --- | --- | --- |
| 4.1 | `1 Home · 2 Signals · 3 Positions · 4 Tax · 5 Shadow test · 6 Briefs · 7 System · 8 Logs · 9 History` | Tabs 1-8; no 9 History | `home_ok_neon_120x36.svg` row 1 | diff: History is MVP2 with no issue yet, so no tab and no key 9 (`test_number_keys_one_to_eight_each_name_a_screen`) |
| 4.2 | Compact (< 100 cols): numbers only | `1   2   3 …` at 80 columns | `home_ok_neon_80x24.svg` row 1 | match |
| 4.3 | Number keys jump to a screen | 1-8 switch the screen body and the tab highlight | `tab2_placeholder_*`; `test_each_number_key_selects_its_screen_in_the_switcher_and_the_tab_bar` | match |
| 4.4 | Tab / Shift+Tab cycle | Forward and backward over all eight, wrapping; ignored under a dialog | `test_tab_walks_every_screen_in_order_and_wraps_home`, `test_shift_tab_walks_every_screen_backwards`, `test_tab_does_nothing_while_the_theme_picker_is_open` | match |
| 4.5 | Ctrl+P: command palette (screens and commands) | Lists "Go to <screen>" for 1-8 and the three hermes themes; Textual's own theme list is removed | `test_palette_offers_screens_and_the_three_themes_only`, `test_palette_search_goes_to_a_screen`, `test_palette_search_switches_and_saves_the_theme` | match |
| 4.6 | Esc goes back from detail panes | Esc closes the detail pane first, then returns to the previous tab; closes help, the palette and the picker; does nothing on a fresh Home | `test_escape_closes_the_detail_pane_before_leaving_home`, `test_escape_leaves_the_help_screen_and_keeps_the_tab`, `test_escape_closes_the_palette_and_keeps_the_screen`, `test_escape_on_home_with_no_history_changes_nothing` | match; going back through tab history is an addition the spec does not mention |
| 4.7 | D W M Y set the range on every screen with one | D/W/M/Y in either case set the Home range from any tab; ignored under help | `test_range_key_sets_the_range_from_another_screen` (7 cases), `test_range_keys_are_ignored_under_the_help_screen` | match |
| 4.8 | Screens 2-8 | Placeholders: "<name> · coming next · issue #N" | `tab2_placeholder_neon_120x36.svg`; `test_every_unbuilt_screen_names_its_issue` | later: issues #714-#720 |

## 5. Footer chips

| # | Spec | Build | Evidence | Status |
| --- | --- | --- | --- | --- |
| 5.1 | Reverse-video key chips | Each key reversed in `muted`, label after it | `home_ok_neon_160x45.svg` last row; `test_footer_keys_are_reverse_video_chips` | match |
| 5.2 | Chips `D W M Y`, `↑↓`, `↵`, `L`, `?`, `^P`, `Q` | `D W M Y range`, `1-8 screens`, `↵ detail`, `L logs`, `T theme`, `? help`, `^P palette`, `Q quit`, then `read-only` | `home_ok_neon_160x45.svg` | diff: `↑↓` moved to the Positions subtitle to save footer room; `1-8` and `T` added for the keys #711 adds |
| 5.3 | — | Compact: chips without labels except `range` and `quit`; `L` dropped; the countdown moves into the footer (`next 3h 48m 00s · read-only`) | `home_ok_neon_80x24.svg` | diff: build choice so the 80-column footer fits; `L` still works and is listed in help |

## 6. Live dot and status row

| # | Spec | Build | Evidence | Status |
| --- | --- | --- | --- | --- |
| 6.1 | Live dot in the accent colour, fixes "live is gray" | OK dot is `bold <live>` of the shown theme: #00F0FF Neon, #58D1EB Control room, #3B8FA1 night | `home_ok_neon_120x36.svg`, `home_ok_control_120x36.svg`; `test_ok_dot_takes_each_themes_live_colour` (3 cases), `test_ok_dot_on_the_empty_scenario_is_live_coloured` | match |
| 6.2 | The text can stay muted | The words after the dot are `dim` | same snapshots; `test_ok_dot_is_live_coloured_and_the_words_stay_dim` | diff (small): `dim`, not the `muted` token; kept from the pre-#711 OK line |
| 6.3 | Pulse, ~1.6 s period, 3-4 colour steps | 4 steps of 0.4 s (live, fading, faint, fading); 3.2 s at night; none with motion off | `test_pulse_cycles_the_dot_through_four_colours`, `test_motion_off_runs_no_effect_timer` | match |
| 6.4 | Late: amber ◐; down: red row, as today | Late `yellow` (= warn amber, also at night); down `bold red` row; neither shows ● | `test_late_dot_stays_amber`, `test_down_row_stays_red`, `test_late_amber_keeps_the_day_warn_colour_at_night`, `test_late_and_down_have_no_live_dot`; down picture in `test_tui/test_svg_snapshot_down_80x24.svg` | match |
| 6.5 | Checking: braille spinner + "checking 18:00 bar…" | Not built | — | later: issue #721 |
| 6.6 | Top bar: brand, `SHADOW · mainnet off`, live dot, last bar ✓, next bar + countdown meter, release | Window bar says `SHADOW CONSOLE`; status row: DEMO tag, release (when known), `● live · checked 14:00 · next 18:00`, countdown as text `in 3h 48m 00s`, theme chip | `home_ok_neon_160x45.svg` rows 0-2 | diff: no `mainnet off` text and no countdown meter. Meter: issue #712. `mainnet off`: not owned by any issue; proposed for issue #712 |
| 6.7 | Terminal tab title, e.g. `hermes ● live · €4,456 · next 16:00` | `hermes ● live · next 18:00` (OSC 2), late and down variants | `test_terminal_title_names_each_state`, `test_app_writes_the_title_through_its_driver_once` | diff: no portfolio total in the title. change.md R-3 fixed it without the total and gives no reason; needs the Code Owner's call |
| 6.8 | Optional terminal bell on down/late/error, off by default | Not built | — | diff: not in change.md scope and no issue; proposed for issue #721 |

## 7. Theme switch and night mode

| # | Spec | Build | Evidence | Status |
| --- | --- | --- | --- | --- |
| 7.1 | Two themes, Neon by default | Neon and Control room; default choice Auto = Neon by day | `home_ok_neon_*`, `home_ok_control_*`; `test_auto_is_neon_by_day` | match |
| 7.2 | Night 23:00-08:00 switches to Control room | Auto switches to `hermes-night` (Control room dimmed, darker up/down, warn unchanged) at 23:00 and back to Neon at 08:00 | `test_auto_switches_to_night_at_23`, `test_auto_leaves_night_at_eight`, `test_night_data_palette_is_the_darker_one` | diff: night is a dimmed Control room, not Control room itself (change.md D-2, the Code Owner's theme chip comment: dimmed at night, warnings fully bright). No night snapshot: the snapshot set is the two day themes |
| 7.3 | Night: motion slows, colours dim, alerts still show | Pulse 3.2 s, strip off, warn stays #E6BE28 | `test_night_runs_only_the_slow_pulse`, `test_entering_night_slows_the_pulse_and_stops_the_strip`, `test_late_amber_keeps_the_day_warn_colour_at_night` | match |
| 7.4 | A hand-picked theme | Neon or Control room picked by hand stays across 23:00 | `test_a_hand_picked_theme_ignores_the_night`, `test_a_hand_picked_control_room_ignores_the_night` | match |
| 7.5 | Theme lives in Settings (MVP2) | MVP1: `T` picker (Neon / Control room / Auto), theme chip on the status row, Ctrl+P theme commands, `--theme`; saved in `~/.config/hermes/console.toml` | `theme_picker_neon_*`, `theme_picker_control_*`; `test_t_opens_the_picker_and_the_pick_is_saved`, `test_picker_highlights_the_current_choice`, `test_picking_neon_at_night_keeps_neon_and_saves_it`, `test_picking_a_theme_repaints_the_chrome_in_its_tokens`, `test_escape_closes_the_picker_without_a_change` | match (the picker and chip come from the Code Owner's theme chip comment on issue #711) |
| 7.6 | — | In the SVG export the picker screen covers the console (only the picker shows) | `theme_picker_neon_120x36.svg` | check by eye in a real terminal; not a spec item |
| 7.7 | Motion off via `HERMES_CONSOLE_MOTION=off` | Env var honoured; pulse and strip stop; clock and countdown keep ticking | `test_motion_env_off_reaches_the_app`, `test_motion_off_runs_no_effect_timer`, `test_clock_and_countdown_tick_every_second` | match |

## 8. Breakpoints

| # | Spec | Build | Evidence | Status |
| --- | --- | --- | --- | --- |
| 8.1 | Breakpoint tokens with Textual's `HORIZONTAL_BREAKPOINTS` / `VERTICAL_BREAKPOINTS`: compact < 100, standard 100-159, wide 160-219, ultra ≥ 220; short < 30, tall ≥ 30, towering ≥ 45 | Classes `-compact/-standard/-wide/-ultra` and `-short/-tall/-towering` set on the screen | `test_tui_theme.py` | match |
| 8.2 | Compact: one column, numbers-only tabs, detail panes become screens | One column, numbers-only tabs, Enter opens the detail full screen | `home_ok_neon_80x24.svg` | match |
| 8.3 | Compact: — | The home body scrolls; its scrollbar thumb shows inside the Portfolio panel, and Market and Activity are below the fold | `home_ok_neon_80x24.svg`, `home_ok_control_80x24.svg` (the `▅▅` at the right) | diff: layout of the 80x24 home is issue #712 |
| 8.4 | Standard 100-159: two columns | Two columns from 120 (100-119 stay one column) | `home_ok_neon_120x36.svg` | diff: change.md D-6 (the positions table wraps badly at 100-119 today); issue #712 |
| 8.5 | Wide 160-219: the mock layout | Same two-column layout as standard, wider | `home_ok_neon_160x45.svg` | later: issue #712 |
| 8.6 | Ultra ≥ 220: third column (Signals heat grid, live log tail) | Same two-column layout | `home_ok_neon_240x60.svg` | later: issue #712 (after #714 and #716) |
| 8.7 | Height < 30 hides the curve; ≥ 45 doubles the chart heights | The big total shows from 30 rows; no braille curve yet; no doubling | `home_ok_neon_80x24.svg` (no big total) vs `home_ok_neon_120x36.svg` (big total) | later: issue #712 |
| 8.8 | Snapshot tests at 80x24, 120x36, 160x45, 240x60 | 32 snapshots: 4 views × 2 themes × 4 sizes | `test_tui_snapshots.py` | match |

## Differences, in one list

Needs the Code Owner's call:

- D1: Window bar and binary strip also show in Control room (2.5).
- D2: Terminal title has no portfolio total (6.7).
- D3: Control room `frame` and `chrome` values, and Neon's focus border, are build-chosen (1.6, 1.7).

No issue owns them yet (proposed home in brackets):

- D4: Aurora rule (2.6) [issue #721].
- D5: `mainnet off` in the top bar (6.6) [issue #712].
- D6: Terminal bell (6.8) [issue #721].
- D7: DEMO DATA tag uses raw yellow, not the `warn` token (1.9) [small follow-up].

Owned by a later issue:

- D8: Market panel heading shown twice (3.7) [issue #713].
- D9: Designed empty state (3.8), countdown meter (6.6), per-breakpoint home layout, 80x24 scroll, two columns from 100, third column, curve and chart heights (8.3-8.7) [issue #712].
- D10: Checking spinner (6.5) [issue #721].
- D11: Screens 2-8 (4.8) [issues #714-#720]; History tab and key 9 (4.1) [MVP2].

Deliberate and recorded:

- D12: Custom edge rows instead of `border: round` (3.6, change.md D-3).
- D13: Night is a dimmed Control room (7.2, change.md D-2).
- D14: Footer: `↑↓` lives on the Positions subtitle; `1-8` and `T` added; compact footer drops labels and `L` (5.2, 5.3).
- D15: OK words are `dim`, not `muted` (6.2).
