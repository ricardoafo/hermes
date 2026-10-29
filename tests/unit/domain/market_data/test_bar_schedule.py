"""Unit and property tests for :mod:`hermes.domain.market_data.bar_schedule`.

Invariants (live-bar-stream change D-3 and D-5):

- a bar is never closed before ``open + interval + settle`` on the EARLIER of
  the two clocks, so clock skew can only delay a bar;
- boundaries are UTC-epoch aligned;
- delivery order is ``(close_time, larger interval first, pair)``;
- :func:`plan_delivery` never delivers a bar twice, never lets a 4h bar
  precede the 1d bar closing at the same instant, and reports skipped bars.
"""

import itertools
import random
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.bar_schedule import (
    Stream,
    close_time_of,
    delivery_key,
    interval_duration,
    is_closed,
    next_boundary,
    order_for_delivery,
    plan_delivery,
    previous_boundary,
)
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair

_BTC = Pair(base=Currency.BTC, quote=Currency.USDT)
_ETH = Pair(base=Currency.ETH, quote=Currency.USDT)
_SOL = Pair(base=Currency.SOL, quote=Currency.USDT)
_H4 = KlineInterval.H4
_D1 = KlineInterval.D1
_SETTLE = timedelta(seconds=5)
_MIDNIGHT = datetime(2026, 10, 2, 0, 0, tzinfo=UTC)

_moments = st.datetimes(
    min_value=datetime(2020, 1, 1),
    max_value=datetime(2032, 1, 1),
    timezones=st.just(UTC),
)
_intervals = st.sampled_from(list(KlineInterval))
_offsets = st.timedeltas(min_value=timedelta(hours=-2), max_value=timedelta(hours=2))


def _bar(pair: Pair, interval: KlineInterval, open_time: datetime) -> Bar:
    """Build a valid flat bar for ``(pair, interval, open_time)``."""
    price = Decimal("100")
    return Bar(
        pair=pair,
        interval=interval,
        open_time=open_time,
        open=price,
        high=price,
        low=price,
        close=price,
        volume=Decimal("1"),
        close_time=close_time_of(open_time, interval),
    )


class TestIsClosed:
    """`is_closed` releases a bar only after boundary plus settle on both clocks."""

    @given(open_time=_moments, interval=_intervals, local_offset=_offsets, exchange_offset=_offsets)
    def test_bar_is_never_closed_before_boundary_plus_settle(
        self,
        open_time: datetime,
        interval: KlineInterval,
        local_offset: timedelta,
        exchange_offset: timedelta,
    ) -> None:
        """No pair of clock readings earlier than boundary + settle may close a bar."""
        boundary_plus_settle = open_time + interval_duration(interval) + _SETTLE
        closed = is_closed(
            open_time,
            interval,
            local_now=boundary_plus_settle + local_offset,
            exchange_now=boundary_plus_settle + exchange_offset,
            settle=_SETTLE,
        )
        if min(local_offset, exchange_offset) < timedelta(0):
            assert closed is False
        else:
            assert closed is True

    @given(
        open_time=_moments,
        interval=_intervals,
        local_now=_moments,
        ahead=st.timedeltas(min_value=timedelta(0), max_value=timedelta(days=2)),
    )
    def test_exchange_clock_ahead_never_advances_closure(
        self, open_time: datetime, interval: KlineInterval, local_now: datetime, ahead: timedelta
    ) -> None:
        """An exchange clock ahead of the local one gives the same answer as agreeing clocks."""
        assert is_closed(
            open_time,
            interval,
            local_now=local_now,
            exchange_now=local_now + ahead,
            settle=_SETTLE,
        ) == is_closed(
            open_time, interval, local_now=local_now, exchange_now=local_now, settle=_SETTLE
        )

    def test_four_hour_bar_closes_exactly_at_boundary_plus_settle(self) -> None:
        """The 12:00 4h bar is open at 16:00:04.999 and closed at 16:00:05 (D-3 sample)."""
        open_time = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
        boundary = datetime(2026, 10, 2, 16, 0, tzinfo=UTC)
        just_before = boundary + timedelta(seconds=4, milliseconds=999)
        at_settle = boundary + _SETTLE
        assert not is_closed(
            open_time, _H4, local_now=just_before, exchange_now=just_before, settle=_SETTLE
        )
        assert is_closed(
            open_time, _H4, local_now=at_settle, exchange_now=at_settle, settle=_SETTLE
        )

    def test_forming_bar_is_not_closed(self) -> None:
        """The 20:00 bar at 22:10 is still forming (the recorded 4h sample, D-3)."""
        open_time = datetime(2026, 10, 2, 20, 0, tzinfo=UTC)
        now = datetime(2026, 10, 2, 22, 10, 57, tzinfo=UTC)
        assert not is_closed(open_time, _H4, local_now=now, exchange_now=now, settle=_SETTLE)

    def test_naive_datetime_is_rejected(self) -> None:
        """A naive reading is a bug at the call site, not a UTC guess."""
        naive = datetime(2026, 10, 2, 12, 0)
        aware = datetime(2026, 10, 2, 17, 0, tzinfo=UTC)
        with pytest.raises(ValueError, match="local_now must be UTC-aware"):
            is_closed(aware, _H4, local_now=naive, exchange_now=aware, settle=_SETTLE)


