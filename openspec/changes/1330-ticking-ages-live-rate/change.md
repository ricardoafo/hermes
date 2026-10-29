# Change: 1330-ticking-ages-live-rate

**SDD change**: `1330-ticking-ages-live-rate`
**Date**: 2026-10-19
**Status**: Applied
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/1330-ticking-ages-live-rate/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

The status line says how long ago hermes last checked a bar (`checked 12m ago`)
instead of the clock time of the check, and the System tab shows Kraken's live
USDT/EUR quote next to the ECB rate (issue #722 items 7 and 10). The quote
comes from one new poll of the existing public ticker; it lives in the console
process only and booked figures keep using the ECB rate.

## Scope

### In

- `format.elapsed_text`: `45s`, `12m`, `1h 02m`, `3d 4h`.
- The OK line of the heartbeat reads `● live · checked 12m ago · next 18:00`; the TUI repaints it every clock tick, so the age moves with the console clock.
- `hermes.application.console.live_rate`: `RatePoller`, `RateBoard`, `LiveRate`.
- `tui_feeds.ContextFeeds` starts a third feed, `rate`, for any ticker; `tui_system.timing_text` shows `ECB rate of <day> · live 0.8908` and, once older than 90 s, the dimmed quote with its age.
- `live_rate` joins the `live-prices-are-console-only` contract.

### Out

- The countdown to the next bar: it already ticks every second in the status row (`in 3h 47m 12s`).
- A countdown to certification: certification is counted in whole days (ADR-021), nothing to tick.
- The quote on the Portfolio screen and in the money footnote: the System tab is the one place that already names the rate; more places can follow if the maintainer wants them.
- Items 4, 8 and 9 of #722.

## Decisions

### D-1: The age replaces the clock time of the last check

- **Decision**: `checked 12m ago` instead of `checked 14:00`. The next check keeps its clock time, and the countdown beside it keeps its seconds.
- **Alternatives**: both (`checked 14:00 (12m ago)`): 8 cells more on a row that also carries the feed pulse, the latency chip and the clock, so the chip would drop at 160 columns. Seconds in the age: the row already ticks seconds in its clock and countdown.
- **Rationale**: the age answers "is it alive" at a glance; the time of day of a bar check is always the bar close plus seconds.
- **Cost of reversal**: LOW.

### D-2: The quote is the middle of best bid and ask, every 30 s

- **Decision**: (bid + ask) / 2 of the public USDT/EUR ticker, polled every 30 s; fresh up to 90 s (three polls), then dimmed with its age. A failed poll keeps the last good quote.
- **Alternatives**: the last trade (jumps within the spread); every 10 s (the rate moves in the fourth decimal over minutes).
- **Rationale**: a reference to compare with the daily ECB rate needs no more.
- **Cost of reversal**: LOW.

### D-3: Display only

- **Decision**: the quote is never used to price anything booked, never stored, never sent to the engine, supervisor or notifications.
- **Rationale**: the tax and the ledger use the ECB rate (#405); ADR-012.
- **Cost of reversal**: not reversible by design.

## Requirements

### R-1: Display only

- **A-1.1**: `uv run lint-imports` keeps `live-prices-are-console-only` with `hermes.application.console.live_rate` forbidden to the engine, ledger, supervisor and notification layers.
- **A-1.2**: `uv run pytest tests/unit/test_live_import_guard.py` passes.

### R-2: Figures are right

- **A-2.1**: `uv run pytest tests/unit/application/console/test_live_rate.py` passes (middle of bid and ask by hand, USDT/EUR pair asked, failure named, 30 s cadence, fresh up to 90 s, stale after, last good quote kept).
- **A-2.2**: `uv run pytest tests/unit/application/console/test_format.py` passes (`elapsed_text` examples and the unit property).

### R-3: The console draws them

- **A-3.1**: `uv run pytest tests/unit/entrypoints/test_tui_live_rate.py` passes (live part beside the ECB date, stale age, no quote, through the app with a working and a failing ticker, the status age following the console clock).
- **A-3.2**: `uv run pytest tests/unit/entrypoints/test_tui_feeds.py` passes (three feeds, rate cadence, the rate reader).

## Design

- `HomeApp._tick` repaints the heartbeat every second; `_feeds_answered` repaints the System tab when it is shown.
- `render_heartbeat` reads `ctx.now`, which the TUI sets to its own clock on every paint, so one line changed in `render.py`; the CLI, Slack and Telegram surfaces show the age against their read time.
- Verified on the current tree: `uv run lint-imports`, `uv run mypy src` (strict), `bash scripts/dev/ci-checks.sh guards`.

## Test plan

1. Every acceptance criterion above passes.
2. `uv run pytest tests/unit` passes.
3. Snapshots change only in the heartbeat line (`checked 14:00` becomes `checked 12m ago`).

Test tiers touched: unit, property

## Risks

- **Snapshots**: every screen's heartbeat line changes; reviewed as text, the diff is that one line.
- **Status row room**: the OK line is 2 cells longer than before, which can drop the latency chip's sparkline one terminal width earlier.
- **Public rate limit**: one more request every 30 s.

## Dependencies on other changes

- None.
