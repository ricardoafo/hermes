"""Book depth, the fresh/stale flag and the round-trip ring are exact (issue #1292).

Invariants:

- depth is the quote value of the thinner side of the top levels, worked out
  by hand from the fixture below;
- a poll times each call on the console clock and never raises: a failing
  coin is a failed outcome with no depth;
- a book is fresh up to 20 s after its fetch and stale after; a failed poll
  keeps the previous good book, so its age keeps growing;
- the round-trip ring keeps the slowest good call of each poll, in
  milliseconds, oldest first, and a poll without a good call adds nothing.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from hypothesis import given
from hypothesis import strategies as st

from hermes.application.console.live_book import (
    STALE_AFTER,
    BookBoard,
    BookOutcome,
    BookPoller,
    book_depth,
)
from hermes.domain.errors import KrakenPublicError
from hermes.domain.market_data.orderbook import Orderbook, OrderbookLevel
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair
from tests.support.clock import FakeClock

_NOW = datetime(2026, 10, 19, 14, 20, tzinfo=UTC)
_PAIR = Pair(Currency("BTC"), Currency("USDT"))


def _book(bids: list[tuple[int, int]], asks: list[tuple[int, int]]) -> Orderbook:
    return Orderbook(
        pair=_PAIR,
        bids=tuple(OrderbookLevel(Decimal(p), Decimal(q)) for p, q in bids),
        asks=tuple(OrderbookLevel(Decimal(p), Decimal(q)) for p, q in asks),
        snapshot_time=_NOW,
    )


def test_depth_is_the_value_of_the_thinner_side() -> None:
    """Bids 100 x 3 + 99 x 2 = 498; asks 101 x 1 + 102 x 1 = 203; the thinner side is 203."""
    book = _book([(100, 3), (99, 2)], [(101, 1), (102, 1)])
    assert book_depth(book) == 203


def test_an_empty_side_has_zero_depth() -> None:
    """A book with no asks cannot be bought from: its depth is 0."""
    assert book_depth(_book([(100, 3)], [])) == 0


@given(
    bid_quantity=st.integers(min_value=1, max_value=1000),
    ask_quantity=st.integers(min_value=1, max_value=1000),
)
def test_depth_is_never_more_than_either_side(bid_quantity: int, ask_quantity: int) -> None:
    """For any sizes, depth equals the smaller of the two sides' values."""
    book = _book([(100, bid_quantity)], [(101, ask_quantity)])
    assert book_depth(book) == min(100 * bid_quantity, 101 * ask_quantity)


class _FakeBooks:
    """Answers each coin from a table, moving the clock by ``take`` per call."""

    def __init__(
        self,
        clock: FakeClock,
        books: dict[str, Orderbook],
        take: timedelta = timedelta(0),
        fail: set[str] | None = None,
    ) -> None:
        self.clock = clock
        self.books = books
        self.take = take
        self.fail = fail or set()
        self.requests: list[tuple[str, int]] = []

    def get_orderbook(self, pair: Pair, *, depth: int = 10) -> Orderbook:
        self.requests.append((pair.base.value, depth))
        self.clock.advance(self.take)
        if pair.base.value in self.fail:
            raise KrakenPublicError("boom")
        return self.books[pair.base.value]


def test_the_poller_asks_every_coin_for_ten_levels() -> None:
    """One request per coin, in order, with depth 10."""
    clock = FakeClock(_NOW)
    fake = _FakeBooks(clock, {"BTC": _book([(100, 1)], [(101, 1)])})
    BookPoller(fake, clock, coins=("BTC",)).fetch()
    assert fake.requests == [("BTC", 10)]