class TestBoundaries:
    """Boundaries are UTC-epoch aligned and strictly ordered around ``now``."""

    @given(now=_moments, interval=_intervals)
    def test_next_boundary_is_strictly_after_now_and_within_one_interval(
        self, now: datetime, interval: KlineInterval
    ) -> None:
        """The next boundary lies in (now, now + interval]."""
        boundary = next_boundary(now, interval)
        assert now < boundary <= now + interval_duration(interval)

    @given(now=_moments, interval=_intervals)
    def test_previous_boundary_is_at_or_before_now_and_next_is_one_interval_later(
        self, now: datetime, interval: KlineInterval
    ) -> None:
        """Previous <= now < next, exactly one interval apart."""
        previous = previous_boundary(now, interval)
        assert previous <= now < next_boundary(now, interval)
        assert next_boundary(now, interval) - previous == interval_duration(interval)

    @given(now=_moments, interval=_intervals)
    def test_boundaries_are_aligned_to_the_utc_epoch(
        self, now: datetime, interval: KlineInterval
    ) -> None:
        """Every boundary is a whole number of intervals after 1970-01-01T00:00Z."""
        epoch = datetime(1970, 1, 1, tzinfo=UTC)
        assert (next_boundary(now, interval) - epoch) % interval_duration(interval) == timedelta(0)

    def test_next_boundary_at_an_exact_boundary_is_the_following_one(self) -> None:
        """At 00:00:00 exactly, the next 4h boundary is 04:00 (strictly after)."""
        assert next_boundary(_MIDNIGHT, _H4) == _MIDNIGHT + timedelta(hours=4)

    def test_four_hour_boundaries_fall_at_zero_four_eight_twelve_sixteen_twenty(self) -> None:
        """4h candles open at 00, 04, 08, 12, 16, 20 UTC, as Binance does."""
        now = datetime(2026, 10, 2, 13, 30, tzinfo=UTC)
        assert previous_boundary(now, _H4) == datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
        assert next_boundary(now, _H4) == datetime(2026, 10, 2, 16, 0, tzinfo=UTC)

    def test_daily_boundary_is_utc_midnight(self) -> None:
        """1d candles open at 00:00 UTC."""
        now = datetime(2026, 10, 2, 13, 30, tzinfo=UTC)
        assert next_boundary(now, _D1) == datetime(2026, 10, 3, 0, 0, tzinfo=UTC)

    def test_naive_datetime_is_rejected(self) -> None:
        """A naive instant cannot be placed on the UTC grid."""
        with pytest.raises(ValueError, match="now must be UTC-aware"):
            next_boundary(datetime(2026, 10, 2, 13, 30), _H4)


