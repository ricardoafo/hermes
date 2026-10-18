"""BarMissed domain event: a bar closed while serve was down (issue #1078).

Serve handles a bar as missed when it reaches the bar more than the live
window after the bar closed, the same line past which no live order book can
stand in for it. The strategy still reads a missed bar, so its decisions match
the backtest's. What changes is execution: an entry on a missed bar is not
filled, and an exit fills at the price when serve is back (ADR-024 §7).

One row is written per missed bar, before the bar's ``BarProcessed`` row. The
rows are what ADR-021's missed-bar budget counts, per stream of a strategy's
ledger.

``occurred_at`` is the bar's close time; ``handled_at`` is when serve reached
it, the only wall-clock value, kept so the downtime can be read back.

Reference: code-conventions.md §10 (event schema versioning).
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import ClassVar

from hermes.domain.events.base import DomainEvent
from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.value_objects.pair import Pair

__all__ = ["BarMissed", "reached_late"]


@dataclass(frozen=True, slots=True)
class BarMissed(DomainEvent):
    """Serve reached one closed bar only after its live window had passed.

    Build it with :meth:`from_bar` so the time source lives in one place.

    Fields:
        pair: Pair of the missed bar.
        interval: Candle interval of the missed bar.
        handled_at: When serve reached the bar, UTC-aware.

    Raises:
        ValueError: On a naive time, a negative ``event_seq``, or a
            ``handled_at`` before the bar closed.
    """

    __event_schema_version__: ClassVar[int] = 1

    pair: Pair
    interval: KlineInterval
    handled_at: datetime

    def __post_init__(self) -> None:
        """Compute the payload hash, then check both times.

        Raises:
            ValueError: On a naive time or ``handled_at`` before ``occurred_at``.
        """
        DomainEvent.__post_init__(self)
        if self.occurred_at.tzinfo is None or self.handled_at.tzinfo is None:
            raise ValueError("BarMissed times must be UTC-aware")
        if self.handled_at < self.occurred_at:
            raise ValueError("BarMissed.handled_at cannot be before the bar closed")

    @classmethod
    def from_bar(cls, bar: Bar, *, handled_at: datetime, correlation_id: str) -> BarMissed:
        """Record that ``bar`` was reached late.

        Args:
            bar: The closed bar serve reached late.
            handled_at: When serve reached it, UTC-aware.
            correlation_id: ULID of the bar's processing cycle.

        Returns:
            An event whose ``occurred_at`` is ``bar.close_time``.
        """
        return cls(
            occurred_at=bar.close_time,
            correlation_id=correlation_id,
            pair=bar.pair,
            interval=bar.interval,
            handled_at=handled_at,
        )


def reached_late(bar: Bar, *, now: datetime, window: timedelta) -> bool:
    """Return whether serve reaches ``bar`` only after its live window has passed.

    Args:
        bar: A closed bar.
        now: When serve reaches it, UTC-aware.
        window: How long after the close a live order book may still stand in.

    Returns:
        ``True`` when more than ``window`` has passed since the bar closed.
    """
    return now - bar.close_time > window
