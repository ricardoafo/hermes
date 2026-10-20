"""ClosedBarFeedPort — contract for a live source of closed candles.

The shadow loop needs closed bars in a deterministic order plus a way to
prime its indicator buffers before the first live bar. This port carries
exactly that, and reports its own health. It sits beside
:class:`~hermes.ports.market_data.MarketDataPort` (which also covers trades
and order books) so a source that only knows candles does not have to stub the
other channels.

- :meth:`ClosedBarFeedPort.warm_up` returns history, apart from the live
  stream. Replaying history through a strategy would evaluate signals on
  bars that closed long ago.
- :meth:`ClosedBarFeedPort.stream_closed_bars` yields each closed bar once,
  in delivery order (see :func:`hermes.domain.market_data.bar_schedule.delivery_key`).

Reference: openspec/archive/307-live-bar-stream/change.md D-4, D-5, D-7.
"""

from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable

from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.value_objects.pair import Pair

__all__ = ["ClosedBarFeedPort", "SourceHealth", "SourceState"]


class SourceState(StrEnum):
    """Lifecycle state of a closed-bar source.

    ``FAILED`` is terminal (a permanent error such as a blocked region);
    ``STOPPED`` follows :meth:`ClosedBarFeedPort.aclose`.
    """

    STARTING = "starting"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    STALE = "stale"
    FAILED = "failed"
    STOPPED = "stopped"


@dataclass(frozen=True, slots=True)
class SourceHealth:
    """Point-in-time health of a closed-bar source.

    Attributes:
        state: Current :class:`SourceState`.
        overdue_seconds: How long the oldest expected bar has been past its
            close without being delivered; ``0.0`` when nothing is overdue.
        consecutive_failures: Failed poll cycles since the last good one.
        last_closed: ``close_time`` of the newest bar delivered, or ``None``
            before the first one.
        clock_skew_ms: Local clock minus exchange clock at the last
            comparison, or ``None`` before the first one.
        used_weight_1m: The exchange's ``x-mbx-used-weight-1m`` header from
            the last response, or ``None`` when absent.
    """

    state: SourceState
    overdue_seconds: float
    consecutive_failures: int
    last_closed: datetime | None
    clock_skew_ms: int | None
    used_weight_1m: int | None


@runtime_checkable
class ClosedBarFeedPort(Protocol):
    """Contract every closed-bar live source satisfies.

    Implementations MUST NOT yield an open (forming) candle, MUST yield each
    ``(pair, interval, open_time)`` at most once per stream, and raise
    :class:`hermes.domain.errors.InfraError` (or a subclass) on a failure they
    cannot absorb.
    """

    async def warm_up(self, pair: Pair, interval: KlineInterval, count: int) -> list[Bar]:
        """Return up to ``count`` of the most recent closed bars, oldest first.

        Args:
            pair: Trading pair.
            interval: Candle-duration bucket.
            count: Number of closed bars wanted.

        Returns:
            Closed bars in ascending ``open_time`` order. Fewer than
            ``count`` when the exchange holds fewer.

        Raises:
            hermes.domain.errors.InfraError: The source could not be read.
        """
        ...  # pragma: no cover

    def stream_closed_bars(
        self,
        pairs: Sequence[Pair],
        intervals: Sequence[KlineInterval],
        *,
        resume_after: Mapping[tuple[Pair, KlineInterval], datetime] | None = None,
    ) -> AsyncIterator[Bar]:
        """Yield closed bars for every ``pair`` x ``interval``, each once.

        Args:
            pairs: Trading pairs to follow.
            intervals: Candle-duration buckets to follow.
            resume_after: Optional per-stream marker. A stream resumes with
                the first bar whose ``open_time`` is later than its value, so
                the ``open_time`` or ``close_time`` of the last handled bar
                both work. Streams without an entry start after the bars
                already returned by :meth:`warm_up`, or, when none were,
                after the latest bar that was already closed at start.

        Yields:
            Closed bars in delivery order.

        Raises:
            hermes.domain.errors.InfraError: A permanent failure.
        """
        ...  # pragma: no cover

    def health(self) -> SourceHealth:
        """Return the current :class:`SourceHealth` snapshot."""
        ...  # pragma: no cover

    async def aclose(self) -> None:
        """Stop the source and release its resources."""
        ...  # pragma: no cover