class TestCloseTimeAndDuration:
    """`close_time_of` matches Binance's inclusive close; each interval has its candle length."""

    def test_close_time_is_open_plus_interval_minus_one_millisecond(self) -> None:
        """The recorded 12:00 4h bar closes at 15:59:59.999, the value Binance reports."""
        open_time = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
        assert close_time_of(open_time, _H4) == datetime(
            2026, 10, 2, 15, 59, 59, 999000, tzinfo=UTC
        )

    @pytest.mark.parametrize(
        ("interval", "length"),
        [
            (KlineInterval.M1, timedelta(minutes=1)),
            (KlineInterval.M5, timedelta(minutes=5)),
            (KlineInterval.H1, timedelta(hours=1)),
            (KlineInterval.H4, timedelta(hours=4)),
            (KlineInterval.D1, timedelta(days=1)),
        ],
    )
    def test_interval_duration_is_the_candle_length(
        self, interval: KlineInterval, length: timedelta
    ) -> None:
        """Each Binance interval code maps to its own candle length (written out by hand)."""
        assert interval_duration(interval) == length

    def test_close_time_of_rejects_a_naive_open_time(self) -> None:
        """A naive open time is rejected rather than read as UTC."""
        with pytest.raises(ValueError, match="open_time must be UTC-aware"):
            close_time_of(datetime(2026, 10, 2, 12, 0), _H4)


class TestDeliveryKey:
    """`delivery_key` orders by close time, then longer interval, then pair."""

    def test_delivery_puts_1d_before_4h_at_equal_close_time(self) -> None:
        """At midnight the 1d bar sorts before the 4h bar that closes at the same instant."""
        day = _bar(_BTC, _D1, _MIDNIGHT - timedelta(days=1))
        four_hour = _bar(_BTC, _H4, _MIDNIGHT - timedelta(hours=4))
        assert day.close_time == four_hour.close_time
        assert sorted([four_hour, day], key=delivery_key) == [day, four_hour]

    def test_delivery_orders_equal_instants_by_pair_text(self) -> None:
        """Same close time and interval: BTC, ETH, SOL (alphabetical by BASE/QUOTE)."""
        open_time = _MIDNIGHT - timedelta(hours=4)
        bars = [_bar(_SOL, _H4, open_time), _bar(_BTC, _H4, open_time), _bar(_ETH, _H4, open_time)]
        assert [str(b.pair) for b in sorted(bars, key=delivery_key)] == [
            "BTC/USDT",
            "ETH/USDT",
            "SOL/USDT",
        ]

    def test_delivery_orders_by_close_time_before_interval(self) -> None:
        """An earlier-closing 4h bar precedes a later-closing 1d bar."""
        early_four_hour = _bar(_BTC, _H4, _MIDNIGHT - timedelta(hours=8))
        later_day = _bar(_BTC, _D1, _MIDNIGHT - timedelta(days=1))
        assert sorted([later_day, early_four_hour], key=delivery_key) == [
            early_four_hour,
            later_day,
        ]


