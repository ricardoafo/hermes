"""The slow feeds start when due, run once at a time and never reach the UI with an error (#1292).

Invariants:

- a source that gives bars and books starts both of those feeds, one worker
  each, named after the feed, and every source with a ticker also starts the
  EUR quote feed; a source that gives neither bars nor books starts only that one;
- a feed that is running is not started again until its answer is applied, and
  is due again only after its cadence;
- the repaint runs on the UI thread after every answer, and a closing app
  (``call_from_thread`` raising ``RuntimeError``) drops the answer silently;
- what the feeds fetched is what the readers return: fresh contexts and book
  healths for the asked coins only, the round trips of the book calls and the
  newest EUR quote.
"""

from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from hermes.domain.errors import KrakenPublicError
from hermes.domain.market_data.kline import Kline, KlineInterval
from hermes.domain.market_data.orderbook import Orderbook, OrderbookLevel
from hermes.domain.market_data.ticker import Ticker
from hermes.domain.value_objects.pair import Pair
from hermes.entrypoints.tui_feeds import ContextFeeds
from tests.support.clock import FakeClock
from tests.support.hourly_bars import hourly_bars

_NOW = datetime(2026, 10, 19, 14, 20, tzinfo=UTC)


class _Source:
    """Gives hourly bars and books for any coin, and 0.8908 / 0.8910 for the euro quote."""

    def get_ticker(self, pair: Pair) -> Ticker:
        return Ticker(pair, Decimal("0.8908"), Decimal("0.8910"), Decimal("0.8909"), _NOW)

    def fetch_klines(
        self, *, pair: Pair, interval: KlineInterval, since: datetime, until: datetime
    ) -> Iterator[Kline]:
        bars: list[Kline] = hourly_bars(pair.base.value, _NOW)
        yield from (bar for bar in bars if since <= bar.open_time < until)

    def get_orderbook(self, pair: Pair, *, depth: int = 10) -> Orderbook:
        return Orderbook(
            pair=pair,
            bids=(OrderbookLevel(Decimal(100), Decimal(3)),),
            asks=(OrderbookLevel(Decimal(101), Decimal(2)),),
            snapshot_time=_NOW,
        )


class _PricesOnly:
    """Gives neither bars nor books; its ticker fails."""

    def get_ticker(self, pair: Pair) -> Ticker:
        raise KrakenPublicError("no quote")


class _App:
    """Runs a worker at once, as the UI thread would after the thread returned."""

    def __init__(self, *, closing: bool = False) -> None:
        self.workers: list[str] = []
        self.closing = closing
        self.held: list[Callable[[], None]] = []
        self.hold = False

    def run_worker(
        self, work: Callable[[], None], /, *, name: str, group: str, **options: bool
    ) -> None:
        self.workers.append(name)
        if self.hold:
            self.held.append(work)
        else:
            work()

    def call_from_thread(self, fn: Callable[..., None], *args: Any) -> None:
        if self.closing:
            raise RuntimeError("app is closing")
        fn(*args)


def _feeds(source: object, clock: FakeClock | None = None) -> ContextFeeds:
    return ContextFeeds(source, clock or FakeClock(_NOW))


def _start(feeds: ContextFeeds, app: _App, now: datetime, repaints: list[int]) -> None:
    feeds.start(app, now, lambda: repaints.append(1))


def test_a_source_with_bars_and_books_starts_one_worker_per_feed() -> None:
    """All feeds are due at once: 'bars', 'books' and 'rate' workers, a repaint for each."""
    app, repaints = _App(), list[int]()
    _start(_feeds(_Source()), app, _NOW, repaints)
    assert (app.workers, len(repaints)) == (["bars", "books", "rate"], 3)


def test_a_source_with_only_prices_starts_only_the_rate_feed() -> None:
    """A prices-only ticker leaves bars and books off; it can still give the EUR quote."""
    app, repaints = _App(), list[int]()
    _start(_feeds(_PricesOnly()), app, _NOW, repaints)
    assert (app.workers, repaints) == (["rate"], [1])


def test_no_ticker_starts_nothing() -> None:
    """Demo data has no ticker: no worker and no repaint."""
    app, repaints = _App(), list[int]()
    _start(ContextFeeds(None, FakeClock(_NOW)), app, _NOW, repaints)
    assert (app.workers, repaints) == ([], [])


def test_a_running_feed_is_not_started_again() -> None:
    """While the books answer is held, 10 s later no second worker starts for it."""
    app, repaints = _App(), list[int]()
    app.hold = True
    feeds = _feeds(_Source())
    _start(feeds, app, _NOW, repaints)
    _start(feeds, app, _NOW + timedelta(seconds=10), repaints)
    assert app.workers == ["bars", "books", "rate"]


def test_a_feed_is_due_again_after_its_cadence() -> None:
    """Books every 10 s, rate every 30 s, bars every 60 s: what starts at +10, +30 and +60 s."""
    app, repaints = _App(), list[int]()
    feeds = _feeds(_Source())
    _start(feeds, app, _NOW, repaints)
    _start(feeds, app, _NOW + timedelta(seconds=10), repaints)
    _start(feeds, app, _NOW + timedelta(seconds=30), repaints)
    _start(feeds, app, _NOW + timedelta(seconds=60), repaints)
    assert app.workers == [
        "bars",
        "books",
        "rate",
        "books",
        "books",
        "rate",
        "bars",
        "books",
        "rate",
    ]


def test_a_closing_app_drops_the_answer_without_raising() -> None:
    """call_from_thread raising RuntimeError (app closing): no repaint, no error."""
    app, repaints = _App(closing=True), list[int]()
    _start(_feeds(_Source()), app, _NOW, repaints)
    assert repaints == []


def test_the_readers_return_what_the_feeds_fetched() -> None:
    """BTC has a context and a book (depth: asks 101 x 2 = 202, the thinner side); XRP has none."""
    app, repaints = _App(), list[int]()
    feeds = _feeds(_Source())
    _start(feeds, app, _NOW, repaints)
    contexts = feeds.contexts(["BTC", "XRP"], _NOW)
    healths = feeds.healths(["BTC", "XRP"], _NOW)
    assert (list(contexts), list(healths), healths["BTC"].depth) == (["BTC"], ["BTC"], Decimal(202))


def test_the_round_trips_are_the_book_calls_in_milliseconds() -> None:
    """One poll with an instant clock gives one round trip of 0 ms."""
    app, repaints = _App(), list[int]()
    feeds = _feeds(_Source())
    _start(feeds, app, _NOW, repaints)
    assert feeds.round_trips() == (Decimal(0),)


def test_the_rate_reader_returns_the_middle_of_the_euro_quote() -> None:
    """Bid 0.8908 and ask 0.8910 read 0.8909, fresh; a prices-only failing ticker reads none."""
    app, repaints = _App(), list[int]()
    good, failing = _feeds(_Source()), _feeds(_PricesOnly())
    _start(good, app, _NOW, repaints)
    _start(failing, app, _NOW, repaints)
    quote = good.rate(_NOW)
    assert quote is not None
    assert (quote.eur_per_usdt, quote.fresh, failing.rate(_NOW)) == (Decimal("0.8909"), True, None)
