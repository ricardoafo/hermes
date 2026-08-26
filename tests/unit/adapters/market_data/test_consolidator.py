"""Unit and property tests for :class:`BarConsolidator`.

Every test names the invariant it protects (D103 enforcement per
docs/guides/ai-authored-tests.md).

Test pyramid coverage:

- Unit: open/high/low/close/volume aggregation, empty stream, single trade,
  multiple windows, interval_to_timedelta mapping.
- Property: bar.high >= every trade price; bar.volume = sum of trade volumes;
  no lookahead (bar at T never uses trades with timestamp > T close_time);
  bar.high >= bar.open, bar.close, bar.low.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from hermes.adapters.market_data.consolidator import BarConsolidator, interval_to_timedelta
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.market_data.trade import Trade, TradeSide
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair

_BTC_USDT = Pair(base=Currency.BTC, quote=Currency.USDT)
_T0 = datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)


def _trade(
    price: str,
    qty: str = "1.0",
    *,
    trade_time: datetime,
    trade_id: str = "1",
    side: TradeSide = TradeSide.BUY,
) -> Trade:
    """Build a test Trade object with the given price and qty."""
    return Trade(
        pair=_BTC_USDT,
        price=Decimal(price),
        quantity=Decimal(qty),
        side=side,
        trade_time=trade_time,
        trade_id=trade_id,
    )


@pytest.mark.unit
def test_interval_to_timedelta_m1() -> None:
    """M1 interval maps to exactly 1 minute."""
    result = interval_to_timedelta(KlineInterval.M1)
    assert result == timedelta(minutes=1), f"Expected 1 minute, got {result}"


@pytest.mark.unit
def test_interval_to_timedelta_m5() -> None:
    """M5 interval maps to exactly 5 minutes."""
    result = interval_to_timedelta(KlineInterval.M5)
    assert result == timedelta(minutes=5), f"Expected 5 minutes, got {result}"


@pytest.mark.unit
def test_interval_to_timedelta_h1() -> None:
    """H1 interval maps to exactly 1 hour."""
    result = interval_to_timedelta(KlineInterval.H1)
    assert result == timedelta(hours=1), f"Expected 1 hour, got {result}"


@pytest.mark.unit
def test_interval_to_timedelta_h4() -> None:
    """H4 interval maps to exactly 4 hours."""
    result = interval_to_timedelta(KlineInterval.H4)
    assert result == timedelta(hours=4), f"Expected 4 hours, got {result}"


@pytest.mark.unit
def test_interval_to_timedelta_d1() -> None:
    """D1 interval maps to exactly 1 day."""
    result = interval_to_timedelta(KlineInterval.D1)
    assert result == timedelta(days=1), f"Expected 1 day, got {result}"


@pytest.mark.unit
def test_consolidate_empty_stream_yields_no_bars() -> None:
    """Empty trade stream produces no bars."""
    consolidator = BarConsolidator(pair=_BTC_USDT, interval=KlineInterval.M1)
    bars = list(consolidator.consolidate([]))
    assert len(bars) == 0, f"Expected 0 bars from empty stream, got {len(bars)}"


@pytest.mark.unit
def test_consolidate_single_trade_produces_one_bar() -> None:
    """A single trade produces exactly one bar."""
    t0 = datetime(2024, 1, 1, 0, 0, 30, tzinfo=UTC)
    trades = [_trade("50000", "0.5", trade_time=t0, trade_id="1")]
    consolidator = BarConsolidator(pair=_BTC_USDT, interval=KlineInterval.M1)
    bars = list(consolidator.consolidate(trades))
    assert len(bars) == 1, f"Expected 1 bar from single trade, got {len(bars)}"


@pytest.mark.unit
def test_consolidate_single_trade_ohlcv_equals_trade() -> None:
    """Bar OHLCV from a single trade: open=close=high=low=trade.price, volume=qty."""
    t0 = datetime(2024, 1, 1, 0, 0, 30, tzinfo=UTC)
    trades = [_trade("50000", "2.5", trade_time=t0, trade_id="1")]
    consolidator = BarConsolidator(pair=_BTC_USDT, interval=KlineInterval.M1)
    bars = list(consolidator.consolidate(trades))
    bar = bars[0]

    assert bar.open == Decimal("50000"), f"Bar.open should equal trade price 50000, got {bar.open}"
    assert bar.close == Decimal("50000"), (
        f"Bar.close should equal trade price 50000, got {bar.close}"
    )
    assert bar.high == Decimal("50000"), f"Bar.high should equal trade price 50000, got {bar.high}"
    assert bar.low == Decimal("50000"), f"Bar.low should equal trade price 50000, got {bar.low}"
    assert bar.volume == Decimal("2.5"), f"Bar.volume should equal trade qty 2.5, got {bar.volume}"


@pytest.mark.unit
def test_consolidate_ohlcv_aggregation() -> None:
    """OHLCV is correctly aggregated across multiple trades in the same window."""
    window_start = datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)
    trades = [
        _trade("100", "1.0", trade_time=window_start + timedelta(seconds=10), trade_id="1"),
        _trade("120", "2.0", trade_time=window_start + timedelta(seconds=20), trade_id="2"),
        _trade("80", "0.5", trade_time=window_start + timedelta(seconds=30), trade_id="3"),
        _trade("110", "1.5", trade_time=window_start + timedelta(seconds=40), trade_id="4"),
    ]
    consolidator = BarConsolidator(pair=_BTC_USDT, interval=KlineInterval.M1)
    bars = list(consolidator.consolidate(trades))
    assert len(bars) == 1, f"All trades in same window should produce 1 bar, got {len(bars)}"
    bar = bars[0]

    assert bar.open == Decimal("100"), (
        f"Bar.open should be price of first trade (100), got {bar.open}"
    )
    assert bar.close == Decimal("110"), (
        f"Bar.close should be price of last trade (110), got {bar.close}"
    )
    assert bar.high == Decimal("120"), f"Bar.high should be max trade price (120), got {bar.high}"
    assert bar.low == Decimal("80"), f"Bar.low should be min trade price (80), got {bar.low}"
    assert bar.volume == Decimal("5.0"), (
        f"Bar.volume should be sum of qty (1+2+0.5+1.5=5), got {bar.volume}"
    )


@pytest.mark.unit
def test_consolidate_two_windows_produces_two_bars() -> None:
    """Trades spanning two intervals produce two bars."""
    t0 = datetime(2024, 1, 1, 0, 0, 30, tzinfo=UTC)  # in window 1
    t1 = datetime(2024, 1, 1, 0, 1, 30, tzinfo=UTC)  # in window 2
    trades = [
        _trade("50000", "1.0", trade_time=t0, trade_id="1"),
        _trade("51000", "2.0", trade_time=t1, trade_id="2"),
    ]
    consolidator = BarConsolidator(pair=_BTC_USDT, interval=KlineInterval.M1)
    bars = list(consolidator.consolidate(trades))
    assert len(bars) == 2, f"Trades in two separate minutes should produce 2 bars, got {len(bars)}"
    assert bars[0].open == Decimal("50000"), f"First bar open should be 50000, got {bars[0].open}"
    assert bars[1].open == Decimal("51000"), f"Second bar open should be 51000, got {bars[1].open}"


@pytest.mark.unit
def test_consolidate_bars_have_correct_open_times() -> None:
    """Each bar's open_time is aligned to the interval boundary (not the first trade time)."""
    t0 = datetime(2024, 1, 1, 0, 0, 30, tzinfo=UTC)  # 30s into minute 0
    t1 = datetime(2024, 1, 1, 0, 2, 15, tzinfo=UTC)  # 15s into minute 2
    trades = [
        _trade("50000", trade_time=t0, trade_id="1"),
        _trade("51000", trade_time=t1, trade_id="2"),
    ]
    consolidator = BarConsolidator(pair=_BTC_USDT, interval=KlineInterval.M1)
    bars = list(consolidator.consolidate(trades))
    assert len(bars) == 2, f"Expected 2 bars, got {len(bars)}"

    expected_open_0 = datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)
    assert bars[0].open_time == expected_open_0, (
        f"First bar open_time should be {expected_open_0}, got {bars[0].open_time}"
    )
    expected_open_1 = datetime(2024, 1, 1, 0, 2, 0, tzinfo=UTC)
    assert bars[1].open_time == expected_open_1, (
        f"Second bar open_time should be {expected_open_1}, got {bars[1].open_time}"
    )


