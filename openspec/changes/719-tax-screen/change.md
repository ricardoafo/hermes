# Change: 719-tax-screen

**SDD change**: `719-tax-screen`
**Date**: 2026-10-07
**Status**: Applied
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/719-tax-screen/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

Screen 4 of the Cockpit console (design spec `docs/design/console-cockpit.md`,
the Tax row): the Tax screen, view only in MVP1, replaces the tab 4
placeholder. Five sub-tabs: Summary, Lots, Brackets, Tax pack and Your
profile. Every figure comes from the existing tax service: a new
`TaxScreenService` asks `TaxService.estimate` for the year and adds what the
screen lists beside it (FIFO lots with the sales that used them, the year's
fees, the savings bracket fills), and the console only lays it out. Lots reuse
the shared `QueryList` from issue #729. The main risk is taxpayer data: the
profile is masked by default and, over `--vps`, never read at all.

## Scope

### In

- `src/hermes/application/tax/estimate.py`: `TaxYearEstimate` also carries
  `savings_base` (the taxable savings base after netting) and `two_month`
  (the structured flags behind the flag lines; traded coins only, never the
  quote currency).
- `src/hermes/application/tax/screen.py` (new): `TaxScreen`, `LotView`,
  `LotSale`, `BracketFill`, `MonthSubtotal`, `TaxpayerProfileView`,
  `TaxScreenSource` (protocol) and `TaxScreenService`; pure helpers
  `bracket_fills`, `year_fees`, `build_lots`, `month_subtotals`.
- `src/hermes/adapters/console/tax_demo.py` (new): the synthetic demo year
  every `--demo` scenario shows, run through the real tax service.
- `src/hermes/entrypoints/tui_tax.py` (new): the renderers, the `TaxTab`
  widget, the pack-status reader, the profile view and `tax_inputs` (the
  wiring for `hermes tui`).
- `src/hermes/entrypoints/tui.py`: tab 4 is the `TaxTab`; keys `[` `]`, `S`,
  `E`; Tab inside a query input completes instead of cycling screens.
- `src/hermes/entrypoints/tui_query.py`: `QueryList` takes a `placeholder`
  and posts `QueryList.Refreshed` after each filter or sort.
- `src/hermes/entrypoints/vps.py`: the remote script exports
  `HERMES_CONSOLE_REMOTE=1`.
- `src/hermes/entrypoints/commands/tui.py`: builds the tax inputs.

### Out

