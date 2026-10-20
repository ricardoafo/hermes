"""BarProcessed domain event — the serve loop handled one closed bar.

The event is the loop's cursor: its ``occurred_at`` is the bar's close time,
so the newest row per ``(pair, interval)`` says which bar was handled last.
Most bars emit no signal, so signals alone cannot give that position; this
row is written for every handled bar, after any signal row and notification.

The event carries no wall clock (``occurred_at`` is the bar's close time),
so it replays deterministically.

Reference: 309-serve-bar-loop change.md D-6;
    code-conventions.md §10 (event schema versioning).
"""

from dataclasses import dataclass
from typing import ClassVar

from hermes.domain.events.base import DomainEvent
from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.value_objects.pair import Pair

__all__ = ["BarProcessed"]


@dataclass(frozen=True, slots=True)
class BarProcessed(DomainEvent):
    """One closed bar was handled by the serve loop.

    Build it with :meth:`from_bar` so the time source lives in one place.

    Fields:
        pair: Pair of the handled bar.
        interval: Candle interval of the handled bar.

    Raises:
        ValueError: On a naive ``occurred_at`` or a negative ``event_seq``.
    """

    __event_schema_version__: ClassVar[int] = 1

    pair: Pair
    interval: KlineInterval

    def __post_init__(self) -> None:
        """Compute the payload hash, then require a UTC-aware ``occurred_at``.

        Raises:
            ValueError: If ``event_seq`` is negative or ``occurred_at`` is naive.
        """
        DomainEvent.__post_init__(self)
        if self.occurred_at.tzinfo is None:
            raise ValueError("BarProcessed.occurred_at must be UTC-aware")

    @classmethod
    def from_bar(cls, bar: Bar, *, correlation_id: str) -> BarProcessed:
        """Record that ``bar`` was handled.

        Args:
            bar: The closed bar the loop just handled.
            correlation_id: ULID of the bar's processing cycle.

        Returns:
            An event whose ``occurred_at`` is ``bar.close_time``.
        """
        return cls(
            occurred_at=bar.close_time,
            correlation_id=correlation_id,
            pair=bar.pair,
            interval=bar.interval,
        )
