"""Shared fixtures and builder helpers for swing strategy unit tests.

Provides ``make_bar`` and ``make_bars`` so every test file can construct
minimal, precise bar sequences without boilerplate.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair

_BTC_USDT = Pair(base=Currency.BTC, quote=Currency.USDT)
_H4 = KlineInterval.H4
_D1 = KlineInterval.D1

_BASE_TIME = datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)


def make_bar(
    *,
    close: Decimal,
    high: Decimal | None = None,
    low: Decimal | None = None,
    open_: Decimal | None = None,
    volume: Decimal = Decimal("100"),
    index: int = 0,
    interval: KlineInterval = _H4,
    pair: Pair = _BTC_USDT,
    interval_hours: int = 4,
) -> Bar:
    """Build a single closed Bar for testing.

    Args:
        close: Close price.
        high: High price (defaults to close).
        low: Low price (defaults to close * 0.99).
        open_: Open price (defaults to close).
        volume: Volume (default 100).
        index: Bar index used to compute open_time (step = interval_hours h).
        interval: KlineInterval (default H4).
        pair: Trading pair (default BTC/USDT).
        interval_hours: Duration in hours (default 4 for H4).

    Returns:
        A valid ``Bar`` instance.

    """
    open_time = _BASE_TIME + timedelta(hours=interval_hours * index)
    close_time = open_time + timedelta(hours=interval_hours)
    _close = close
    _high = high if high is not None else _close
    _low = low if low is not None else (_close * Decimal("0.99"))
    _open = open_ if open_ is not None else _close

    # Ensure OHLC validity
    _high = max(_high, _close, _open)
    _low = min(_low, _close, _open)
    if _low <= Decimal("0"):
        _low = Decimal("0.01")

    return Bar(
        pair=pair,
        interval=interval,
        open_time=open_time,
        open=_open,
        high=_high,
        low=_low,
        close=_close,
        volume=volume,
        close_time=close_time,
    )


def make_bars(
    closes: Sequence[Decimal | float | int],
    *,
    highs: Sequence[Decimal | float | int] | None = None,
    lows: Sequence[Decimal | float | int] | None = None,
    interval: KlineInterval = _H4,
    interval_hours: int = 4,
    pair: Pair = _BTC_USDT,
) -> list[Bar]:
    """Build a list of Bars from a list of close prices.

    Args:
        closes: Close prices in ascending time order.
        highs: Optional high prices (defaults to close for each bar).
        lows: Optional low prices (defaults to close * 0.99 for each bar).
        interval: KlineInterval for all bars.
        interval_hours: Duration in hours for each bar.
        pair: Trading pair for all bars.

    Returns:
        List of ``Bar`` instances in ascending ``open_time`` order.

    """
    bars = []
    for i, c in enumerate(closes):
        h = Decimal(str(highs[i])) if highs else None
        lo = Decimal(str(lows[i])) if lows else None
        bars.append(
            make_bar(
                close=Decimal(str(c)),
                high=h,
                low=lo,
                index=i,
                interval=interval,
                interval_hours=interval_hours,
                pair=pair,
            )
        )
    return bars
