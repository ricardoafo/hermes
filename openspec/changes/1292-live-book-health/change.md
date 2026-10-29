# Change: 1292-live-book-health

**SDD change**: `1292-live-book-health`
**Date**: 2026-10-19
**Status**: Applied
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/1292-live-book-health/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

The Market panel shows, under each coin, a dim line with the depth of its order
book and whether the book is fresh or stale, and the status row shows how long
the exchange takes to answer (issue #722 items 5 and 6, as in the approved
live-data mock of 19 Oct). Both come from one new read-only call, the order
book of each coin every 10 s, through a new port that the existing public Kraken
adapter already satisfies. The numbers live in the console process only.

## Scope

### In

- `LiveBookPort` in `hermes.ports.live_prices` (one method, `get_orderbook`).
- `hermes.application.console.live_book`: `book_depth`, `BookPoller`, `BookBoard`.
- `tui_live.book_line` and `tui_live.latency_chip`.
- `tui_feeds.ContextFeeds`: one home for the bars feed (#1289) and the book feed, so `HomeApp` only starts them and repaints; this moves the bars wiring out of `tui.py`, which is at its size ratchet.
- `live_book` joins the `live-prices-are-console-only` contract.

### Out

- Binance latency: the console has no Binance feed, so there is nothing to time.
- Ticker round trips: the book call is the same host and the same path to the exchange; one series is enough for a sparkline.
- Items 3, 4, 7 to 10 of #722.

## Decisions

### D-1: Depth is the thinner side of the top 10 levels, in quote currency

- **Decision**: depth = min(value of the 10 best bids, value of the 10 best asks).
- **Alternatives**: the sum of both sides (hides a one-sided book); each side shown (no room at 44 cells).
- **Rationale**: an order of that size can be filled on either side; one number fits the panel.
- **Cost of reversal**: LOW.

### D-2: The fresh/stale flag is the console's own

- **Decision**: a book is fresh while its newest good answer is at most 20 s old (two missed polls at 10 s), stale after, shown in amber with its age.
- **Alternatives**: the engine's 2 s rule (RO-04): a polled book is always older than 2 s, so it would always read stale.
- **Rationale**: it says whether the console's feed went quiet; it neither reads nor changes the engine's rule, which is pre-registered.
- **Cost of reversal**: LOW.

### D-3: A failed poll keeps the last good book

- **Decision**: a failed fetch leaves the previous depth, which then ages into stale.
- **Rationale**: the operator sees "stale 34s" with the depth it had instead of a blank.
- **Cost of reversal**: LOW.

### D-4: Latency is the slowest good call of each poll

- **Decision**: the ring keeps one value per poll, the slowest good call in milliseconds, 30 polls (five minutes), timed on the console clock; the chip shows the last one, an 8-cell sparkline and the age of the newest price, and drops the sparkline when short of room.
- **Alternatives**: one value per coin per poll (three times the points); a monotonic timer (not injectable in tests).
- **Rationale**: a slow coin shows; the status row's spare cells are few.
- **Cost of reversal**: LOW.

## Requirements

### R-1: Display only

- **A-1.1**: `uv run lint-imports` keeps `live-prices-are-console-only` with `hermes.application.console.live_book` forbidden to the engine, ledger, supervisor and notification layers.
- **A-1.2**: `uv run pytest tests/unit/test_live_import_guard.py` passes.

### R-2: Figures are right

- **A-2.1**: `uv run pytest tests/unit/application/console/test_live_book.py` passes (hand-computed depth, timing, failure, fresh up to 20 s, stale after, ring order and size).

### R-3: The console draws them

- **A-3.1**: `uv run pytest tests/unit/entrypoints/test_tui_live_book.py` passes (book line forms, amber stale, paused dim, EUR, chip forms, through the app, failing source).
- **A-3.2**: `uv run pytest tests/unit/entrypoints/test_tui_feeds.py` passes (one worker per feed, cadence, no overlap, closing app, readers).

## Design

- `LiveBookPort.get_orderbook(pair, *, depth=10) -> Orderbook`.
- `HomeApp` calls `ContextFeeds.start` from the 1 s clock tick; each feed starts a worker only when the ticker also satisfies its port, so a ticker that does not leaves the console as before. `tui.py` shrinks from 2655 to 2624 lines.
- The book line follows each coin's context lines; the chip is appended to the status row's feed segment only when spare cells remain after the feed pulse.
- Verified on the current tree: `uv run lint-imports`, `uv run mypy src` (strict), `bash scripts/dev/ci-checks.sh guards`.

## Test plan

1. Every acceptance criterion above passes.
2. `uv run pytest tests/unit` passes.
3. Console snapshots do not change: the fake tickers of the existing tests give no books.

Test tiers touched: unit, property

## Risks

- **Panel height**: one more line per coin in the Market panel; the body scrolls.
- **Public rate limit**: 3 extra requests every 10 s, 18 a minute; the adapter's limiter already paces public calls.
- **Status row room**: a narrow row drops the chip first; the heartbeat, badge and countdown are never cut.

## Dependencies on other changes

- None. It reads the same hexagon edge as the ticker and the bars poll.
