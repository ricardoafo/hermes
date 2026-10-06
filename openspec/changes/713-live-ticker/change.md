# Change: 713-live-ticker

**SDD change**: `713-live-ticker`
**Date**: 2026-10-06
**Status**: Applied
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/713-live-ticker/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

Step 3 of the Cockpit console (design spec `docs/design/console-cockpit.md`:
"Live between bars", the Market row of Home, the hard rules). A worker in the
console process reads Kraken's public ticker every 10 s for BTC, ETH and SOL
through a small port, so the UI never blocks and tests inject a fake. The
Market panel shows each coin's live price, its change over the recent ticks
and a braille sparkline. The same ticks drive each open position's now price,
change, unrealised gross and net, stop-room meter and sparkline, and the Home
total and range change. Live values carry a pulsing `live` tag and sit apart
from the booked values ("booked as of 16:00", from the ledger at the last bar
close). A failed tick keeps the last price, dimmed, with "as of HH:MM:SS"; a
ticker stale for more than 60 s dims every live value and the tag reads
"live · paused HH:MM:SS". Live values exist only in the console process: they
are never written to the ledger, never move a stop or an exit, and never reach
the supervisor, briefs, notifications or any AI. No new dependency.

## Scope

### In

- `src/hermes/ports/live_prices.py` (new): `LiveTickerPort`, the one call the
  console needs (`get_ticker(pair) -> Ticker`). `KrakenPublicAdapter`
  satisfies it as it is.
- `src/hermes/application/console/live.py` (new, pure): the poll cadence
  (`TickerPoller`), the in-memory tick board with its ring buffer and
  staleness (`LiveBoard`), and the live maths (`live_position`, `live_total`).
- `src/hermes/entrypoints/tui_live.py` (new): the live tag, the cell flash
  styles and the cockpit Market rows.
- `src/hermes/entrypoints/tui_home.py`: `position_rows` takes the live
  figures; a booked line under the rows.
- `src/hermes/entrypoints/tui_charts.py`: `braille_spark`, a one-row braille
  sparkline.
- `src/hermes/entrypoints/tui.py`: the ticker worker (`run_worker`, thread),
  the cached views repainted on a tick, the flash timer, the live lines on
  Portfolio and the position detail.
- `src/hermes/entrypoints/commands/tui.py`: wires `KrakenPublicAdapter` as the
  ticker for a ledger-backed console (also over `--vps`, where the console
  runs on the VPS); demo data gets no ticker.
- `src/hermes/application/console/render.py`: `range_label` made public; the
  `chart_slot` option of `render_market_watch` is removed (the TUI now draws
  its own Market rows; the CLI output is unchanged).
- `pyproject.toml`: import-linter contract `live-prices-are-console-only`.
- `src/hermes/adapters/console/ledger_read_model.py`: the empty-ledger
  heartbeat's next check is the next 4h boundary, not the read time (separate
  commit).

### Out

- The other live items (forming candle, 24 h context, live exposure, live tax
  if sold now, order book health, feed latency, ...): issue #722.
- Live prices for demo data: a demo scenario's positions are synthetic, so
  mixing them with real prices would mislead.
- A live "would the rules fire" preview: not approved (spec item 11).

## Decisions

### D-1: the port is the adapter's existing call

- **Decision**: `LiveTickerPort` is a `Protocol` with `get_ticker(pair) ->
  Ticker`. The console passes `KrakenPublicAdapter.create()`; tests pass a
  fake with the same method.
- **Alternatives**: the existing `PriceLookupPort` (async, served by the
  Binance ticker adapter; the spec names Kraken's `get_ticker`, and the worker
  is a thread); a console-specific `LivePrice` value (a new adapter method).
- **Rationale**: no adapter change, no new dependency, one seam to fake. The
  application module re-exports the port, so `tui.py` still imports no port
  or adapter (its existing guard test).
- **Cost of reversal**: LOW.

### D-2: the cadence is decided by the 1 s clock tick, the fetch runs in a thread

- **Decision**: `TickerPoller.due(now)` is true when 10 s have passed since the
  last poll started. The app's 1 s tick asks it and starts one exclusive thread
  worker (`run_worker(thread=True, exclusive=True, group="ticker")`). The worker
  calls `TickerPoller.fetch()`, which never raises (each coin's failure becomes
  an outcome), and hands the outcomes back with `call_from_thread`; the board
  is only touched on the UI thread.
- **Alternatives**: a 10 s `set_interval` (not testable with a frozen clock);
  an asyncio loop around a blocking HTTP call (blocks the UI).
- **Rationale**: the cadence follows the injected clock, so a test proves it
  exactly; the network call never runs on the UI thread.
- **Cost of reversal**: LOW.

### D-3: EUR at the booked rate, ticks in USDT

- **Decision**: the ticker reads the USDT pairs hermes trades. A position's
  live EUR values use the row's own EUR rate (`eur_per_usdt`, the ledger's
  ECB rate); the Market price uses the first open position's rate, else the
  portfolio's (`total_value / total_value_quote`), else shows USDT.