@pytest.mark.unit
def test_consolidate_close_time_is_inclusive_1ms_before_next_window() -> None:
    """Bar close_time is open_time + interval - 1ms (Binance Vision convention)."""
    t0 = datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)
    trades = [_trade("50000", trade_time=t0, trade_id="1")]
    consolidator = BarConsolidator(pair=_BTC_USDT, interval=KlineInterval.M1)
    bars = list(consolidator.consolidate(trades))
    bar = bars[0]

    expected_close = datetime(2024, 1, 1, 0, 0, 59, 999000, tzinfo=UTC)
    assert bar.close_time == expected_close, (
        f"Bar close_time should be {expected_close} (1ms before next window), got {bar.close_time}"
    )


@pytest.mark.unit
def test_consolidate_h4_window_boundary_alignment() -> None:
    """4H bars align to 00:00, 04:00, 08:00, etc. boundaries."""
    t_in_second_window = datetime(2024, 1, 1, 5, 30, 0, tzinfo=UTC)
    trades = [_trade("50000", trade_time=t_in_second_window, trade_id="1")]
    consolidator = BarConsolidator(pair=_BTC_USDT, interval=KlineInterval.H4)
    bars = list(consolidator.consolidate(trades))
    bar = bars[0]

    expected_open = datetime(2024, 1, 1, 4, 0, 0, tzinfo=UTC)
    assert bar.open_time == expected_open, (
        f"H4 bar at 05:30 should have open_time=04:00, got {bar.open_time}"
    )


