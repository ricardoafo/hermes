"""Tests for M-09 and M-10 over a whole shadow window (#965).

Invariants protected here (openspec/changes/965-shadow-parity-window/change.md):

- a round trip runs from a pair's first BUY while flat to the SELL that flattens
  it; its net P&L subtracts every fee; an open position is not a round trip;
- a shadow round trip matches a replay one of the same pair entered at most one
  4h bar before it, one to one; anything else is left out of M-09;
- the shadow's daily equity is rebuilt from its trades and the stored bars,
  and returns are taken over the 1D closes both curves have;
- the verdicts are statuses only, and identical sides pass both metrics.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hermes.application.calibration.shadow_parity import MetricStatus
from hermes.application.calibration.shadow_window import (
    RoundTrip,
    ShadowWindowError,
    aligned_returns,
    matched_pnl,
    round_trips,
    shadow_daily_equity,
    window_verdicts,
)
from hermes.domain.events.trade_executed import TradeExecuted, TradeSide
from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.price import Price
from hermes.domain.value_objects.quantity import Quantity
from tests.support.fill_kernel import BTC_USDT, STRATEGY, seed

ETH_USDT = Pair(base=Currency.ETH, quote=Currency.USDT)
T0 = datetime(2026, 10, 3, tzinfo=UTC)
H4 = timedelta(hours=4)


def _trade(
    at: datetime,
    side: TradeSide,
    *,
    qty: str = "1",
    price: str = "100",
    fee: str = "0",
    pair: Pair = BTC_USDT,
) -> TradeExecuted:
    return TradeExecuted(
        occurred_at=at,
        correlation_id="01J9Z8T3M5BQR2FVKN4XWY6PH1",
        pair=pair,
        side=side,
        qty=Quantity(amount=Decimal(qty), asset=pair.base),
        price=Price(amount=Decimal(price), pair=pair),
        fee=Money(amount=Decimal(fee), currency=Currency.USDT),
        strategy_id=STRATEGY,
    )


def _bar(close_time: datetime, close: str, interval: KlineInterval = KlineInterval.H4) -> Bar:
    span = timedelta(days=1) if interval is KlineInterval.D1 else H4
    price = Decimal(close)
    return Bar(
        pair=BTC_USDT,
        interval=interval,
        open_time=close_time - span,
        open=price,
        high=price,
        low=price,
        close=price,
        volume=Decimal(1000),
        close_time=close_time,
    )


def _trip(entered_at: datetime, pnl: str, pair: Pair = BTC_USDT) -> RoundTrip:
    return RoundTrip(pair=pair, entered_at=entered_at, net_pnl=Decimal(pnl))


class TestRoundTrips:
    """Flat to flat per pair, net of every fee."""

    def test_net_pnl_is_proceeds_minus_cost_minus_both_fees(self) -> None:
        """Buy 2 at 100 (fee 0.2), sell 2 at 110 (fee 0.22): 20 - 0.42 = 19.58."""
        trades = [
            _trade(T0, TradeSide.BUY, qty="2", price="100", fee="0.2"),
            _trade(T0 + H4, TradeSide.SELL, qty="2", price="110", fee="0.22"),
        ]

        assert round_trips(trades) == [_trip(T0, "19.58")]

    def test_a_round_trip_starts_at_the_first_buy_and_spans_partial_fills(self) -> None:
        """Two buys and two sells make one trip, entered at the first buy."""
        trades = [
            _trade(T0, TradeSide.BUY, qty="1", price="100"),
            _trade(T0 + H4, TradeSide.BUY, qty="1", price="120"),
            _trade(T0 + 2 * H4, TradeSide.SELL, qty="1", price="130"),
            _trade(T0 + 3 * H4, TradeSide.SELL, qty="1", price="90"),
        ]

        assert round_trips(trades) == [_trip(T0, "0")]

    def test_a_position_still_open_is_not_a_round_trip(self) -> None:
        """Only the closed trip counts; the open ETH position is left out."""
        trades = [
            _trade(T0, TradeSide.BUY),
            _trade(T0 + H4, TradeSide.BUY, pair=ETH_USDT),
            _trade(T0 + 2 * H4, TradeSide.SELL, price="105"),
        ]

        assert round_trips(trades) == [_trip(T0, "5")]

    def test_a_sell_beyond_the_holding_is_an_error(self) -> None:
        """A SELL with nothing held is not a shadow run the metrics can read."""
        with pytest.raises(ShadowWindowError, match="exceeds the 0 held"):
            round_trips([_trade(T0, TradeSide.SELL)])


class TestMatching:
    """Same pair, replay entry at most one 4h bar before the shadow's."""

    def test_a_shadow_entry_seconds_after_the_replay_entry_matches(self) -> None:
        """Serve fills a few seconds after the bar close the replay fills at."""
        shadow = [_trip(T0 + timedelta(seconds=5), "4")]
        replay = [_trip(T0, "3")]

        assert matched_pnl(shadow, replay) == ([4.0], [3.0])

    def test_a_shadow_entry_one_full_bar_later_does_not_match(self) -> None:
        """Exactly one 4h bar apart is outside the window."""
        assert matched_pnl([_trip(T0 + H4, "4")], [_trip(T0, "3")]) == ([], [])

    def test_a_replay_entry_after_the_shadow_entry_does_not_match(self) -> None:
        """The replay fills at the bar close; serve cannot fill before it."""
        later = T0 + timedelta(seconds=1)

        assert matched_pnl([_trip(T0, "4")], [_trip(later, "3")]) == ([], [])

    def test_another_pair_does_not_match(self) -> None:
        """An ETH replay trip at the same time is not the BTC shadow trip's twin."""
        assert matched_pnl([_trip(T0, "4")], [_trip(T0, "3", pair=ETH_USDT)]) == ([], [])

    def test_each_replay_trip_matches_one_shadow_trip_only(self) -> None:
        """Two shadow trips in the same bar share one replay trip: only the first matches."""
        shadow = [_trip(T0 + timedelta(seconds=5), "4"), _trip(T0 + timedelta(hours=1), "9")]

        assert matched_pnl(shadow, [_trip(T0, "3")]) == ([4.0], [3.0])

    @given(
        shadow=st.lists(st.integers(min_value=0, max_value=60), max_size=12),
        replay=st.lists(st.integers(min_value=0, max_value=60), max_size=12),
    )
    def test_matches_are_one_to_one_and_never_exceed_either_side(
        self, shadow: list[int], replay: list[int]
    ) -> None:
        """However entries fall, no side is matched more often than it has trips.

        The shadow P&L encodes the trip index, so a repeated value would mean a
        shadow trip matched twice.
        """
        shadow_trips = [_trip(T0 + timedelta(hours=h), str(i)) for i, h in enumerate(shadow)]
        replay_trips = [_trip(T0 + timedelta(hours=h), str(-i - 1)) for i, h in enumerate(replay)]

        shadow_pnl, replay_pnl = matched_pnl(shadow_trips, replay_trips)

        assert len(shadow_pnl) == len(replay_pnl) <= min(len(shadow), len(replay))
        assert len(set(shadow_pnl)) == len(shadow_pnl)
        assert len(set(replay_pnl)) == len(replay_pnl)


