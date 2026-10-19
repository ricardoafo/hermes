"""The forming 4h bar and the 24 h context are built from hourly bars (issue #1289).

Invariants:

- from 25 hourly bars (the 24 h window plus the current hour) the 4h bar is the
  hourly bars since the last 4h boundary and the 24 h window starts at the
  hourly bar that opened 24 h before the current hour; every figure below is
  worked out by hand from the fixture;
- a live price beyond the bars' high or low widens the forming bar and the 24 h
  range, and the 24 h change is measured to the live price;
- a missing first hourly bar hides the figure that needs it (bar open, 24 h
  change) instead of guessing it;
- a forming bar whose 4h window has closed since the fetch is not shown;
- the poller asks for the hourly bars once a minute, never raises, and a failed
  coin keeps its last good context until it is older than five minutes.
"""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hermes.application.console.live_context import (
    STALE_AFTER,
    BarContext,
    BarsOutcome,
    BarsPoller,
    ContextBoard,
    bar_context,
)
from hermes.domain.errors import KrakenPublicError
from hermes.domain.market_data.kline import Kline, KlineInterval
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair
from tests.support.clock import FakeClock
from tests.support.hourly_bars import hourly_bars

_NOW = datetime(2026, 10, 19, 14, 20, tzinfo=UTC)
_DAY_START = datetime(2026, 10, 18, 14, 0, tzinfo=UTC)
_PAIR = Pair(Currency("BTC"), Currency("USDT"))


# Bars 0..24 open 14:00 on 18 Oct to 14:00 on 19 Oct; bars 22..24 are the 12:00, 13:00
# and 14:00 hours of the 4h bar that opened at 12:00.
_HOURS = hourly_bars("BTC", _NOW)


def _context() -> BarContext:
    context = bar_context("BTC", _HOURS, _NOW)
    assert context is not None
    return context


def test_the_4h_bar_is_built_from_the_hours_since_its_boundary() -> None:
    """Bars 22..24: open 122 (bar 22), high 129 (bar 24: 124 + 5), low 117 (bar 22: 122 - 5)."""
    bar = _context().forming(Decimal(126), _NOW)
    assert bar is not None
    assert (bar.open, bar.high, bar.low, bar.last) == (122, 129, 117, 126)
    assert bar.closes_at == datetime(2026, 10, 19, 16, 0, tzinfo=UTC)
    assert bar.left(_NOW) == timedelta(hours=1, minutes=40)


def test_the_24h_context_spans_the_25_hourly_bars() -> None:
    """Open 100, high 129, low 95 (bar 0: 100 - 5), volume 25 x 2 = 50, change 126 / 100 = +26 %."""
    day = _context().day(Decimal(126))
    assert day is not None
    assert (day.change_pct, day.high, day.low, day.volume) == (26, 129, 95, 50)


def test_a_live_price_beyond_the_bars_widens_the_ranges() -> None:
    """At 131 the bar high and the 24 h high are 131; at 90 both lows are 90."""
    context = _context()
    high_bar, high_day = context.forming(Decimal(131), _NOW), context.day(Decimal(131))
    low_bar, low_day = context.forming(Decimal(90), _NOW), context.day(Decimal(90))
    assert high_bar is not None
    assert high_day is not None
    assert low_bar is not None
    assert low_day is not None
    assert (high_bar.high, high_day.high, low_bar.low, low_day.low) == (131, 131, 90, 90)


def test_a_missing_first_hour_of_the_4h_bar_hides_the_forming_bar() -> None:
    """Without the 12:00 hourly bar the bar's open is unknown, so no forming bar is drawn."""
    context = bar_context("BTC", [k for k in _HOURS if k is not _HOURS[22]], _NOW)
    assert context is not None
    assert (context.forming(Decimal(126), _NOW), context.day(Decimal(126)) is not None) == (
        None,
        True,
    )


def test_a_missing_first_hour_of_the_window_hides_the_24h_change() -> None:
    """Without the bar that opened 24 h before the current hour there is no 24 h context."""
    context = bar_context("BTC", _HOURS[1:], _NOW)
    assert context is not None
    assert (context.day(Decimal(126)), context.forming(Decimal(126), _NOW) is not None) == (
        None,
        True,
    )


def test_no_hourly_bar_in_the_window_gives_no_context() -> None:
    """An empty answer, or bars all older than the window, is no context at all."""
    old = [k for k in _HOURS if k.open_time < _DAY_START + timedelta(hours=1)][:1]
    stale_now = _NOW + timedelta(days=2)
    assert (bar_context("BTC", [], _NOW), bar_context("BTC", old, stale_now)) == (None, None)