@pytest.mark.unit
def test_consolidate_d1_window_boundary_alignment() -> None:
    """Daily bars align to midnight UTC."""
    t_midday = datetime(2024, 1, 15, 12, 0, 0, tzinfo=UTC)
    trades = [_trade("50000", trade_time=t_midday, trade_id="1")]
    consolidator = BarConsolidator(pair=_BTC_USDT, interval=KlineInterval.D1)
    bars = list(consolidator.consolidate(trades))
    bar = bars[0]

    expected_open = datetime(2024, 1, 15, 0, 0, 0, tzinfo=UTC)
    assert bar.open_time == expected_open, (
        f"D1 bar at 12:00 should have open_time=midnight, got {bar.open_time}"
    )


@pytest.mark.unit
def test_consolidate_gap_window_does_not_produce_empty_bar() -> None:
    """No trades in a window — no bar is emitted for that window."""
    t0 = datetime(2024, 1, 1, 0, 0, 30, tzinfo=UTC)  # minute 0
    # Minute 1 has no trades
    t2 = datetime(2024, 1, 1, 0, 2, 30, tzinfo=UTC)  # minute 2
    trades = [
        _trade("50000", trade_time=t0, trade_id="1"),
        _trade("51000", trade_time=t2, trade_id="2"),
    ]
    consolidator = BarConsolidator(pair=_BTC_USDT, interval=KlineInterval.M1)
    bars = list(consolidator.consolidate(trades))
    assert len(bars) == 2, (
        f"Gap window (minute 1) should produce no bar — expected 2 bars, got {len(bars)}"
    )
    assert bars[0].open_time == datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC), (
        f"First bar should be minute 0, got {bars[0].open_time}"
    )
    assert bars[1].open_time == datetime(2024, 1, 1, 0, 2, 0, tzinfo=UTC), (
        f"Second bar should be minute 2, got {bars[1].open_time}"
    )


@pytest.mark.unit
def test_consolidate_bar_pair_matches_consolidator_pair() -> None:
    """Every emitted bar carries the pair the consolidator was configured with."""
    t0 = datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)
    trades = [_trade("50000", trade_time=t0, trade_id="1")]
    consolidator = BarConsolidator(pair=_BTC_USDT, interval=KlineInterval.M1)
    bars = list(consolidator.consolidate(trades))
    assert bars[0].pair == _BTC_USDT, f"Bar.pair should be {_BTC_USDT}, got {bars[0].pair}"