class TestDailyEquity:
    """The shadow's curve is rebuilt from its trades and the stored bars."""

    def test_the_curve_marks_a_held_position_at_each_1d_close(self) -> None:
        """Buy 10 BTC at 100 from 5000 cash; the 1D closes at 110 and 90 give 5100 and 4900."""
        day1 = T0 + timedelta(days=1)
        day2 = T0 + timedelta(days=2)
        trades = [_trade(T0 + H4 + timedelta(seconds=5), TradeSide.BUY, qty="10")]
        bars = [
            _bar(T0 + H4, "100"),
            _bar(T0 + 2 * H4, "100"),
            _bar(day1, "110", KlineInterval.D1),
            _bar(day2, "90", KlineInterval.D1),
        ]

        points = shadow_daily_equity(seed(), trades, bars, start=T0)

        assert points == [(day1, Decimal(5100)), (day2, Decimal(4900))]

    def test_a_trade_at_the_1d_close_counts_from_the_next_point(self) -> None:
        """A fill recorded after the 1D bar closed is not in that close's point."""
        day1 = T0 + timedelta(days=1)
        day2 = T0 + timedelta(days=2)
        trades = [_trade(day1 + timedelta(seconds=5), TradeSide.BUY, qty="10")]
        bars = [_bar(day1, "100", KlineInterval.D1), _bar(day2, "110", KlineInterval.D1)]

        points = shadow_daily_equity(seed(), trades, bars, start=T0)

        assert points == [(day1, Decimal(5000)), (day2, Decimal(5100))]

    def test_a_1d_close_at_day_zero_is_not_a_point(self) -> None:
        """The window holds the closes after day 0 only, as the replay's curve does."""
        bars = [
            _bar(T0, "100", KlineInterval.D1),
            _bar(T0 + timedelta(days=1), "100", KlineInterval.D1),
        ]

        points = shadow_daily_equity(seed(), [], bars, start=T0)

        assert [at for at, _ in points] == [T0 + timedelta(days=1)]

    def test_returns_use_only_the_close_times_both_curves_have(self) -> None:
        """The replay lacks day 2, so the returns span day 1 to day 3 on both sides."""
        days = [T0 + timedelta(days=n) for n in (1, 2, 3)]
        shadow = [(days[0], Decimal(100)), (days[1], Decimal(50)), (days[2], Decimal(110))]
        replay = [(days[0], Decimal(200)), (days[2], Decimal(240))]

        assert aligned_returns(shadow, replay) == (
            [pytest.approx(0.1)],
            [pytest.approx(0.2)],
        )


