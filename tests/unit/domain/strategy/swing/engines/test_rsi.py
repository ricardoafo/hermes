"""Tests for RsiEngine, Wilder's RSI over a bounded window (#1066).

Reference: research batch swing-b1 §5 (C4), card swing-b1-pullback.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from hypothesis import given
from hypothesis import strategies as st

from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.strategy.swing.engines.rsi import RsiEngine
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair

_BTC_USDT = Pair(base=Currency.BTC, quote=Currency.USDT)
_BASE = datetime(2024, 1, 1, tzinfo=UTC)


def _bars(closes: list[int]) -> list[Bar]:
    bars = []
    for index, value in enumerate(closes):
        close = Decimal(value)
        open_time = _BASE + timedelta(hours=4 * index)
        bars.append(
            Bar(
                pair=_BTC_USDT,
                interval=KlineInterval.H4,
                open_time=open_time,
                open=close,
                high=close + Decimal("1"),
                low=close - Decimal("1"),
                close=close,
                volume=Decimal("100"),
                close_time=open_time + timedelta(hours=4),
            )
        )
    return bars


def test_a_hand_computed_value() -> None:
    """Changes +1, -1, +2 at period 2: seed 0.5/0.5, then 1.25/0.25, so RS 5 and RSI 100 - 100/6."""
    assert RsiEngine.value(_bars([10, 11, 10, 12]), period=2) == Decimal(100) - Decimal(100) / 6


def test_only_gains_is_one_hundred() -> None:
    """A window with no losing close is RSI 100."""
    assert RsiEngine.value(_bars(list(range(100, 120))), period=14) == Decimal(100)


def test_only_losses_is_zero() -> None:
    """A window with no gaining close is RSI 0."""
    assert RsiEngine.value(_bars(list(range(120, 100, -1))), period=14) == Decimal(0)


def test_a_flat_window_is_fifty() -> None:
    """No gains and no losses: RSI 50, not a division by zero."""
    assert RsiEngine.value(_bars([100] * 20), period=14) == Decimal(50)


def test_too_few_bars_is_none() -> None:
    """RSI(period) needs period changes, so period + 1 bars."""
    assert (
        RsiEngine.value(_bars(list(range(2, 16))), period=14),
        RsiEngine.value(_bars(list(range(2, 17))), period=14) is not None,
    ) == (None, True)


_CLOSES = st.lists(st.integers(min_value=2, max_value=10_000), min_size=15, max_size=80)


@given(closes=_CLOSES)
def test_rsi_is_between_zero_and_one_hundred(closes: list[int]) -> None:
    """Property: RSI never leaves [0, 100]."""
    value = RsiEngine.value(_bars(closes), period=14)

    assert value is not None
    assert Decimal(0) <= value <= Decimal(100)


@given(
    older=st.lists(st.integers(min_value=2, max_value=10_000), min_size=1, max_size=40),
    recent=st.lists(st.integers(min_value=2, max_value=10_000), min_size=29, max_size=29),
)
def test_rsi_depends_only_on_the_last_two_periods(older: list[int], recent: list[int]) -> None:
    """Property: history older than the last 2 * period + 1 bars never changes the value."""
    assert RsiEngine.value(_bars(older + recent), period=14) == RsiEngine.value(
        _bars(recent), period=14
    )
