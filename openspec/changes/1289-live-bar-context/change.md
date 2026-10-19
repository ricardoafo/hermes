# Change: 1289-live-bar-context

**SDD change**: `1289-live-bar-context`
**Date**: 2026-10-19
**Status**: Applied
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/1289-live-bar-context/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

The Market panel shows, under each coin, a dim line for the 4h bar being built
right now and a line for the last 24 hours (issue #722 items 1 and 2, as in the
approved live-data mock of 19 Oct). Both come from one new read-only call, the
hourly bars of the coin every 60 s, through a new port that the existing public
Kraken adapter already satisfies. The numbers live in the console process only.
The main risk is width: the Market panel is 44 cells wide beside Activity at 160
columns, so each line has short forms that drop the label, the live price and
the volume before a part is dropped.

## Scope

### In

- `LiveBarsPort` in `hermes.ports.live_prices` (one method, `fetch_klines`).
- `hermes.application.console.live_context`: `bar_context`, `BarsPoller`, `ContextBoard`.
- `tui_live.market_rows` draws the lines; `HomeApp` polls and repaints.
- `live_context` joins the `live-prices-are-console-only` contract.

### Out

- The Positions chart: it is a line chart of closes, not candles, so the mock's hollow last bar needs the Code Owner's decision (follow-up).
- Items 3 to 10 of #722.

## Decisions

### D-1: One hourly-bars call feeds both lines

- **Decision**: Poll the hourly bars of each coin every 60 s and build the 4h bar and the 24 h context from them.
- **Alternatives**: the 4h OHLC endpoint plus the ticker's 24 h fields (the ticker's open is the UTC day open, not 24 h ago); two calls per coin.
- **Rationale**: one call gives open, high, low and volume for both windows with exact boundaries; 3 requests a minute stay far under the public rate limit.
- **Cost of reversal**: LOW.

### D-2: The 24 h window starts at the hourly bar that opened 24 h before the current hour

- **Decision**: window = [current hour open - 24 h, now], so it is between 24 and 25 hours long.
- **Alternatives**: a rolling 24 h from minute data (needs 1-minute bars, 1,440 rows per coin).
- **Rationale**: stays on the data floor of hourly bars; the label says 24h and the error is under 4 %.
- **Cost of reversal**: LOW.

### D-3: A context holds no live price

- **Decision**: the forming bar and the 24 h change are computed at paint time from the live price of the ticker.
- **Alternatives**: use the last close of the hourly bar (up to 60 s old, disagrees with the price column).
- **Rationale**: the lines always agree with the price beside them; a live price beyond the bars' range widens high and low.
- **Cost of reversal**: LOW.

### D-4: Missing data hides a figure, never guesses it

- **Decision**: no first hourly bar of a window means no bar open or no 24 h change; a context older than 5 minutes, a failed price, or a bar that closed since the fetch shows nothing.
- **Rationale**: a wrong open would make every other number on the line wrong.
- **Cost of reversal**: LOW.

### D-5: Show what fits, in short forms

- **Decision**: each part has three forms; the fullest that fits is used, and both parts share a line only when their full forms fit.
- **Alternatives**: fixed breakpoints from the mock (the mock assumed a wide panel); widen the panel (layout change, Activity shrinks).
- **Rationale**: no layout change and no snapshot churn outside the Market panel; at 44 cells the label, the live price and the volume are dropped.
- **Cost of reversal**: LOW.

## Requirements

### R-1: Display only

- **A-1.1**: `uv run lint-imports` keeps `live-prices-are-console-only` with `hermes.application.console.live_context` forbidden to the engine, ledger, supervisor and notification layers.
- **A-1.2**: `uv run pytest tests/unit/test_live_import_guard.py` passes (a fresh interpreter importing those layers never loads the module).

### R-2: Figures are right

- **A-2.1**: `uv run pytest tests/unit/application/console/test_live_context.py` passes (hand-computed bar and 24 h figures, missing hours, closed bar, cadence, failure, stale).

### R-3: The panel draws them

- **A-3.1**: `uv run pytest tests/unit/entrypoints/test_tui_live_context.py` passes (one line, two lines, short forms, once per coin, paused, EUR, through the app, failing source).

## Design

- `LiveBarsPort.fetch_klines(*, pair, interval, since, until) -> Iterator[Kline]`; the poller asks for `[current hour open - 24 h, current hour open + 1 h)` at 1h.
- `BarContext` keeps the open, high, low of both windows and the 24 h volume; `forming(last, now)` and `day(last)` add the live price.
- `HomeApp` starts a `bars` worker from the 1 s clock tick (`_start_bars`) when the ticker also satisfies the port; a ticker that does not leaves the panel as before.
- Verified on the current tree: `uv run lint-imports` (14 contracts kept), `uv run mypy src` (strict, no issues).

## Test plan

1. Every acceptance criterion above passes.
2. `uv run pytest tests/unit` passes.
3. Console snapshots do not change: the fake tickers of the existing tests give no bars.

Test tiers touched: unit, property

## Risks

- **Panel height**: three coins add up to six lines to the Market panel, so the row grows; the body scrolls and the Activity card beside it keeps its size.
- **Narrow short forms drop the "forming" label**: at 44 cells the line reads `O 53,020 H 53,240 L 52,980 · 2h11m left`. Mitigation: it sits directly under the coin and is dim; the Code Owner can ask for a label.
- **Public rate limit**: 3 extra requests a minute; the adapter's limiter already paces public calls.

## Dependencies on other changes

- None. The Positions chart follow-up depends on the Code Owner's decision on candle versus line.