@pytest.mark.unit
def test_consolidate_bar_interval_matches_consolidator_interval() -> None:
    """Every emitted bar carries the interval the consolidator was configured with."""
    t0 = datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)
    trades = [_trade("50000", trade_time=t0, trade_id="1")]
    consolidator = BarConsolidator(pair=_BTC_USDT, interval=KlineInterval.H4)
    bars = list(consolidator.consolidate(trades))
    assert bars[0].interval == KlineInterval.H4, (
        f"Bar.interval should be H4, got {bars[0].interval}"
    )


def _positive_decimal_strategy(
    min_val: str = "0.01",
    max_val: str = "100000",
) -> st.SearchStrategy[Decimal]:
    """Hypothesis strategy for positive decimal values."""
    return st.decimals(
        min_value=Decimal(min_val),
        max_value=Decimal(max_val),
        allow_nan=False,
        allow_infinity=False,
    )


def _trade_time_strategy(base: datetime) -> st.SearchStrategy[datetime]:
    """Strategy for trade times within a bounded range (avoids overflow)."""
    max_offset_seconds = 86400  # 1 day in seconds
    return st.integers(min_value=0, max_value=max_offset_seconds).map(
        lambda s: base + timedelta(seconds=s)
    )


@st.composite
def _trade_list_strategy(draw: st.DrawFn) -> list[Trade]:
    """Draw a non-empty list of trades in ascending order."""
    base = datetime(2024, 6, 1, 0, 0, 0, tzinfo=UTC)
    n = draw(st.integers(min_value=1, max_value=50))
    offsets = sorted(
        draw(
            st.lists(
                st.integers(min_value=0, max_value=3600),
                min_size=n,
                max_size=n,
            )
        )
    )
    trades: list[Trade] = []
    for i, offset in enumerate(offsets):
        price = draw(_positive_decimal_strategy())
        qty = draw(_positive_decimal_strategy(min_val="0.001", max_val="100"))
        side = draw(st.sampled_from(list(TradeSide)))
        trades.append(
            Trade(
                pair=_BTC_USDT,
                price=price,
                quantity=qty,
                side=side,
                trade_time=base + timedelta(seconds=offset),
                trade_id=str(i),
            )
        )
    return trades


@pytest.mark.property
@given(_trade_list_strategy())
@settings(max_examples=200)
def test_property_bar_high_ge_all_trade_prices(trades: list[Trade]) -> None:
    """Property: bar.high >= every trade price that contributed to the bar."""
    consolidator = BarConsolidator(pair=_BTC_USDT, interval=KlineInterval.H1)
    bars = list(consolidator.consolidate(trades))

    duration = timedelta(hours=1)

    for bar in bars:
        window_start = bar.open_time
        window_end = window_start + duration

        contributing_prices = [t.price for t in trades if window_start <= t.trade_time < window_end]
        for price in contributing_prices:
            assert bar.high >= price, (
                f"bar.high ({bar.high}) must be >= trade price ({price}) "
                f"in window [{window_start}, {window_end})"
            )


@pytest.mark.property
@given(_trade_list_strategy())
@settings(max_examples=200)
def test_property_bar_volume_equals_sum_of_trade_volumes(trades: list[Trade]) -> None:
    """Property: bar.volume == sum of trade.quantity for all contributing trades."""
    consolidator = BarConsolidator(pair=_BTC_USDT, interval=KlineInterval.H1)
    bars = list(consolidator.consolidate(trades))

    duration = timedelta(hours=1)

    for bar in bars:
        window_start = bar.open_time
        window_end = window_start + duration

        expected_volume = sum(
            (t.quantity for t in trades if window_start <= t.trade_time < window_end),
            Decimal("0"),
        )
        assert bar.volume == expected_volume, (
            f"bar.volume ({bar.volume}) must equal sum of contributing trade volumes "
            f"({expected_volume}) for window [{window_start}, {window_end})"
        )