def test_a_bar_that_closed_since_the_fetch_is_not_shown() -> None:
    """At 16:00 sharp the 12:00 bar is over; one second before, 1 s is left."""
    closed = _context().forming(Decimal(126), datetime(2026, 10, 19, 16, 0, tzinfo=UTC))
    open_ = _context().forming(Decimal(126), datetime(2026, 10, 19, 15, 59, 59, tzinfo=UTC))
    assert (
        closed,
        None if open_ is None else open_.left(datetime(2026, 10, 19, 15, 59, 59, tzinfo=UTC)),
    ) == (
        None,
        timedelta(seconds=1),
    )


@given(
    last=st.decimals(min_value=Decimal(1), max_value=Decimal(1000), places=2),
)
def test_the_forming_bar_always_contains_open_and_the_live_price(last: Decimal) -> None:
    """For any live price: low <= open <= high and low <= last <= high."""
    bar = _context().forming(last, _NOW)
    assert bar is not None
    assert (bar.low <= bar.open <= bar.high, bar.low <= bar.last <= bar.high) == (True, True)


class _FakeBars:
    """Yields the given hourly bars, or raises; remembers every request."""

    def __init__(self, klines: list[Kline], fail: Exception | None = None) -> None:
        self.klines = klines
        self.fail = fail
        self.requests: list[tuple[str, KlineInterval, datetime, datetime]] = []

    def fetch_klines(
        self, *, pair: Pair, interval: KlineInterval, since: datetime, until: datetime
    ) -> Iterator[Kline]:
        self.requests.append((pair.base.value, interval, since, until))
        if self.fail is not None:
            raise self.fail
        yield from (k for k in self.klines if since <= k.open_time < until)


def test_the_poller_asks_for_hourly_bars_from_24h_before_the_current_hour() -> None:
    """At 14:20 each coin is asked for 1h bars in [14:00 on 18 Oct, 15:00 on 19 Oct)."""
    fake = _FakeBars(_HOURS)
    BarsPoller(fake, FakeClock(_NOW), coins=("BTC", "ETH")).fetch()
    window = (_DAY_START, datetime(2026, 10, 19, 15, 0, tzinfo=UTC))
    assert fake.requests == [
        ("BTC", KlineInterval.H1, *window),
        ("ETH", KlineInterval.H1, *window),
    ]


def test_the_poller_is_due_every_sixty_seconds() -> None:
    """Due at once, not at 59 s, due at 60 s."""
    poller = BarsPoller(_FakeBars([]), FakeClock(_NOW))
    first = poller.due(_NOW)
    poller.begin(_NOW)
    assert (
        first,
        poller.due(_NOW + timedelta(seconds=59)),
        poller.due(_NOW + timedelta(seconds=60)),
    ) == (
        True,
        False,
        True,
    )


def test_a_failing_source_becomes_a_failed_outcome_and_never_raises() -> None:
    """A KrakenPublicError is an outcome with the error name and no context."""
    fake = _FakeBars([], fail=KrakenPublicError("boom"))
    [outcome] = BarsPoller(fake, FakeClock(_NOW), coins=("BTC",)).fetch()
    assert (outcome.coin, outcome.context, outcome.error) == ("BTC", None, "KrakenPublicError")


def test_a_failed_poll_keeps_the_last_good_context_until_it_is_stale() -> None:
    """After a failure the old context still shows at 4m59s, and is gone at 5m01s."""
    board = ContextBoard()
    board.apply([BarsOutcome("BTC", _NOW, _context())])
    board.apply([BarsOutcome("BTC", _NOW + timedelta(minutes=1), None, "KrakenPublicError")])
    fresh = board.get("BTC", _NOW + STALE_AFTER - timedelta(seconds=1))
    stale = board.get("BTC", _NOW + STALE_AFTER + timedelta(seconds=1))
    assert (fresh is not None, stale) == (True, None)


def test_a_coin_never_fetched_has_no_context() -> None:
    """The board has BTC after a good fetch and nothing for SOL, which it never heard of."""
    board = ContextBoard()
    board.apply([BarsOutcome("BTC", _NOW, _context())])
    assert [board.get(coin, _NOW) is not None for coin in ("BTC", "SOL")] == [True, False]


@pytest.mark.parametrize("coin", ["BTC", "ETH"])
def test_the_context_carries_the_coin_and_the_fetch_time(coin: str) -> None:
    """The coin and the console-clock time of the fetch are kept for the stale check."""
    context = bar_context(coin, _HOURS, _NOW)
    assert context is not None
    assert (context.coin, context.at) == (coin, _NOW)