- **Alternatives**: Kraken EUR pairs (a second price source for one figure);
  a live USDT/EUR quote (issue #722, item 10).
- **Rationale**: the spec keeps ECB for EUR; live and booked differ only in
  the price.
- **Cost of reversal**: LOW.

### D-4: live figures are whole or absent

- **Decision**: a live position figure uses the live price for every price
  term and the booked facts only for what the price does not change (size,
  entry, fees paid; fees to close scale with the price, as in
  `position_figures.build_detail`). The live total is cash plus every open
  position at its live price, or nothing when any position has no live price
  or EUR is pending. Tax, the waterfall and the Shadow test stay booked-only.
- **Alternatives**: patch the booked figures position by position (a total
  that is half live, half booked).
- **Rationale**: the spec's two layers; a property test proves it.
- **Cost of reversal**: LOW.

### D-5: change in the Market panel is over the ring buffer

- **Decision**: the board keeps the last 90 ticks (15 minutes) per coin in
  memory. Market shows the change from the oldest kept tick to the newest,
  headed `15m`, and the braille sparkline of the same ticks.
- **Alternatives**: the 24 h change (needs the ticker's open price; issue
  #722, item 2).
- **Rationale**: the sparkline and the change describe the same window.
- **Cost of reversal**: LOW.

### D-6: live code is console-only, proved twice

- **Decision**: import-linter contract `live-prices-are-console-only` forbids
  the engine (order router, strategy protocol, execution, portfolio, ledger,
  domain), the supervisor, the bot, the notifier, the messages and their
  entrypoints from importing `hermes.application.console.live`,
  `hermes.ports.live_prices` or `hermes.entrypoints.tui_live`. A unit test
  imports those modules in a fresh interpreter and checks the live modules
  never load. The shared renderers in `render.py` (also used by the bot)
  never import the live module.
- **Rationale**: the hard rule "live prices never feed the engine, the
  ledger, the supervisor or any notification".
- **Cost of reversal**: LOW.

## Requirements

### R-1: ticker worker

- **A-1.1**: the ticker is called for BTC, ETH and SOL once every 10 s of the
  console clock, never more often, in a thread worker.
- **A-1.2**: a failing ticker never raises into the UI: the coin keeps its last
  price, dimmed, with "as of HH:MM:SS".
- **A-1.3**: no tick for more than 60 s: the live values dim and the tag reads
  "live · paused HH:MM:SS".

### R-2: Market panel

- **A-2.1**: per coin the live price, the change over the kept ticks and a
  braille sparkline, with the next check; the columns fit 44 cells and up.

### R-3: live between bars

- **A-3.1**: each open position's now price, change, unrealised gross and net,
  stop-room meter and sparkline follow the live price.
- **A-3.2**: the Home total and the range change follow the live prices.
- **A-3.3**: live values carry the pulsing `live` tag; booked values show
  "booked as of HH:MM" (the last bar close); waterfall, tax and Shadow test
  are booked-only.
- **A-3.4**: a tick flashes the changed cells (blue up, orange down) for 0.9 s;
  with `HERMES_CONSOLE_MOTION=off` nothing flashes.
- **A-3.5**: money still follows `pnl_visible`.

### R-4: hard rules

- **A-4.1**: no ledger write: the ledger file's bytes are the same before and
  after ticks.
- **A-4.2**: engine, supervisor and notification code never import the live
  modules (contract and unit guard).

### R-5: heartbeat on an empty ledger

- **A-5.1**: with no bar check yet, the next check is the next 4h boundary.

### R-6: snapshots

- **A-6.1**: SVG snapshots at 80x24, 120x36, 160x45 and 240x60 with a frozen
  clock and a fake ticker.

## Design

```text
src/hermes/ports/live_prices.py            LiveTickerPort (Protocol)
src/hermes/application/console/live.py     TickOutcome, LiveQuote, LiveStatus,
                                           LiveBoard, TickerPoller,
                                           LivePosition, live_position,
                                           LiveTotal, live_total
src/hermes/entrypoints/tui_live.py         LiveLook, live_tag, market_rows, eur_rate,
                                           live_portfolio_lines, live_detail_line
src/hermes/entrypoints/tui_home.py         LiveRows; position_rows(live=...)
src/hermes/entrypoints/tui.py              1 s tick -> poller.due -> run_worker(thread)
                                           -> fetch -> call_from_thread(apply)
                                           -> board.apply -> flash + _paint_live()
```

The app caches the views it read on the last poll (portfolio, positions,
details, market watch), so a tick, a flash ending or a pulse step repaints the
live cards without reading the ledger again.

## Test plan

1. Every acceptance criterion above passes (`tests/unit/application/console/test_live.py`,
   `tests/unit/entrypoints/test_tui_live.py`, `tests/unit/entrypoints/test_tui_live_screen.py`,
   `tests/unit/test_live_import_guard.py`, `tests/unit/entrypoints/test_console.py`,
   `tests/unit/entrypoints/test_tui_charts.py`,
   `tests/unit/adapters/console/test_ledger_read_model.py`).
2. `bash scripts/dev/pr-preflight.sh` → all checks green.
3. All CI jobs green on the PR.

Test tiers touched: unit, property

## Risks

- **Kraken rate limit**: 3 public calls per 10 s, under the 1 call/s cap; the
  adapter's own limiter spaces them.
- **Snapshot churn**: the empty live ledger snapshots change with the
  heartbeat fix; new live snapshots are added. Motion off, frozen clock.
- **Operator reads live as booked**: the two layers are labelled on every
  card, and only booked figures feed tax and the Shadow test.

## Dependencies on other changes

- Builds on `712-cockpit-home` and `711-cockpit-theme` (not merged yet).
  Follow-up: issue #722.
