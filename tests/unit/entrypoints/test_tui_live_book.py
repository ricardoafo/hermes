"""The Market panel shows book depth and fresh/stale; the status row shows latency (#1292).

Invariants:

- under a coin with a live price and a good book sits one line: its depth and
  whether the book is fresh; a stale book shows its age in amber instead;
- a narrow panel drops the label, then the flag, and shows nothing when not
  even the shortest form fits; a coin without a book or without a live price
  shows no book line;
- the depth shows in EUR at the booked rate;
- the latency chip shows the last round trip, a sparkline and the age of the
  newest price, falls back to the figures alone, and is empty without a poll;
- through the app: a ticker that can also give books fills the lines and the
  chip, one that cannot leaves the panel as before, and a failing book
  source never reaches the UI.
"""

import asyncio
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from rich.console import Group
from rich.text import Text

from hermes.adapters.console import FixtureReadModel
from hermes.application.console import Surface
from hermes.application.console.live import LiveQuote, LiveState, LiveStatus
from hermes.application.console.live_book import BookHealth
from hermes.application.console.render import RenderContext, to_plain_text
from hermes.domain.errors import KrakenPublicError
from hermes.domain.market_data.orderbook import Orderbook, OrderbookLevel
from hermes.domain.market_data.ticker import Ticker
from hermes.domain.value_objects.pair import Pair
from hermes.entrypoints.tui import HomeApp
from hermes.entrypoints.tui_live import LiveLook, latency_chip, market_rows
from hermes.entrypoints.tui_theme import BASE
from tests.support.clock import FakeClock

_NOW = datetime(2026, 10, 19, 14, 20, tzinfo=UTC)


def _look(state: LiveState = LiveState.LIVE) -> LiveLook:
    model = FixtureReadModel.load("ok-two-positions")
    ctx = replace(RenderContext.for_model(model, surface=Surface.OPERATOR_TUI), demo_scenario=None)
    return LiveLook(
        status=LiveStatus(state, _NOW), tokens=BASE, dot="#00F0FF", ctx=ctx, booked_at=_NOW
    )


def _quote(coin: str = "BTC", *, failed: bool = False) -> LiveQuote:
    return LiveQuote(
        coin=coin,
        price=Decimal(126),
        at=_NOW,
        previous=None,
        history=(Decimal(125), Decimal(126)),
        failed_at=_NOW + timedelta(seconds=10) if failed else None,
    )


def _health(*, age: int = 3, depth: str = "480000") -> BookHealth:
    return BookHealth(
        coin="BTC",
        depth=Decimal(depth),
        at=_NOW - timedelta(seconds=age),
        age=timedelta(seconds=age),
        fresh=age <= 20,
    )


def _rows(
    width: int,
    *,
    look: LiveLook | None = None,
    rate: Decimal | None = None,
    books: Mapping[str, BookHealth] | None = None,
) -> Group:
    model = FixtureReadModel.load("ok-two-positions")
    rows = market_rows(
        model.market_watch(),
        {"BTC": _quote()},
        look or _look(),
        width=width,
        rate=rate,
        books={"BTC": _health()} if books is None else books,
        now=_NOW,
    )
    assert isinstance(rows, Group)
    return rows


def _book_lines(
    width: int,
    *,
    rate: Decimal | None = None,
    books: Mapping[str, BookHealth] | None = None,
) -> list[str]:
    text = to_plain_text(_rows(width, rate=rate, books=books), width=width)
    return [line for line in text.splitlines() if line.startswith("     ")]


def test_a_fresh_book_shows_its_depth_and_the_flag() -> None:
    """A book 3 s old with 480,000 resting reads depth 480.0k and fresh."""
    assert _book_lines(100) == ["     book  depth 480.0k · fresh"]


def test_a_stale_book_shows_its_age_and_keeps_the_depth() -> None:
    """A book 34 s old reads stale 34s, with the depth it had."""
    assert _book_lines(100, books={"BTC": _health(age=34)}) == [
        "     book  stale 34s · depth 480.0k"
    ]


def test_sizes_read_in_millions_thousands_and_units() -> None:
    """1,200,000 reads 1.2M; 12,400 reads 12.4k; 950 reads 950."""
    shown = [
        _book_lines(100, books={"BTC": _health(depth=value)})[0].split("depth ")[1].split(" ")[0]
        for value in ("1200000", "12400", "950")
    ]
    assert shown == ["1.2M", "12.4k", "950"]


def test_a_narrow_panel_drops_the_label_then_the_flag() -> None:
    """At 26 cells 'depth 480.0k · fresh' fits; at 20 only 'depth 480.0k'; at 16 nothing."""
    assert (_book_lines(30), _book_lines(20), _book_lines(16)) == (
        ["     depth 480.0k · fresh"],
        ["     depth 480.0k"],
        [],
    )


def test_a_stale_narrow_book_keeps_the_age() -> None:
    """At 16 cells a stale book is 'stale 34s'."""
    assert _book_lines(16, books={"BTC": _health(age=34)}) == ["     stale 34s"]