class TestVerdicts:
    """Statuses only; identical sides pass."""

    def test_identical_shadow_and_replay_pass_both_metrics(self) -> None:
        """30 matched trips and 40 days of the same returns: M-09 and M-10 pass."""
        trades: list[TradeExecuted] = []
        for n in range(30):
            entry = T0 + n * 6 * H4
            exit_price = str(100 + (n % 7) - 3)
            trades += [
                _trade(entry, TradeSide.BUY),
                _trade(entry + H4, TradeSide.SELL, price=exit_price),
            ]
        equity = [
            (T0 + timedelta(days=d), Decimal(5000 + (d * 37) % 11 * 10)) for d in range(1, 41)
        ]

        m09, m10 = window_verdicts(
            shadow_trades=trades,
            replay_trades=trades,
            shadow_equity=equity,
            replay_equity=equity,
            resamples=200,
        )

        assert (m09.status, m10.status) == (MetricStatus.OK, MetricStatus.OK)

    def test_too_few_matched_trips_and_days_are_insufficient(self) -> None:
        """One trip and two days are below both metrics' floors."""
        trades = [_trade(T0, TradeSide.BUY), _trade(T0 + H4, TradeSide.SELL, price="101")]
        equity = [(T0 + timedelta(days=1), Decimal(5000)), (T0 + timedelta(days=2), Decimal(5001))]

        m09, m10 = window_verdicts(
            shadow_trades=trades,
            replay_trades=trades,
            shadow_equity=equity,
            replay_equity=equity,
            resamples=200,
        )

        assert (m09.status, m10.status) == (MetricStatus.INSUFFICIENT, MetricStatus.INSUFFICIENT)

    def test_verdicts_render_no_value(self) -> None:
        """A verdict's text names the metric and its status, never the sealed number."""
        trades = [_trade(T0, TradeSide.BUY), _trade(T0 + H4, TradeSide.SELL, price="101")]

        m09, m10 = window_verdicts(
            shadow_trades=trades, replay_trades=trades, shadow_equity=[], replay_equity=[]
        )

        assert (str(m09), str(m10)) == ("M-09: insufficient", "M-10: insufficient")
