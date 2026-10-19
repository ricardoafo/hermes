"""Hourly bars for the live bar context tests (issue #1289)."""

from datetime import datetime, timedelta
from decimal import Decimal

from hermes.domain.market_data.bar_schedule import previous_boundary
from hermes.domain.market_data.kline import Kline, KlineInterval
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.price import Price
from hermes.domain.value_objects.quantity import Quantity

__all__ = ["hourly_bars"]


def hourly_bars(coin: str, now: datetime) -> list[Kline]:
    """Return the 25 hourly bars a source gives at ``now``: the 24 h window and the current hour.

    Bar ``n`` (``n`` hours after the window start, the hour that opened 24 h
    before the current hour) opens at ``100 + n``, reaches 5 above its open,
    dips 5 below it, closes 1 above it and traded 2 units of ``coin``.

    Args:
        coin: Base asset of the bars.
        now: The moment the bars are read at (UTC-aware).

    Returns:
        The bars, oldest first.

    """
    pair = Pair(Currency(coin), Currency("USDT"))
    start = previous_boundary(now, KlineInterval.H1) - timedelta(hours=24)
    bars = []
    for index in range(25):
        opened = Decimal(100 + index)
        opens_at = start + timedelta(hours=index)
        bars.append(
            Kline(
                pair=pair,
                interval=KlineInterval.H1,
                open_time=opens_at,
                open=Price(amount=opened, pair=pair),
                high=Price(amount=opened + 5, pair=pair),
                low=Price(amount=opened - 5, pair=pair),
                close=Price(amount=opened + 1, pair=pair),
                volume=Quantity(amount=Decimal(2), asset=pair.base),
                close_time=opens_at + timedelta(hours=1),
            )
        )
    return bars
