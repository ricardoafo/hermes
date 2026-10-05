"""Synthetic price paths for the fake market.

A :class:`MarketScript` is written per pair as a sequence of 4h closes. Daily
bars are aggregated from the six 4h bars of each UTC day, as Binance's 1d
candle covers the same six 4h candles, so the two intervals never disagree.

Every number is invented: the default start price is a round 100 and nothing is
taken from a recording (ADR-012 keeps live bars and the pre-registered test
window away from AI-written tests).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Self

from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.bar_schedule import close_time_of
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.value_objects.pair import Pair

H4 = timedelta(hours=4)
D1 = timedelta(days=1)
BARS_PER_DAY = 6

_SPREAD = Decimal("0.004")
"""Default high-low range of a scripted bar, as a fraction of its close."""

_VOLUME = Decimal("1000")
_TICK = Decimal("0.00000001")


@dataclass(frozen=True, slots=True)
class Step:
    """One scripted 4h bar of one pair, before it becomes a :class:`Bar`.

    Attributes:
        close: Close price.
        high: High, or ``None`` for close plus half the default range.
        low: Low, or ``None`` for close minus half the default range.
        volume: Base-asset volume.

    """

    close: Decimal
    high: Decimal | None = None
    low: Decimal | None = None
    volume: Decimal = _VOLUME


@dataclass
class MarketScript:
    """Builder for a multi-pair synthetic market starting at a UTC midnight.

    Attributes:
        start: Open time of the first 4h and 1d bar. Must be 00:00 UTC.
        prices: Start price of each pair.
        steps: Scripted 4h bars per pair, in order.

    """

    start: datetime
    prices: dict[Pair, Decimal]
    steps: dict[Pair, list[Step]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Reject a start that is not a UTC day boundary."""
        if self.start.tzinfo is None or self.start.utcoffset() != timedelta(0):
            raise ValueError(f"start must be UTC-aware, got {self.start!r}")
        if self.start != datetime.combine(self.start.date(), datetime.min.time(), tzinfo=UTC):
            raise ValueError(f"start must be 00:00 UTC, got {self.start.isoformat()}")
        for pair in self.prices:
            self.steps.setdefault(pair, [])

    def last_close(self, pair: Pair) -> Decimal:
        """Return the close of the pair's latest scripted bar, or its start price."""
        steps = self.steps[pair]
        return steps[-1].close if steps else self.prices[pair]

    def ramp(self, pair: Pair, *, days: float, to: Decimal) -> Self:
        """Move ``pair`` in a straight line to ``to`` over ``days`` of 4h bars.

        Args:
            pair: Pair to move.
            days: Length of the move; rounded to whole 4h bars, at least one.
            to: Close of the last bar of the move.

        Returns:
            The script, for chaining.

        """
        bars = max(1, round(days * BARS_PER_DAY))
        first = self.last_close(pair)
        for i in range(1, bars + 1):
            close = first + (to - first) * Decimal(i) / Decimal(bars)
            self.steps[pair].append(Step(close=close.quantize(_TICK)))
        return self

    def hold(self, pair: Pair, *, days: float) -> Self:
        """Keep ``pair`` flat at its last close for ``days``."""
        return self.ramp(pair, days=days, to=self.last_close(pair))

    def bar(
        self,
        pair: Pair,
        *,
        close: Decimal,
        high: Decimal | None = None,
        low: Decimal | None = None,
        volume: Decimal = _VOLUME,
    ) -> Self:
        """Add one 4h bar with explicit values (a breakout, a crash, a thin bar)."""
        self.steps[pair].append(Step(close=close, high=high, low=low, volume=volume))
        return self

    def build(self) -> Market:
        """Freeze the script into bars, padding shorter pairs flat to whole days.

        Returns:
            The market every pair shares one timeline in.

        """
        longest = max(len(s) for s in self.steps.values())
        days = -(-longest // BARS_PER_DAY)
        h4: dict[Pair, list[Bar]] = {}
        d1: dict[Pair, list[Bar]] = {}
        for pair, steps in self.steps.items():
            padded = list(steps)
            while len(padded) < days * BARS_PER_DAY:
                padded.append(Step(close=padded[-1].close if padded else self.prices[pair]))
            h4[pair] = list(_h4_bars(pair, self.start, self.prices[pair], padded))
            d1[pair] = [
                _day_bar(pair, h4[pair][d * BARS_PER_DAY : (d + 1) * BARS_PER_DAY])
                for d in range(days)
            ]
        return Market(start=self.start, h4=h4, d1=d1)


@dataclass(frozen=True)
class Market:
    """A built synthetic market.

    Attributes:
        start: Open time of the first bar.
        h4: 4h bars per pair, oldest first.
        d1: 1d bars per pair, oldest first.

    """

    start: datetime
    h4: dict[Pair, list[Bar]]
    d1: dict[Pair, list[Bar]]

    @property
    def end(self) -> datetime:
        """Return the instant the last scripted bar closes (exclusive close)."""
        last = max(bars[-1].close_time for bars in self.h4.values())
        return last + timedelta(milliseconds=1)

    def bars(self, pair: Pair, interval: KlineInterval) -> list[Bar]:
        """Return the bars of one stream; an unknown pair has none."""
        series = self.h4 if interval is KlineInterval.H4 else self.d1
        return series.get(pair, [])

    def day_close(self, pair: Pair, on: date) -> Decimal:
        """Return the close of ``pair`` on day ``on`` (its 1d bar)."""
        for bar in self.d1[pair]:
            if bar.open_time.date() == on:
                return bar.close
        raise KeyError(f"no 1d bar for {pair} on {on}")


def _h4_bars(pair: Pair, start: datetime, first_open: Decimal, steps: list[Step]) -> Iterable[Bar]:
    """Turn steps into contiguous 4h bars: each opens at the previous close."""
    open_price = first_open
    for i, step in enumerate(steps):
        open_time = start + H4 * i
        half = step.close * _SPREAD / 2
        high = step.high if step.high is not None else max(open_price, step.close) + half
        low = step.low if step.low is not None else min(open_price, step.close) - half
        yield Bar(
            pair=pair,
            interval=KlineInterval.H4,
            open_time=open_time,
            open=open_price,
            high=max(high, open_price, step.close).quantize(_TICK),
            low=min(low, open_price, step.close).quantize(_TICK),
            close=step.close,
            volume=step.volume,
            close_time=close_time_of(open_time, KlineInterval.H4),
        )
        open_price = step.close


def _day_bar(pair: Pair, six: list[Bar]) -> Bar:
    """Aggregate the six 4h bars of one UTC day into its 1d bar."""
    open_time = six[0].open_time
    return Bar(
        pair=pair,
        interval=KlineInterval.D1,
        open_time=open_time,
        open=six[0].open,
        high=max(b.high for b in six),
        low=min(b.low for b in six),
        close=six[-1].close,
        volume=sum((b.volume for b in six), Decimal(0)),
        close_time=close_time_of(open_time, KlineInterval.D1),
    )
