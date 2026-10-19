# Change: ledger-read-model

**SDD change**: `ledger-read-model`
**Date**: 2026-10-04
**Status**: Shipped
**Tracking issue**: see sibling `issue.md` (#546)

Mirror of engram topic key `sdd/ledger-read-model/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

Slice 1 of #546: a `LedgerReadModel` adapter implements the `ConsoleReadModel` protocol over the SQLite ledger, opened
read-only (`mode=ro`, one short connection per view call, never migrates, never takes the serve lock). This slice fills
`positions()` and `position(coin)` from the newest portfolio mark (#387) and the recorded trades and lots with their
EUR values (#435); the other views say plainly that they are not available in the live read model yet.
`resolve_read_model` returns it when no demo is selected and the ledger file exists, so `hermes positions` shows real
positions. Main risk: EUR figures mixing fill-date and current rates; the rule is fixed in D-4 and an unpriced fill turns
the row into an explicit "EUR pending" state, never a guessed figure.

## Scope

### In

- `src/hermes/adapters/console/ledger_read_model.py`: `LedgerReadModel` (read-only SQL over `portfolio_marks`, `lots`,
  `trades`, `events`).
- `src/hermes/application/console/position_figures.py`: pure `PositionFacts` → `PositionRow` / `PositionDetail`.
- `src/hermes/application/console/read_model.py`: `ViewNotAvailableError`.
- `src/hermes/application/console/views.py`: `PositionRow.eur_pending` and `PositionRow.entry_price_eur` (defaulted, so
  the fixture adapter and its scenarios are untouched).
- `src/hermes/application/console/render.py`, `serialize.py`: the EUR-pending row and detail.
- `src/hermes/entrypoints/console.py`: `resolve_read_model(..., db_path=...)`, `demo_read_model`.
- `src/hermes/entrypoints/commands/positions.py`: `--db PATH`; ledger read failure exits 3.
- `src/hermes/entrypoints/commands/tui.py`, `src/hermes/entrypoints/activity.py`: keep today's behaviour (D-8).

### Out

- Slice 2 (activity) and slice 3 (home: heartbeat, portfolio, shadow test, TUI without `--demo`). See D-8.
- Per-bar stop history: the trailing stop is only recorded on signals today (D-5). Follow-up issue.
- A stored ECB rate for "today": the ledger only holds the rates of priced trades (D-4).
- Tax if sold now: stays `TaxPending` until the tax module (#394-#396).

## Decisions

All decisions: DECIDED by implementer, Code Owner reviews in PR.

### D-1: One read-only connection per view call

- **Decision**: `LedgerReadModel.open(path)` checks the file exists and keeps only the path. Each view call opens
  `file:<path>?mode=ro`, runs its queries and closes. It never runs migrations and never writes.
- **Alternatives**: one connection for the model's lifetime (the TUI would hold a shared lock between redraws, and the
  protocol has no `close`); reuse `SqliteLedger` (its constructor migrates, which is a write).
- **Rationale**: issue acceptance ("read-only (mode=ro)"); `read_model.py` contract ("never take the serve lock"); same
  pattern as `SqliteStatusReader` and `LedgerActivityReader`.
- **Cost of reversal**: LOW.

### D-2: Figures are a pure application function

- **Decision**: the adapter turns rows into `PositionFacts` (plain values); `position_figures.build_detail` computes
  every derived number. The adapter does no arithmetic beyond parsing.
- **Rationale**: hexagonal layering (ADR-013); the arithmetic is property-testable without SQLite.
- **Cost of reversal**: LOW.

### D-3: Which positions are open, and their native figures

- **Decision**: the open set is the newest portfolio mark of each strategy (`portfolio_marks`, #387): its
  `positions_json` gives size, native cost (notional, fees excluded) and the latest mark price. Average entry price =
  cost / size. Timeframe = interval of the newest mark row of that pair. Side is always `long` (spot, long-only).
  `opened_at` = earliest `opened_at` of the strategy's open lots of the coin; without lots, the time of the entry
  signal.
- **Alternatives**: rebuild positions from `TradeExecuted` events (duplicates the tracker; marks exist for this).
- **Cost of reversal**: LOW.

### D-4: EUR values: acquisition at fill rates, value at the newest recorded rate

- **Decision**:
  - Entry (EUR) = Σ `price_eur × remaining_qty` over the strategy's open lots of the coin (#435 values, fill-date ECB
    rate). Fees paid = Σ `fee_eur × remaining_qty / original_qty` (a partly sold lot keeps its share of the buy fee).
    So `cost = size × entry_eur + fees_paid` is the ADR-016 acquisition value of what is still held.
  - `eur_per_usdt` (current prices, stop, high) = `1 / eur_rate` of the newest EUR-priced trade in the position's quote
    currency: the newest ECB rate the ledger holds.
  - Gross = `size × current_price × eur_per_usdt − size × entry_eur`; fees to close (estimate) = now value × the buy
    fee rate of the open lots (`fees_paid / (size × entry_eur)`); tax pending; net = gross − fees − fees to close.
  - Change % = `current_eur / entry_eur − 1`.
  - **EUR pending**: when any open lot's trade has `eur_status = pending`, when the open lots do not add up to the
    snapshot size (fills not recorded), or when no rate exists, the row has `eur_pending = True`: prices are shown in
    USDT, no EUR figure is shown or serialised, and the detail points at `hermes fx backfill`.
- **Alternatives**: convert everything at today's rate (drops the #435 values and the EUR/USD drift that IRPF counts);
  call an FX API from the console (network on a read path; the console stays offline).
- **Rationale**: ADR-002 §3, ADR-016 items 3 and 5; issue: "EUR-pending rows → the view's pending state, never guessed".
- **Cost of reversal**: LOW (one function).

### D-5: Stop = the entry signal's stop

- **Decision**: stop price and ATR come from the newest `SignalEmitted` entry of the strategy and pair. Per-bar stop
  levels are not recorded yet (only entry and exit signals carry a stop), so the console shows the **entry stop**:
  `stop_moves = 0` and the timeline has no stop raises. A position with no entry signal is a ledger inconsistency and
  raises `LedgerError` (the command exits 3).
- **Alternatives**: recompute the trailing stop from `live_bars` (duplicates strategy logic in the console).
- **Cost of reversal**: LOW; a follow-up records the stop per bar and this reads it.

### D-6: High since entry and price path from the marks

- **Decision**: `price_path` = the coin's mark prices of the strategy's marks since `opened_at`, one per distinct
  `marked_at`, oldest first (closes, not wicks). `high_since_entry` = max of the entry price and that path.
- **Cost of reversal**: LOW.

### D-7: Entry values

- **Decision**: `entry_values = (("atr", atr × eur_per_usdt),)` from the entry signal; empty while EUR is pending. The
  Donchian channel value is not on the event.
- **Cost of reversal**: LOW.

### D-8: Per-method decisions for this slice

| Method | Slice 1 behaviour | Why |
| --- | --- | --- |
| `demo_scenario` | `None` | real data: no DEMO DATA tag |
| `positions()` | live rows (D-3..D-7), coin order | this slice |
| `position(coin)` | live detail, `None` when not held | this slice |
| `heartbeat()` | `as_of` = read clock; last check = newest `BarProcessed`; next = soonest due close; `LATE` once 15 min past it; never `DOWN`; no bar yet → `OK` at the read clock | `RenderContext.for_model` needs it; DOWN needs systemd (home slice uses the status layer) |
| `portfolio()` | raises `ViewNotAvailableError` | home slice (sparkline ranges over marks, EUR equity) |
| `activity()` | raises `ViewNotAvailableError` | activity slice; `hermes activity` keeps its own ledger reader |
| `shadow_test()` | raises `ViewNotAvailableError` | the test start date is not in the ledger |

`hermes tui` without `--demo` keeps exiting 2 with "use --demo" (the home screen needs every view); `hermes activity`
and `hermes status` resolve only demo scenarios (`demo_read_model`) and keep their own `--db` ledger readers.

### D-9: Resolution and the ledger path

- **Decision**: `resolve_read_model(demo_flag, env, db_path=None)`: a demo wins; otherwise the ledger at `db_path`
  (default `./hermes.sqlite3`, `serve.DEFAULT_DB_PATH`) if the file exists; otherwise `LiveReadModelUnavailableError`
  naming the path and `--demo`. `hermes positions` gains `--db PATH`. A ledger read failure exits 3.
- **Cost of reversal**: LOW.

## Requirements

### R-1: Live positions

- **A-1.1**: a ledger with one recorded priced BUY, its entry signal and a later mark lists that coin with entry,
  size, current price, stop and net computed per D-4.
- **A-1.2**: `position(coin)` returns `None` for a coin not held; a sold-out position is not listed.
- **A-1.3**: `cost` equals the acquisition value of the held quantity (#435 `price_eur × qty + fee_eur`).

### R-2: Read-only

- **A-2.1**: the model's connection refuses a write (`attempt to write a readonly database`).
- **A-2.2**: reading every view leaves the ledger file's bytes unchanged; a missing file is never created.

### R-3: EUR pending

- **A-3.1**: a position whose BUY is pending renders "EUR pending", no `€` figure, and its JSON `pnl` carries
  `eur_pending: true` and no `_eur` keys.

### R-4: Visibility

- **A-4.1**: on every blind surface the live rows show no money and the JSON has no `pnl` (only via `pnl_visible`).

### R-5: Resolution

- **A-5.1**: without a demo and with a ledger, `resolve_read_model` returns a model with `demo_scenario is None`; with
  no ledger it raises `LiveReadModelUnavailableError` mentioning `--demo`.
- **A-5.2**: `hermes positions --db PATH` prints a recorded position (integration test from recorded trade to output).

## Design

```text
src/hermes/application/console/read_model.py        + ViewNotAvailableError
src/hermes/application/console/views.py             + PositionRow.eur_pending, entry_price_eur
src/hermes/application/console/position_figures.py  new: PositionFacts, LotFacts, build_detail
src/hermes/application/console/render.py            EUR-pending row and detail
src/hermes/application/console/serialize.py         EUR-pending pnl
src/hermes/adapters/console/ledger_read_model.py    new: LedgerReadModel
src/hermes/entrypoints/console.py                   resolve_read_model(db_path), demo_read_model
src/hermes/entrypoints/commands/positions.py        --db, exit 3 on LedgerError
src/hermes/entrypoints/commands/tui.py              live model → "use --demo" (D-8)
src/hermes/entrypoints/activity.py                  demo_read_model
```

## Test plan

1. Every acceptance criterion above passes; fixtures are built through `SqliteLedger` (`record_trade`,
   `record_portfolio_mark`, `append_event`), never by hand-written SQL.
2. `bash scripts/dev/pr-preflight.sh` → all checks green.

Test tiers touched: unit, property, integration

## Risks

- **Stale "now" rate.** The current EUR value uses the newest rate the ledger holds, which can be days old when no
  trade was priced recently. Mitigation: documented here; a stored daily rate is a follow-up.
- **Shared FIFO across strategies.** Lots are consumed FIFO per asset, not per strategy; two strategies on one coin
  could make lot remainders differ from a strategy's snapshot size. That case shows EUR pending instead of a wrong
  figure (D-4).
- **Entry stop only.** Until per-bar stops are recorded the stop distance overstates the cushion once the stop has
  trailed up (D-5).

## Dependencies on other changes

- Built on #532 (console panel registry) and #511 (portfolio marks); both must merge first.
