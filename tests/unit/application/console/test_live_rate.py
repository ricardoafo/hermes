"""The live USDT/EUR quote is the middle of bid and ask and ages honestly (issue #1330).

Invariants:

- the quote is the middle of the best bid and the best ask, in euro per USDT,
  worked out by hand below;
- a poll asks for the USDT/EUR pair and never raises: a failing ticker is a
  failed outcome with no rate;
- a quote is fresh up to 90 s after its fetch and stale after; a failed poll
  keeps the previous good quote, so its age keeps growing;
- before the first good poll there is no quote.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from hypothesis import given
from hypothesis import strategies as st

from hermes.application.console.live_rate import (
    RATE_EVERY,
    RATE_STALE_AFTER,
    RateBoard,
    RateOutcome,
    RatePoller,
)
from hermes.domain.errors import KrakenPublicError
from hermes.domain.market_data.ticker import Ticker
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair
from tests.support.clock import FakeClock

_NOW = datetime(2026, 10, 19, 14, 20, tzinfo=UTC)


class _Ticker:
    """Answers the ticker of the pair it was asked for, or fails."""

    def __init__(self, bid: str = "0.8908", ask: str = "0.8909", *, fail: bool = False) -> None:
        self.bid, self.ask, self.fail = Decimal(bid), Decimal(ask), fail
        self.asked: list[Pair] = []

    def get_ticker(self, pair: Pair) -> Ticker:
        self.asked.append(pair)
        if self.fail:
            raise KrakenPublicError("down")
        return Ticker(pair, self.bid, self.ask, self.bid, _NOW)


def test_the_quote_is_the_middle_of_bid_and_ask() -> None:
    """Bid 0.8908 and ask 0.8910 give (0.8908 + 0.8910) / 2 = 0.8909."""
    port = _Ticker("0.8908", "0.8910")
    [outcome] = RatePoller(port, FakeClock(_NOW)).fetch()
    assert (outcome.eur_per_usdt, port.asked) == (
        Decimal("0.8909"),
        [Pair(Currency.USDT, Currency.EUR)],
    )


@given(
    bid=st.integers(min_value=1, max_value=10**6),
    spread=st.integers(min_value=1, max_value=10**4),
)
def test_the_quote_sits_between_bid_and_ask(bid: int, spread: int) -> None:
    """For any uncrossed book the middle is at least the bid and at most the ask."""
    port = _Ticker(str(Decimal(bid) / 10**4), str(Decimal(bid + spread) / 10**4))
    [outcome] = RatePoller(port, FakeClock(_NOW)).fetch()
    assert outcome.eur_per_usdt is not None
    assert port.bid <= outcome.eur_per_usdt <= port.ask


def test_a_failing_ticker_is_a_failed_outcome() -> None:
    """The exchange error is named, no rate, and nothing is raised."""
    [outcome] = RatePoller(_Ticker(fail=True), FakeClock(_NOW)).fetch()
    assert (outcome.eur_per_usdt, outcome.error) == (None, "KrakenPublicError")


def test_a_poll_is_due_first_then_every_thirty_seconds() -> None:
    """Due before any poll; not due 29 s after one began; due at 30 s."""
    poller = RatePoller(_Ticker(), FakeClock(_NOW))
    first = poller.due(_NOW)
    poller.begin(_NOW)
    assert (first, poller.due(_NOW + RATE_EVERY - timedelta(seconds=1))) == (True, False)
    assert poller.due(_NOW + RATE_EVERY)


def test_there_is_no_quote_before_the_first_good_poll() -> None:
    """A fresh board, and a board that only saw a failed poll, have no quote."""
    fresh, failed = RateBoard(), RateBoard()
    failed.apply([RateOutcome(_NOW, None, "KrakenPublicError")])
    assert (fresh.quote(_NOW), failed.quote(_NOW)) == (None, None)


def test_a_quote_is_fresh_for_ninety_seconds_then_stale() -> None:
    """At +90 s the quote is fresh; at +91 s it is stale and says how old it is."""
    board = RateBoard()
    board.apply([RateOutcome(_NOW, Decimal("0.89"))])
    at_limit = board.quote(_NOW + RATE_STALE_AFTER)
    after = board.quote(_NOW + RATE_STALE_AFTER + timedelta(seconds=1))
    assert at_limit is not None and after is not None
    assert (at_limit.fresh, after.fresh, after.age) == (True, False, timedelta(seconds=91))


def test_a_failed_poll_keeps_the_last_good_quote() -> None:
    """A good 0.89 at 14:20 and a failure at 14:21: still 0.89, now 60 s old."""
    board = RateBoard()
    board.apply([RateOutcome(_NOW, Decimal("0.89"))])
    board.apply([RateOutcome(_NOW + timedelta(seconds=60), None, "KrakenPublicError")])
    kept = board.quote(_NOW + timedelta(seconds=60))
    assert kept is not None
    assert (kept.eur_per_usdt, kept.age) == (Decimal("0.89"), timedelta(seconds=60))