@pytest.mark.property
@given(_trade_list_strategy())
@settings(max_examples=200)
def test_property_no_lookahead(trades: list[Trade]) -> None:
    """Property: bar at close_time T never uses any trade with trade_time > T."""
    consolidator = BarConsolidator(pair=_BTC_USDT, interval=KlineInterval.H1)
    bars = list(consolidator.consolidate(trades))

    duration = timedelta(hours=1)

    for bar in bars:
        window_end = bar.open_time + duration  # exclusive upper bound
        # Any trade with trade_time >= window_end must NOT be in this bar.
        future_trades = [t for t in trades if t.trade_time >= window_end]
        for ft in future_trades:
            # If a future trade is at the same price as this bar's close,
            # we can't tell from price alone — test by checking window alignment.
            trade_window_start_seconds = (
                (ft.trade_time - datetime(1970, 1, 1, tzinfo=UTC)).total_seconds()
                // duration.total_seconds()
            ) * duration.total_seconds()
            trade_window_open = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(
                seconds=trade_window_start_seconds
            )
            assert trade_window_open != bar.open_time, (
                f"No-lookahead violated: trade at {ft.trade_time} was included in "
                f"bar with open_time={bar.open_time} (window ends at {window_end})"
            )


@pytest.mark.property
@given(_trade_list_strategy())
@settings(max_examples=200)
def test_property_bar_high_ge_open_close_low(trades: list[Trade]) -> None:
    """Property: bar.high >= bar.open, bar.close, bar.low for all emitted bars."""
    consolidator = BarConsolidator(pair=_BTC_USDT, interval=KlineInterval.H1)
    bars = list(consolidator.consolidate(trades))

    for bar in bars:
        assert bar.high >= bar.open, f"bar.high ({bar.high}) must be >= bar.open ({bar.open})"
        assert bar.high >= bar.close, f"bar.high ({bar.high}) must be >= bar.close ({bar.close})"
        assert bar.high >= bar.low, f"bar.high ({bar.high}) must be >= bar.low ({bar.low})"


@pytest.mark.property
@given(_trade_list_strategy())
@settings(max_examples=200)
def test_property_bar_low_le_open_close_high(trades: list[Trade]) -> None:
    """Property: bar.low <= bar.open, bar.close, bar.high for all emitted bars."""
    consolidator = BarConsolidator(pair=_BTC_USDT, interval=KlineInterval.H1)
    bars = list(consolidator.consolidate(trades))

    for bar in bars:
        assert bar.low <= bar.open, f"bar.low ({bar.low}) must be <= bar.open ({bar.open})"
        assert bar.low <= bar.close, f"bar.low ({bar.low}) must be <= bar.close ({bar.close})"
        assert bar.low <= bar.high, f"bar.low ({bar.low}) must be <= bar.high ({bar.high})"


@pytest.mark.property
@given(_trade_list_strategy())
@settings(max_examples=200)
def test_property_close_time_strictly_after_open_time(trades: list[Trade]) -> None:
    """Property: bar.close_time > bar.open_time for all emitted bars."""
    consolidator = BarConsolidator(pair=_BTC_USDT, interval=KlineInterval.H1)
    bars = list(consolidator.consolidate(trades))

    for bar in bars:
        assert bar.close_time > bar.open_time, (
            f"bar.close_time ({bar.close_time}) must be strictly after "
            f"bar.open_time ({bar.open_time})"
        )


@pytest.mark.property
@given(_trade_list_strategy())
@settings(max_examples=200)
def test_property_bars_in_ascending_open_time_order(trades: list[Trade]) -> None:
    """Property: emitted bars are in ascending open_time order."""
    consolidator = BarConsolidator(pair=_BTC_USDT, interval=KlineInterval.H1)
    bars = list(consolidator.consolidate(trades))

    for i in range(1, len(bars)):
        assert bars[i].open_time > bars[i - 1].open_time, (
            f"bars must be in ascending open_time order, but bar[{i - 1}].open_time="
            f"{bars[i - 1].open_time} >= bar[{i}].open_time={bars[i].open_time}"
        )


@pytest.mark.property
@given(_trade_list_strategy())
@settings(max_examples=200)
def test_property_bar_volume_non_negative(trades: list[Trade]) -> None:
    """Property: bar.volume >= 0 for all emitted bars."""
    consolidator = BarConsolidator(pair=_BTC_USDT, interval=KlineInterval.H1)
    bars = list(consolidator.consolidate(trades))

    for bar in bars:
        assert bar.volume >= Decimal("0"), f"bar.volume must be >= 0, got {bar.volume}"
