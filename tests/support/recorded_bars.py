"""Recorded Binance bars and a replaying feed for the serve end-to-end tests.

The fixture ``tests/fixtures/e2e/serve-cycle-bars.csv.gz`` holds real spot
klines for the three shadow pairs (see the README next to it). Bars closing
before :data:`LIVE_FROM` are the warm-up history a first boot sees; the rest,
up to :data:`LIVE_UNTIL`, are delivered live.

:class:`ReplayFeed` delivers the live bars and moves a :class:`FakeClock` to
each bar's delivery time, so the serve loop sees the same "now" it would have
seen in production and notifies every fresh signal.
"""

from __future__ import annotations

import asyncio
import csv
import gzip
from collections.abc import AsyncIterator, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from functools import cache

from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.bar_schedule import delivery_key
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair
from tests.support.paths import REPO_ROOT
from tests.support.serve_loop import FakeClock, FakeFeed

FIXTURE = REPO_ROOT / "tests" / "fixtures" / "e2e" / "serve-cycle-bars.csv.gz"
LIVE_FROM = datetime(2025, 1, 1, tzinfo=UTC)
"""First boot happens here: earlier bars are history, later ones arrive live."""

LIVE_UNTIL = datetime(2025, 1, 21, tzinfo=UTC)
"""The recording ends at this close."""

SETTLE = timedelta(seconds=5)
"""Matches ``settle_delay_seconds`` in ``config/market_data.yaml``."""

_ONE_MS = timedelta(milliseconds=1)


def _utc(ms: str) -> datetime:
    """Return the UTC instant of a millisecond epoch string."""
    return datetime.fromtimestamp(int(ms) / 1000, tz=UTC)


@cache
def recorded_bars() -> tuple[Bar, ...]:
    """Return every recorded bar in delivery order."""
    with gzip.open(FIXTURE, "rt", newline="") as handle:
        rows = list(csv.DictReader(handle))
    bars = [
        Bar(
            pair=Pair(base=Currency[row["symbol"].removesuffix("USDT")], quote=Currency.USDT),
            interval=KlineInterval(row["interval"]),
            open_time=_utc(row["open_time_ms"]),
            open=Decimal(row["open"]),
            high=Decimal(row["high"]),
            low=Decimal(row["low"]),
            close=Decimal(row["close"]),
            volume=Decimal(row["volume"]),
            close_time=_utc(row["close_time_ms"]),
        )
        for row in rows
    ]
    return tuple(sorted(bars, key=delivery_key))


def delivered_at(bar: Bar) -> datetime:
    """Return when the live feed hands ``bar`` over: its close plus the settle delay."""
    return bar.close_time + _ONE_MS + SETTLE


def closed_by(at: datetime) -> list[Bar]:
    """Return the recorded bars the feed had delivered by ``at``."""
    return [b for b in recorded_bars() if delivered_at(b) <= at]


def delivered_after(at: datetime) -> list[Bar]:
    """Return the recorded bars the feed delivers after ``at``."""
    return [b for b in recorded_bars() if delivered_at(b) > at]


def bar_at(pair: Pair, interval: KlineInterval, open_time: datetime) -> Bar:
    """Return the one recorded bar of a stream that opens at ``open_time``."""
    return next(
        b
        for b in recorded_bars()
        if (b.pair, b.interval, b.open_time) == (pair, interval, open_time)
    )


class ReplayFeed(FakeFeed):
    """Feed that serves recorded history and replays live bars on a moving clock.

    ``warm_up`` returns the newest ``count`` bars of ``warm`` per stream, as the
    REST warm-up does. ``stream_closed_bars`` sets ``clock`` to each live bar's
    delivery time before yielding it, then idles like :class:`FakeFeed`.
    """

    def __init__(self, warm: Sequence[Bar], live_bars: Sequence[Bar], clock: FakeClock) -> None:
        """Script the feed and bind the clock it advances."""
        super().__init__(warm=warm, live_bars=live_bars)
        self.clock = clock

    async def stream_closed_bars(
        self,
        pairs: Sequence[Pair],
        intervals: Sequence[KlineInterval],
        *,
        resume_after: Mapping[tuple[Pair, KlineInterval], datetime] | None = None,
    ) -> AsyncIterator[Bar]:
        """Yield every live bar at its delivery time, then idle."""
        for bar in self.live_bars:
            self.clock.current = delivered_at(bar)
            yield bar
        self.drained.set()
        await asyncio.Event().wait()