def test_a_coin_without_a_book_has_no_book_line() -> None:
    """With no book health at all the panel has no indented line."""
    assert _book_lines(100, books={}) == []


def test_the_depth_shows_in_eur_at_the_booked_rate() -> None:
    """At 0.5 EUR per USDT, 480,000 reads 240.0k."""
    assert _book_lines(100, rate=Decimal("0.5")) == ["     book  depth 240.0k · fresh"]


def test_a_paused_feed_dims_a_stale_book_instead_of_amber() -> None:
    """Paused: the stale line is dim, not warning amber."""
    rows = _rows(100, look=_look(LiveState.PAUSED), books={"BTC": _health(age=34)})
    line = next(r for r in rows.renderables if isinstance(r, Text) and "stale" in r.plain)
    assert {str(span.style) for span in line.spans} | {str(line.style)} - {""} == {BASE.dim}


def test_a_live_stale_book_is_amber() -> None:
    """Live: the stale line carries the warning token."""
    rows = _rows(100, books={"BTC": _health(age=34)})
    line = next(r for r in rows.renderables if isinstance(r, Text) and "stale" in r.plain)
    assert {str(span.style) for span in line.spans} | {str(line.style)} - {""} == {BASE.warn}


def test_the_chip_shows_the_last_round_trip_a_sparkline_and_the_price_age() -> None:
    """Round trips 60, 90, 84 ms and a 3 s old price: '84ms', 8 braille cells, '3s'."""
    chip = latency_chip(
        [Decimal(60), Decimal(90), Decimal(84)], timedelta(seconds=3), BASE, room=30
    )
    assert (chip.plain.startswith(" 84ms "), chip.plain.endswith(" 3s"), chip.cell_len) == (
        True,
        True,
        1 + 5 + 8 + 3,
    )


def test_the_chip_drops_the_sparkline_when_short_of_room() -> None:
    """With 10 cells the chip is ' 84ms 3s'."""
    chip = latency_chip([Decimal(84)], timedelta(seconds=3), BASE, room=10)
    assert chip.plain == " 84ms 3s"


def test_the_chip_is_empty_without_a_poll_or_room() -> None:
    """No round trip, or 5 cells for a chip that needs 8, gives nothing."""
    short = latency_chip([Decimal(84)], timedelta(seconds=3), BASE, room=5)
    none = latency_chip([], timedelta(seconds=3), BASE, room=30)
    assert (short.plain, none.plain) == ("", "")


# ----------------------------------------------------------------------
# Through the app
# ----------------------------------------------------------------------

_FROZEN = datetime(2026, 10, 3, 12, 12, tzinfo=UTC)


class _Ticker:
    """A price table; with books it can also give an order book."""

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.book_calls = 0

    def get_ticker(self, pair: Pair) -> Ticker:
        price = Decimal(126)
        return Ticker(
            pair=pair,
            best_bid=price - 1,
            best_ask=price + 1,
            last_price=price,
            snapshot_time=_FROZEN,
        )


class _TickerWithBooks(_Ticker):
    def get_orderbook(self, pair: Pair, *, depth: int = 10) -> Orderbook:
        self.book_calls += 1
        if self.fail:
            raise KrakenPublicError("no book")
        return Orderbook(
            pair=pair,
            bids=(OrderbookLevel(Decimal(125), Decimal(1000)),),
            asks=(OrderbookLevel(Decimal(127), Decimal(2000)),),
            snapshot_time=_FROZEN,
        )


def _texts(ticker: _Ticker) -> tuple[str, str]:
    async def drive() -> tuple[str, str]:
        app = HomeApp(
            FixtureReadModel.load("ok-two-positions"),
            clock=FakeClock(_FROZEN),
            motion=False,
            refresh_seconds=3600,
            exposure_cap=Decimal("0.15"),
            ticker=ticker,
        )
        async with app.run_test(size=(160, 45)) as pilot:
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            return app.plain_text("market"), app.plain_text("feed")

    return asyncio.run(drive())


def test_a_ticker_that_gives_books_fills_the_book_line_and_the_chip() -> None:
    """The Market panel gains a depth line marked fresh, and the status row a round trip."""
    market, feed = _texts(_TickerWithBooks())
    assert ("depth " in market, " fresh" in market, "0ms" in feed) == (True, True, True)


def test_a_ticker_without_books_leaves_the_panel_as_before() -> None:
    """No get_orderbook: no book line, no chip, the prices are still there."""
    market, feed = _texts(_Ticker())
    assert ("depth " in market, "ms" in feed, "BTC" in market) == (False, False, True)


def test_a_failing_book_source_never_reaches_the_ui() -> None:
    """The book source raising leaves the live prices on screen and no book line."""
    ticker = _TickerWithBooks(fail=True)
    market, _ = _texts(ticker)
    assert (ticker.book_calls > 0, "depth " in market, "BTC" in market) == (True, False, True)