class TestOrderForDelivery:
    """`order_for_delivery` is the close-time, 1d-first order serve and the parity replay share."""

    @given(
        picks=st.lists(
            st.tuples(
                st.sampled_from([_BTC, _ETH, _SOL]),
                st.sampled_from([_H4, _D1]),
                st.integers(min_value=0, max_value=60),
            ),
            unique=True,
            max_size=30,
        ),
        seed=st.randoms(use_true_random=False),
    )
    @settings(max_examples=60)
    def test_order_is_close_time_then_1d_first_whatever_the_input_order(
        self, picks: list[tuple[Pair, KlineInterval, int]], seed: random.Random
    ) -> None:
        """Invariant: close time ascends, 1d precedes 4h on a tie, and input order is irrelevant."""
        bars = [
            _bar(pair, interval, _MIDNIGHT + interval_duration(interval) * n)
            for pair, interval, n in picks
        ]
        shuffled = list(bars)
        seed.shuffle(shuffled)

        ordered = order_for_delivery(shuffled)

        assert ordered == order_for_delivery(bars)
        assert [b.close_time for b in ordered] == sorted(b.close_time for b in bars)
        for earlier, later in itertools.pairwise(ordered):
            if earlier.close_time == later.close_time and earlier.pair == later.pair:
                assert (earlier.interval, later.interval) != (_H4, _D1)

    def test_open_time_order_would_put_the_4h_bar_before_the_1d_bar_it_closes_with(self) -> None:
        """#343 R-4: the 4h bar opening at 20:00 closes with the 1d bar that opened at 00:00."""
        day = _bar(_BTC, _D1, _MIDNIGHT)
        last_four_hour = _bar(_BTC, _H4, _MIDNIGHT + timedelta(hours=20))
        by_open_time = sorted([last_four_hour, day], key=lambda b: b.open_time)
        assert by_open_time == [day, last_four_hour]
        early_four_hour = _bar(_BTC, _H4, _MIDNIGHT + timedelta(hours=4))
        assert order_for_delivery([day, early_four_hour, last_four_hour]) == (
            early_four_hour,
            day,
            last_four_hour,
        )


def _streams(*pairs: Pair, intervals: tuple[KlineInterval, ...] = (_H4, _D1)) -> list[Stream]:
    """Return every ``pair x interval`` stream."""
    return [(pair, interval) for pair in pairs for interval in intervals]


def _cursors_before_midnight(streams: list[Stream]) -> dict[Stream, datetime]:
    """Cursors positioned so the bar closing at ``_MIDNIGHT`` is the next one due."""
    return {stream: _MIDNIGHT - 2 * interval_duration(stream[1]) for stream in streams}


