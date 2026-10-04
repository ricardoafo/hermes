# Change: live-home-screen

**SDD change**: `live-home-screen`
**Date**: 2026-10-04
**Status**: Proposed
**Tracking issue**: see sibling `issue.md` (#546)

Mirror of engram topic key `sdd/live-home-screen/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

Builds on [`ledger-read-model`](../ledger-read-model/change.md) (slice 1 of #546, shipped in #595) and supersedes its
D-8 for the portfolio, activity and shadow test views and for `hermes tui` / `hermes activity`. Its D-1 (read-only
connection per view), D-4 (EUR rule) and D-9 (resolution and ledger path) still apply.

## Summary

Slices 2 and 3 of #546 fill the rest of the `ConsoleReadModel` protocol from the ledger, read-only: `portfolio()` from
each strategy's portfolio marks and capital seed (#405) converted at the newest recorded EUR rate, with Today / This
year from the FIFO values of closed sales; `activity()` from the ledger activity rows, now with opened and closed rows
from recorded fills; and `shadow_test()` from the days since the capital seed and the fill count. `hermes tui` (with
`--db PATH`) and `hermes activity` use the live model when no demo is given; a card the ledger cannot answer yet shows
a plain "not available yet" line instead of stopping the screen. Main risk: a figure without an EUR value passing as
real; every such figure is flagged "EUR pending" and never guessed.

## Scope

### In

- `src/hermes/application/console/portfolio_figures.py`: pure `PortfolioFacts` → `PortfolioSummary` (D-1 to D-3).
- `src/hermes/application/console/shadow_figures.py`: pure shadow test line (D-4).
- `src/hermes/application/console/views.py`: `PortfolioSummary.eur_pending`, `total_value_quote`,
  `starting_capital_quote`, `open_pnl_pending`; `WaterfallColumn.eur_pending`; `ShadowTestStatus.RUNNING` (all
  defaulted or additive, so the fixture scenarios are untouched).
- `src/hermes/application/console/render.py`: the EUR-pending portfolio, waterfall and open line; the start line
  "started €X on D · Equivalent in EUR".
- `src/hermes/adapters/console/ledger_read_model.py`: `portfolio()`, `activity()`, `shadow_test()`.
- `src/hermes/adapters/console/ledger_activity.py`: opened / closed rows from recorded fills (D-5).
- `src/hermes/entrypoints/commands/tui.py`: live model, `--db PATH` (D-6).
- `src/hermes/entrypoints/tui.py`: one card's failure is a line in that card (D-6).
- `src/hermes/entrypoints/activity.py`: live model instead of its own reader.
- `src/hermes/entrypoints/money.py`, `src/hermes/application/bot/replies.py`: EUR-pending portfolio text; "not
  available yet" instead of a crash.

### Out

- A shadow test verdict (on / off track): needs the go-live criteria evaluation, which the ledger does not hold (D-4).
- Restarts, warnings and errors in the live activity feed: they live in the journal (#376).
- `hermes status` keeps its own ledger reader.

## Decisions

All decisions: DECIDED by implementer, Code Owner reviews in PR.

### D-1: Portfolio total = summed equity at the newest rate

- **Decision**: each strategy's equity is a step function: its capital seed amount from `seeded_at` (when the seed
  predates its first mark), then the `equity` of each portfolio mark from its bar boundary (`bar_close_time + 1 ms`).
  The total at an instant is the sum over strategies of the value in force. Total, cash, invested, change and sparkline
  are these USDT figures × `eur_per_usdt`, the newest rate the ledger holds over priced trades **and** priced seeds
  (`eur_rate_date` first; the positions of `ledger-read-model` use the same rate from now on).
  - Range starts: today = local midnight (Europe/Madrid); week = 7 days back; month = 30 days back (the TUI's `3`);
    year = 1 January local (the tax year).
  - Sparkline = the total in force at the range start, then the total at every point inside the range. When nothing
    is in force at the start, the first point inside the range is the baseline. Change = last − first; change % is
    against the baseline.
  - Net over the range = the change: equity already carries the fees paid, and tax is pending (`· before tax`).
  - No rate at all → `eur_pending = True`: no EUR figure; the card shows the USDT total and "EUR pending · run hermes
    fx backfill". Strategies in more than one quote currency → `ViewNotAvailableError`. No mark and no seed →
    `ViewNotAvailableError` ("not available yet" on the card).
- **Alternatives**: convert each mark at its own date's rate (the ledger has no daily rate, `ledger-read-model` D-4);
  rebuild equity from trades (duplicates the tracker; marks exist for this).
- **Rationale**: issue acceptance (EUR everywhere, hero total first); same "now" rate as the positions
  (`ledger-read-model` D-4).
- **Cost of reversal**: LOW (one pure function).

### D-2: Today / This year = FIFO values of coin sales

- **Decision**: the waterfall reads `lot_consumptions` of SELL trades whose `consumed_asset` is the trade's base coin
  (the USDT quote-leg consumptions of #405 are not "closed trades"). Per piece: buy-fee share = buy trade `fee_eur ×
  consumed_qty / original_qty`; gross = `proceeds_eur − cost_eur + buy_fee_share` (before every fee); fees =
  buy-fee share + `fee_eur` of the sale; so gross − fees = `realized_gain_eur`. Today = sales on the local day; year =
  since 1 January local. Tax stays `TaxPending` (#394-#396). Any piece with a pending EUR part makes its column
  `eur_pending` (no partial sum). Open P&L = Σ net of the live positions (`ledger-read-model` D-4), or
  `open_pnl_pending` when any is EUR pending.
- **Alternatives**: include the USDT quote-leg disposals (they are tax events, not trade results; tax is out of
  scope until #394).
- **Rationale**: the waterfall shows closed-trade results; the gain matches the FIFO record exactly.
- **Cost of reversal**: LOW.

### D-3: Start capital = the seed's EUR equivalent

- **Decision**: `starting_capital_quote` = Σ seed amounts (5,000 USDT on the VPS); `starting_capital` = Σ `value_eur`
  once every seed is priced (seed-date ECB rate, #405), else `None`; `started_on` = local date of the earliest seed. The
  line reads `started €4,000.00 on 1 Oct · Equivalent in EUR` (USDT stays off the home screen, operator ruling
  2026-10-03), or `started 5,000.00 USDT on 1 Oct · EUR pending` before `hermes fx backfill` prices the seed, as an
  EUR-pending position shows USDT. Fixture scenarios (no quote amount) keep their line.
- **Alternatives**: convert the seed at today's rate (changes the start capital every day).
- **Rationale**: Code Owner's design: the seed shown as its EUR equivalent, labelled "Equivalent in EUR".
- **Cost of reversal**: LOW.

### D-4: Shadow test = days since the seed and the fill count, no verdict

- **Decision**: day = local calendar days since the earliest capital seed + 1 (the seed day is day 1), capped at 90;
  past day 90 → `FINISHED`. In between the status is the new `ShadowTestStatus.RUNNING` and the note says
  `N fills · verdict not available yet` (N = trades since the seed; `FillQualityRecorded` events, once #353 records
  them, add "(M quality-checked)"; on main there are none and the count is simply omitted). No seed → `NOT_STARTED`,
  "no capital seed in the ledger yet".
- **Alternatives**: claim `ON_TRACK` (a verdict the ledger cannot back); count from the first bar (the seed is what
  starts the run, #405 D-1).
- **Rationale**: show only what the ledger can honestly report.
- **Cost of reversal**: LOW.

### D-5: Activity from fills when there are fills

- **Decision**: `activity()` reads `LedgerActivityReader.items_since(now − window)` (default 7 days; `hermes activity
  --days N` sets it) and groups by local day. A ledger with at least one trade gets opened rows from BUY fills and
  closed rows from SELL fills, at the fill price shown at the fill-date rate (`1 / eur_rate`), and a closed row's net =
  Σ `realized_gain_eur` of its coin consumptions. A fill still EUR pending shows no price and no net. Entry and exit
  signals then give only quiet `signal` rows. A ledger with no trade at all keeps the signal-based opened / closed rows
  (today's VPS, before #353 records fills).
- **Alternatives**: keep signal-based rows always (no price, and they would double the opened rows once fills exist).
- **Rationale**: the fill is the event the operator cares about.
- **Cost of reversal**: LOW.

### D-6: Live `hermes tui`, card by card

- **Decision**: `hermes tui [--db PATH]` uses `resolve_read_model` like `hermes positions`: a demo wins, else the
  ledger, else exit 2 naming `--demo`. In the app each card (heartbeat, portfolio, positions, activity, shadow test,
  detail, tax footnote behind `?`) is drawn on its own: `ViewNotAvailableError` → "<Card> · not available yet",
  `LedgerError` → "<Card> · could not read the ledger: …". No signals card. `hermes activity` drops its private ledger
  path for the live model (same rows, same exit 3 for a missing or unreadable ledger). `hermes money` and the bot's
  `/money` and `/status` show "EUR pending" for a pending summary and the "not available yet" message instead of
  raising.
- **Alternatives**: keep the TUI demo-only until every view is complete (`ledger-read-model` D-8; leaves the VPS
  without a home screen); stop the app on the first failing view.
- **Rationale**: issue goal (the console works live on the VPS); a missing figure must never take the screen down.
- **Cost of reversal**: LOW.

## Requirements

### R-1: Live portfolio

- **A-1.1**: a ledger with a priced 5,000 USDT seed, a buy, a partial sale and three marks shows total, change, cash,
  invested and sparkline per D-1, the waterfall per D-2 and the start line per D-3.
- **A-1.2**: with no EUR rate the portfolio is EUR pending: the USDT total and "EUR pending", no `€` figure; the JSON
  `pnl` is `{"eur_pending": true, "total_value_usdt": ...}`.
- **A-1.3**: a ledger with no mark and no seed raises `ViewNotAvailableError`; the TUI card says "not available yet".

### R-2: Live activity

- **A-2.1**: with recorded fills, `hermes activity` lists "opened BTC at €40,000" and "closed BTC at €44,000" with the
  realised net, and no signal-based opened row.

### R-3: Shadow test

- **A-3.1**: on the seed's day the line reads `day 1 of 90 · running · 2 fills · verdict not available yet`.

### R-4: Live TUI

- **A-4.1**: `hermes tui` with a ledger and no demo runs the home screen on the ledger model (no DEMO DATA tag).
- **A-4.2**: a view that raises `ViewNotAvailableError` turns only its own card into a "not available yet" line.

### R-5: Read-only and visibility

- **A-5.1**: reading every home view leaves the ledger file's bytes unchanged.
- **A-5.2**: on every blind surface the portfolio shows no money, pending or not (only via `pnl_visible`).

## Design

```text
src/hermes/application/console/portfolio_figures.py new: PortfolioFacts, build_portfolio, range_start
src/hermes/application/console/shadow_figures.py    new: build_shadow_test
src/hermes/application/console/views.py             + pending flags, ShadowTestStatus.RUNNING
src/hermes/application/console/render.py            pending portfolio, start line
src/hermes/adapters/console/ledger_read_model.py    portfolio(), activity(), shadow_test()
src/hermes/adapters/console/ledger_activity.py      rows from fills
src/hermes/entrypoints/commands/tui.py              live model, --db
src/hermes/entrypoints/tui.py                       card-by-card fallback
src/hermes/entrypoints/activity.py                  live model
src/hermes/entrypoints/money.py                     pending JSON, not-available note, exit 3
src/hermes/application/bot/replies.py               pending text
```

## Test plan

1. Every acceptance criterion above passes; fixtures are built through `SqliteLedger` (`record_capital_seed`,
   `apply_seed_eur`, `record_trade`, `record_portfolio_mark`, `append_event`), never by hand-written SQL.
2. `bash scripts/dev/pr-preflight.sh` → all checks green.

Test tiers touched: unit, property, integration

## Risks

- **One rate for the whole sparkline.** Past equity is converted at today's newest rate, so the EUR change shows the
  trading result, not the EUR/USD drift (D-1). The start line uses the seed-date rate, so it can differ from the first
  sparkline sample.
- **No fills on the VPS yet.** Until #353 records fills there are no lots, so positions and the open P&L are EUR
  pending and the activity keeps the signal-based rows; the total still shows once the seed is priced by `hermes fx
  backfill`.

## Dependencies on other changes

- Built on `ledger-read-model` (#595), #511 (portfolio marks), #405 (capital seeds) and #435 (trade EUR values); all
  merged.