- Profile editor, on-demand pack and what-if: MVP2 (needs the Code Owner's OK).
- `DetailPane` / `DetailScreen` (issue #730): the lot detail is a plain
  `HudPanel` beside the list, below it on a compact terminal.
- Live "tax if sold now" at ticker prices: the screen stays booked-only (the
  spec's tax figures are booked); the Home portfolio keeps its own line.

## Decisions

### D-1: the console never computes tax

- **Decision**: the TUI renders `TaxScreen` values only. Lots come from the
  same FIFO replay the estimate runs (`replay_fifo_eur`); fees, bracket fills
  and month subtotals are sums or splits of service outputs, never a tax.
- **Alternatives**: compute the taxable base and the bracket room in the
  console (forbidden by the issue); add a `tax_screen` view to
  `ConsoleReadModel` (every read model would need the scales and the profile).
- **Rationale**: one source of tax figures (ADR-016); the estimate already
  computed the base and the flags, so it now exposes them.
- **Cost of reversal**: LOW.

### D-2: the profile is never read over `--vps`

- **Decision**: `hermes --vps` exports `HERMES_CONSOLE_REMOTE=1` in the
  remote script; with it set, `tax_inputs` only checks whether the profile
  file exists, never opens it, and the estimate runs without taxpayer
  figures. On the PC the figures are read once and shown masked (`S` shows).
- **Alternatives**: detect ssh through `SSH_CONNECTION` (also set for an
  operator who ssh-es into the PC); never show the profile at all (the issue
  asks for the masked view).
- **Rationale**: design spec hard rule: taxpayer figures stay in
  `~/.config/hermes/taxpayer.yaml` on the PC.
- **Cost of reversal**: LOW.

### D-3: sub-tabs reuse `TabBar`, lots reuse `QueryList`

- **Decision**: the sub-tab bar is a second `TabBar` instance (keys `4.1` to
  `4.5`, switched with `[` and `]` or a click); its `Picked` message is
  stopped inside the Tax screen. Lots are the shared `QueryList`, extended
  with a placeholder and a `Refreshed` message rather than a second variant.
- **Alternatives**: Textual's `TabbedContent` (a second tab look); a
  Lots-only table (a second query bar).
- **Rationale**: design spec rule "never build a second variant; extend the
  component".
- **Cost of reversal**: LOW.

### D-4: one synthetic demo year for every scenario

- **Decision**: under `--demo` the Tax screen shows nine synthetic trades
  dated back from the read time and a synthetic taxpayer (general income
  €30,000), run through the real `TaxScreenService`.
- **Alternatives**: a tax block in each scenario JSON (six files to keep in
  sync with no gain).
- **Rationale**: the demo never touches the ledger or the real profile.
- **Cost of reversal**: LOW.

## Requirements

### R-1: Summary

- **A-1.1**: realised gains, fees, taxable base, estimated tax (FIFO), open if
  sold now (with its split per coin), today's tax, the two-month flags and
  the bracket meters with "€X left before N%" show from the service's values:
  `test_summary_shows_the_service_figures_as_given`.

### R-2: figures come from the tax service

- **A-2.1**: a fake source with figures no recomputation could produce is
  shown as is: `test_pressing_4_shows_the_summary_from_the_source`,
  `test_the_screen_takes_every_tax_figure_from_the_tax_service`.

### R-3: Lots

- **A-3.1**: every lot in the shared `QueryList`, filter `state:open`, the
  highlighted lot's FIFO detail and month subtotals:
  `test_lots_show_every_lot_and_filter_by_state`,
  `test_highlighting_a_lot_shows_its_fifo_detail`,
  `test_month_subtotals_total_cost_and_gain_per_buy_month`.

### R-4: Brackets and Tax pack

- **A-4.1**: every band as a meter; the pack's state with "for your gestor,
  never submitted to AEAT": `test_bracket_fills_split_the_base_across_the_bands`,
  `test_the_pack_sub_tab_shows_the_pack_state`.

### R-5: two-month flags exclude USDT

- **A-5.1**: `test_two_month_flags_never_name_usdt`,
  `test_two_month_flags_name_a_coin_loss_with_a_nearby_buy`.

### R-6: profile privacy

- **A-6.1**: masked by default, `S` toggles, `E` says MVP2:
  `test_s_shows_the_profile_and_s_again_masks_it`,
  `test_e_on_the_profile_says_editing_waits_for_mvp2`.
- **A-6.2**: never read over `--vps` (SEV-1, rubric S3):
  `test_profile_view_never_reads_the_file_over_vps`,
  `test_tax_inputs_over_vps_never_read_the_profile`.

### R-7: money visibility and snapshots

- **A-7.1**: without `pnl_visible` no euro amount shows:
  `test_summary_hides_every_amount_when_money_may_not_show`.
- **A-7.2**: 16 SVG snapshots in
  `tests/unit/entrypoints/__snapshots__/test_tui_tax_snapshots/`.

## Design

```text
HomeApp._redraw (tab 4)
  └─ TaxScreenSource.tax_screen(as_of, prices=booked position prices)
       ├─ TaxService.estimate(...)          -> TaxYearEstimate (+ savings_base, two_month)
       ├─ year_fees(trades, year)
       ├─ bracket_fills(scale, savings_base)
       └─ build_lots(trades, flagged refs)  -> LotView + LotSale
  └─ TaxTab.show(screen, profile, pack, tokens, tz, money, width, big_number)
       ├─ Summary  : BigNumber + summary_text
       ├─ Lots     : QueryList(lot_rows) + subtotals_text | lot_detail_text
       ├─ Brackets : brackets_text (Meter per band)
       ├─ Tax pack : pack_text(pack_status(root, year))
       └─ Profile  : profile_text(TaxpayerProfileView, shown)
```

`hermes tui` builds the inputs with `tax_inputs(demo, env, db_path,
config_dir, now)`: the demo source, or a `TaxScreenService` over a read-only
ledger connection with the scales from `config/tax_scales.yaml` and the
taxpayer figures (PC only).

## Test plan

1. `uv run pytest tests/unit/application/tax tests/unit/adapters/console/test_tax_demo.py
   tests/unit/entrypoints/test_tui_tax.py tests/unit/entrypoints/test_tui_tax_snapshots.py`.
2. `bash scripts/dev/pr-preflight.sh` → all checks green.

Test tiers touched: unit, property

## Risks

- **Taxpayer data over `--vps`**: guarded by the remote marker; an operator
  who runs `hermes tui` in a plain ssh shell on the VPS has no profile file
  there, so nothing can be read either.
- **Snapshot churn**: the Tax snapshots live in their own module and
  directory, so Home snapshots are unaffected.
- **Key clashes**: `S` sorts the focused Lots table (its own binding wins);
  on the app `S` and `E` act only on Your profile.

## Dependencies on other changes

- Issue #729 (PR #896): the shared query bar, extended here.
- Issue #730: `DetailPane` / `DetailScreen`; the lot detail moves to it later.