class TestPlanDelivery:
    """`plan_delivery` completes a pair's boundary only when all its due intervals are visible."""

    def test_four_hour_bar_waits_for_the_daily_bar_closing_at_the_same_instant(self) -> None:
        """With the 1d bar not visible yet, the 4h bar at midnight is held, not delivered."""
        streams = _streams(_BTC)
        four_hour = _bar(_BTC, _H4, _MIDNIGHT - timedelta(hours=4))
        plan = plan_delivery({(_BTC, _H4): [four_hour]}, _cursors_before_midnight(streams))
        assert plan.bars == ()
        assert plan.cursors[(_BTC, _H4)] == _MIDNIGHT - timedelta(hours=8)

    def test_both_bars_are_delivered_daily_first_once_both_are_visible(self) -> None:
        """Once the 1d bar shows up the pair's midnight boundary delivers 1d, then 4h."""
        streams = _streams(_BTC)
        day = _bar(_BTC, _D1, _MIDNIGHT - timedelta(days=1))
        four_hour = _bar(_BTC, _H4, _MIDNIGHT - timedelta(hours=4))
        plan = plan_delivery(
            {(_BTC, _H4): [four_hour], (_BTC, _D1): [day]}, _cursors_before_midnight(streams)
        )
        assert plan.bars == (day, four_hour)

    def test_cursor_advances_to_the_open_time_of_the_last_delivered_bar(self) -> None:
        """Delivered streams move their cursor to the delivered bar's open time."""
        streams = _streams(_BTC)
        day = _bar(_BTC, _D1, _MIDNIGHT - timedelta(days=1))
        four_hour = _bar(_BTC, _H4, _MIDNIGHT - timedelta(hours=4))
        plan = plan_delivery(
            {(_BTC, _H4): [four_hour], (_BTC, _D1): [day]}, _cursors_before_midnight(streams)
        )
        assert plan.cursors == {
            (_BTC, _H4): four_hour.open_time,
            (_BTC, _D1): day.open_time,
        }

    def test_bars_at_or_before_the_cursor_are_never_delivered_again(self) -> None:
        """An overlapping poll that repeats a delivered bar delivers nothing."""
        stale = _bar(_BTC, _H4, _MIDNIGHT - timedelta(hours=8))
        plan = plan_delivery({(_BTC, _H4): [stale]}, {(_BTC, _H4): stale.open_time})
        assert plan.bars == ()

    def test_pairs_are_independent_one_pair_waiting_does_not_hold_another(self) -> None:
        """ETH is delivered while BTC waits for its 1d bar."""
        streams = _streams(_BTC, _ETH)
        eth_day = _bar(_ETH, _D1, _MIDNIGHT - timedelta(days=1))
        eth_four_hour = _bar(_ETH, _H4, _MIDNIGHT - timedelta(hours=4))
        btc_four_hour = _bar(_BTC, _H4, _MIDNIGHT - timedelta(hours=4))
        plan = plan_delivery(
            {
                (_BTC, _H4): [btc_four_hour],
                (_ETH, _H4): [eth_four_hour],
                (_ETH, _D1): [eth_day],
            },
            _cursors_before_midnight(streams),
        )
        assert plan.bars == (eth_day, eth_four_hour)

    def test_a_held_boundary_holds_every_later_boundary_of_the_same_pair(self) -> None:
        """A 4h bar after an incomplete midnight is held too, so order is never broken."""
        streams = _streams(_BTC)
        midnight_four_hour = _bar(_BTC, _H4, _MIDNIGHT - timedelta(hours=4))
        later_four_hour = _bar(_BTC, _H4, _MIDNIGHT)
        plan = plan_delivery(
            {(_BTC, _H4): [midnight_four_hour, later_four_hour]},
            _cursors_before_midnight(streams),
        )
        assert plan.bars == ()

    def test_skipped_bar_is_reported_as_a_gap_and_later_bars_still_flow(self) -> None:
        """A missing 4h bar with a later one present is a gap, not a reason to stall."""
        stream = (_BTC, _H4)
        first_missing = _MIDNIGHT - timedelta(hours=8)
        after_gap = _bar(_BTC, _H4, _MIDNIGHT - timedelta(hours=4))
        plan = plan_delivery({stream: [after_gap]}, {stream: _MIDNIGHT - timedelta(hours=12)})
        assert plan.bars == (after_gap,)
        assert plan.gaps == ((stream, first_missing),)

    def test_streams_without_fetched_bars_leave_the_plan_empty(self) -> None:
        """Nothing fetched, nothing delivered, cursors unchanged."""
        cursors = _cursors_before_midnight(_streams(_BTC))
        plan = plan_delivery({}, cursors)
        assert plan.bars == ()
        assert plan.cursors == cursors

    @settings(max_examples=60)
    @given(
        hold_daily=st.booleans(),
        offsets=st.lists(st.integers(min_value=0, max_value=11), min_size=1, max_size=12),
    )
    def test_delivery_is_sorted_unique_and_never_puts_four_hour_before_daily(
        self, hold_daily: bool, offsets: list[int]
    ) -> None:
        """Whatever subset of 4h bars is visible, output is sorted, unique, 1d-first at ties."""
        streams = _streams(_BTC)
        start = _MIDNIGHT - timedelta(days=2)
        cursors = {stream: start - interval_duration(stream[1]) for stream in streams}
        four_hours = [
            _bar(_BTC, _H4, start + timedelta(hours=4 * offset)) for offset in sorted(set(offsets))
        ]
        daily = (
            []
            if hold_daily
            else [_bar(_BTC, _D1, start), _bar(_BTC, _D1, start + timedelta(days=1))]
        )
        plan = plan_delivery({(_BTC, _H4): four_hours, (_BTC, _D1): daily}, cursors)
        keys = [delivery_key(bar) for bar in plan.bars]
        assert keys == sorted(keys)
        assert len(set(keys)) == len(keys)
        for index, bar in enumerate(plan.bars):
            if bar.interval is _H4:
                same_instant_daily = [
                    other
                    for other in plan.bars[index + 1 :]
                    if other.interval is _D1 and other.close_time == bar.close_time
                ]
                assert same_instant_daily == []