def test_the_poller_times_each_call_on_the_console_clock() -> None:
    """A call that takes 84 ms on the clock is an outcome with a round trip of 84 ms."""
    clock = FakeClock(_NOW)
    fake = _FakeBooks(
        clock, {"BTC": _book([(100, 1)], [(101, 1)])}, take=timedelta(milliseconds=84)
    )
    [outcome] = BookPoller(fake, clock, coins=("BTC",)).fetch()
    assert (outcome.round_trip, outcome.depth, outcome.at) == (
        timedelta(milliseconds=84),
        Decimal(100),
        _NOW + timedelta(milliseconds=84),
    )


def test_a_failing_coin_is_a_failed_outcome_and_the_poll_goes_on() -> None:
    """ETH fails with the error's name; BTC is still answered."""
    clock = FakeClock(_NOW)
    book = _book([(100, 1)], [(101, 1)])
    fake = _FakeBooks(clock, {"BTC": book}, fail={"ETH"})
    btc, eth = BookPoller(fake, clock, coins=("BTC", "ETH")).fetch()
    assert (btc.depth, eth.depth, eth.error) == (Decimal(100), None, "KrakenPublicError")


def test_the_poller_is_due_every_ten_seconds() -> None:
    """Due at once, not at 9 s, due at 10 s."""
    poller = BookPoller(_FakeBooks(FakeClock(_NOW), {}), FakeClock(_NOW))
    first = poller.due(_NOW)
    poller.begin(_NOW)
    assert (
        first,
        poller.due(_NOW + timedelta(seconds=9)),
        poller.due(_NOW + timedelta(seconds=10)),
    ) == (True, False, True)


def _good(at: datetime, depth: int = 500, ms: int = 80, coin: str = "BTC") -> BookOutcome:
    return BookOutcome(coin, at, timedelta(milliseconds=ms), Decimal(depth))


def test_a_book_is_fresh_up_to_twenty_seconds_and_stale_after() -> None:
    """At 20 s the book is fresh, at 21 s it is stale, and both still carry its depth."""
    board = BookBoard()
    board.apply([_good(_NOW)])
    fresh = board.health("BTC", _NOW + STALE_AFTER)
    stale = board.health("BTC", _NOW + STALE_AFTER + timedelta(seconds=1))
    assert fresh is not None
    assert stale is not None
    assert (fresh.fresh, stale.fresh, stale.depth, stale.age) == (
        True,
        False,
        Decimal(500),
        timedelta(seconds=21),
    )


def test_a_failed_poll_keeps_the_last_good_book_so_it_ages() -> None:
    """After a failure the old depth stays and its age counts from the good fetch."""
    board = BookBoard()
    board.apply([_good(_NOW)])
    later = _NOW + timedelta(seconds=30)
    board.apply([BookOutcome("BTC", later, timedelta(0), None, "KrakenPublicError")])
    health = board.health("BTC", later)
    assert health is not None
    assert (health.fresh, health.age, health.depth) == (False, timedelta(seconds=30), Decimal(500))


def test_a_coin_never_fetched_has_no_health() -> None:
    """The board knows BTC after a good fetch and nothing about SOL."""
    board = BookBoard()
    board.apply([_good(_NOW)])
    assert [board.health(coin, _NOW) is not None for coin in ("BTC", "SOL")] == [True, False]


def test_the_ring_keeps_the_slowest_good_call_of_each_poll_oldest_first() -> None:
    """Poll one: 80 ms and 120 ms give 120; poll two: only 60 ms; poll three has no good call."""
    board = BookBoard()
    board.apply([_good(_NOW, ms=80), _good(_NOW, ms=120, coin="ETH")])
    board.apply([_good(_NOW, ms=60)])
    board.apply([BookOutcome("BTC", _NOW, timedelta(seconds=5), None, "KrakenPublicError")])
    assert board.round_trips() == (Decimal(120), Decimal(60))


def test_the_ring_forgets_the_oldest_poll_when_full() -> None:
    """A ring of 2 holds the last two polls."""
    board = BookBoard(ring=2)
    for ms in (10, 20, 30):
        board.apply([_good(_NOW, ms=ms)])
    assert board.round_trips() == (Decimal(20), Decimal(30))
