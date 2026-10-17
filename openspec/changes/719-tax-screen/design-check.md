# Design check: 719-tax-screen

What issue #719 builds, checked item by item against the approved design
(`docs/design/console-cockpit.md`: the Tax row of the screen table, the hard
rules, the reusable components, the drill-down inventory "Lots row → lot FIFO
detail → DetailPane" and the responsiveness table).

- Snapshots live in `tests/unit/entrypoints/__snapshots__/test_tui_tax_snapshots/`
  and are named `tax_<view>_<theme>_<cols>x<rows>.svg`. Views: `summary`
  (press `4`) and `lots` (press `4`, `]`, cursor on the flagged SOL lot).
  Themes: `neon` and `control` (Control room). Sizes: `80x24`, `120x36`,
  `160x45`, `240x60`.
- All snapshots run with motion off, a frozen clock (14:12 Madrid, 3 Oct 2026),
  the `ok-two-positions` demo scenario and the synthetic demo tax year.
- Colours quoted below were read from the SVG fills, not guessed.
- Status: **match** = as the spec says; **diff** = differs, with the reason or
  the follow-up issue; **later** = the spec gives this to another issue.
- Where a picture cannot show a behaviour (keys, `--vps`, money visibility)
  the test that checks it is named instead.
- Tests: `tests/unit/entrypoints/test_tui_tax.py`,
  `tests/unit/application/tax/test_screen.py`,
  `tests/unit/adapters/console/test_tax_demo.py`.

## Summary: every difference

Small, kept on purpose or cheap to change:

- D1: The lot detail is a plain `HudPanel` beside the list (below it under
  100 columns, where the Lots pane scrolls), not the shared `DetailPane` /
  `DetailScreen`, which do not exist yet (3.4) [issue #730].
- D2: Sub-tabs switch with `[` and `]` (and a click); the spec names no keys.
  Their labels are `4.1` to `4.5` so they never read as screen keys (1.2).
- D3: The Lots table's cells are plain text in the table colour: the `⚑`
  two-month mark and negative gains are not amber or orange inside the table
  (`SortableTable` renders values, not styles). The lot detail and the month
  subtotals do colour them (`#e6be28` amber, `#f07814` orange) (3.2).
- D4: "Murcia bracket meters": the savings scale is national (one scale in
  `config/tax_scales.yaml`), so the meters show that scale and the Brackets
  sub-tab says "the savings scale is national; you file as a Murcia
  resident". The regional general-income scale is issue #396 (2.3).
- D5: "Open if sold now" uses the booked prices of the open positions (the
  last bar close), not the live ticker: the spec keeps tax figures booked
  (spec "two layers"). A held coin with no booked price leaves it pending.
  The demo values its synthetic lots at its own synthetic prices (2.2).
- D6: The big estimated-tax figure (`BigNumber`) shows from 30 rows, as the
  Home total does; at 80x24 only the figure line shows (2.1).
- D7: Over `--vps` the profile sub-tab says whether a profile file exists on
  the machine the console runs on (the VPS), since the PC's file cannot be
  seen from there; it never reads it (5.3).
- D8: The Lots summary "one row per coin" is not a separate block: the coin
  facet line under the query (`coin  BTC 2 · ETH 2 · SOL 2`) gives the count
  per coin, and `coin:SOL` filters (3.1).

Owned by a later issue or MVP2:

- D9: Profile editor (`E`), on-demand pack, what-if (5.4, 4.2) [MVP2, needs
  the Code Owner's OK].
- D10: `DetailPane` / `DetailScreen` drill-down with `[` / `]` for previous /
  next item (3.4) [issue #730]; when it lands, the sub-tab keys may need to
  move.

## 1. Screen and sub-tabs

| # | Spec | Build | Evidence | Status |
| --- | --- | --- | --- | --- |
| 1.1 | Tab 4 Tax, view only in MVP1 | Tab 4 is the Tax screen; the tab bar no longer names an issue for it; nothing on it writes | `tax_summary_*`; `test_pressing_4_shows_the_summary_from_the_source` | match |
| 1.2 | Sub-tabs: Summary · Lots · Brackets · Tax pack · Your profile | A `TabBar` under the screen tabs: `4.1 Summary · 4.2 Lots · 4.3 Brackets · 4.4 Tax pack · 4.5 Your profile`; `[` `]` cycle, a click picks | `tax_summary_neon_160x45.svg` row 4; `test_brackets_cycle_the_sub_tabs`, `test_brackets_do_nothing_off_the_tax_screen` | diff (D2) |
| 1.3 | Every panel in a `HudPanel`, styled by tokens | Summary, Lots, Lot detail, Brackets, Tax pack and Your profile are `HudPanel`s labelled `computed` | all snapshots | match |

## 2. Summary

| # | Spec | Build | Evidence | Status |
| --- | --- | --- | --- | --- |
| 2.1 | Realised, fees, taxable base, est. tax (FIFO) | `realised gains +€57.24` (up blue `#468ce6`), `fees €3.71`, `taxable base €57.24`, `estimated tax (FIFO) €10.88`, with the big `€10.88` from 30 rows | `tax_summary_neon_160x45.svg`; `test_summary_shows_the_service_figures_as_given` | match (D6) |
| 2.2 | Open if sold now | `open if sold now €7.93`, then `BTC €2.80 · ETH €0.90 · SOL €4.24` and `today's tax` | `tax_summary_control_160x45.svg` | match (D5) |
| 2.3 | Murcia bracket meters with "€X left before 21%" | `Meter` per reached band plus the next one; `€5,942.76 left before 21% · now 19%` (live cyan `#58d1eb`) | `tax_summary_*`; `test_bracket_fills_split_the_base_across_the_bands` | diff (D4) |
| 2.4 | Two-month-rule flags, BTC/ETH/SOL only | Heading `Two-month rule · BTC, ETH and SOL only, never USDT`; `⚑` (amber `#e6be28`) `SOL loss −€40.60 on 2026-07-15, bought again 2026-08-05 · for your gestor` | `tax_summary_*`; `test_two_month_flags_never_name_usdt`, `test_summary_lists_the_two_month_flag_and_the_coins_it_covers` | match |
| 2.5 | Every figure from the tax estimate, labelled an estimate | Title `Tax year 2026 · estimate · shadow (no real tax due)`; a fake source's figures appear unchanged | `test_pressing_4_shows_the_summary_from_the_source`, `test_the_screen_takes_every_tax_figure_from_the_tax_service` | match |

## 3. Lots

| # | Spec | Build | Evidence | Status |
| --- | --- | --- | --- | --- |
| 3.1 | Scrolling DataTable, grouped by month with subtotals | The shared `QueryList` (`SortableTable`), newest buy first; "by month" subtotals (lots, cost, gain) of the rows the filter keeps | `tax_lots_*`; `test_month_subtotals_total_cost_and_gain_per_buy_month` | match (D8) |
| 3.2 | Filters all / open / closed and per coin; search and sort | `state:open`, `coin:SOL`, `month:2026-07`, free text; facet counts for coin, state, month; header click or `S` sorts | `tax_lots_*` rows 6-10; `test_lots_show_every_lot_and_filter_by_state` | match (D3) |
| 3.3 | Selected lot beside the list: FIFO sales that used it, proceeds / cost / fees / gain, two-month rule | `SOL lot demo-4 · closed`, `demo-5 · 2026-07-15 14:12 · 2`, `proceeds €280.00`, `cost €320.32`, `fees €0.28`, `gain −€40.60` (orange `#f07814`), `two-month rule flagged for your gestor` (amber) | `tax_lots_neon_160x45.svg`; `test_highlighting_a_lot_shows_its_fifo_detail` | match |
| 3.4 | Lots row opens a `DetailPane` | Plain `HudPanel` "Lot detail" beside the list from 100 columns, below it under 100 | `tax_lots_*_80x24.svg` vs `_120x36` | diff (D1) |
| 3.5 | Tab completes field names | Inside the query input Tab completes (`sta` → `state:`) instead of cycling screens | `test_tab_in_the_query_completes_instead_of_switching_screens` | match |

## 4. Brackets and Tax pack

| # | Spec | Build | Evidence | Status |
| --- | --- | --- | --- | --- |
| 4.1 | Brackets sub-tab | Every band: `19% … €57.24 of €6,000.00`, `21%`, `23%`, `27%`, `30% above €300,000.00`, then the room line | `test_bracket_fills_add_up_to_the_base` (property) | match (D4) |
| 4.2 | Tax pack status: "for your gestor, never submitted to AEAT" | `Tax pack 2026 · refreshed / frozen / not built yet` from the pack folder's markers, then the line | `test_pack_status_reads_the_markers`, `test_the_pack_sub_tab_shows_the_pack_state` | match |

## 5. Profile and hard rules

| # | Spec | Build | Evidence | Status |
| --- | --- | --- | --- | --- |
| 5.1 | Profile masked by default; S shows | `••••••` per figure; `S` toggles; `S` on other sub-tabs never reveals | `test_profile_is_masked_by_default`, `test_s_shows_the_profile_and_s_again_masks_it`, `test_s_off_the_profile_sub_tab_keeps_the_mask` | match |
| 5.2 | E edit = MVP2 | `E` shows the notice "Editing the profile comes in MVP2" | `test_e_on_the_profile_says_editing_waits_for_mvp2` | match |
| 5.3 | Over `--vps` only whether a profile exists; figures never reach the VPS | The remote script sets `HERMES_CONSOLE_REMOTE=1`; the console then only checks the file exists and the estimate runs without taxpayer figures | `test_profile_view_never_reads_the_file_over_vps`, `test_tax_inputs_over_vps_never_read_the_profile`, `test_a_remote_console_shows_only_whether_a_profile_exists` | match (D7) |
| 5.4 | Read-only | The screen calls only read methods; the ledger connection is `mode=ro` | `tax_inputs` | match |
| 5.5 | Money follows `pnl_visible` | Without it every amount reads `hidden`; the table's cost and gain cells are empty | `test_summary_hides_every_amount_when_money_may_not_show`, `test_lot_rows_drop_money_when_it_may_not_show` | match |
| 5.6 | Colour-blind palette: up blue, down orange, warning amber | gain `#468ce6`, loss `#f07814`, flag `#e6be28` | SVG fills above | match |
| 5.7 | Demo data synthetic for every scenario | One synthetic year and taxpayer for every `--demo` scenario, through the real tax service | `test_demo_figures_come_from_the_tax_service`, `test_demo_profile_is_synthetic` | match |

## 6. Responsiveness

| # | Spec | Build | Evidence | Status |
| --- | --- | --- | --- | --- |
| 6.1 | Compact < 100: one column, detail panes become screens | Lots pane scrolls: list (16 rows), subtotals, then the detail below | `tax_lots_neon_80x24.svg` | diff (D1) |
| 6.2 | Standard and wider: detail beside | List 3fr, detail 2fr from 100 columns | `tax_lots_*_120x36`, `_160x45`, `_240x60` | match |
