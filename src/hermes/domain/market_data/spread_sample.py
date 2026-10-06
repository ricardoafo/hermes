"""One sampled bid-ask spread and the 1-hour median it closes (RO-02 input, #835).

Risk rule RO-02 (spread spike) compares the current spread with its rolling
1-hour median. Serve reads a book only when it executes a signal, a few times
a week, which is far too sparse for a median, so a sampler reads one book per
pair every few minutes and records each spread here. The sample also carries
the median of every sample of its pair in the hour up to it, so the recorded
value is the one a rule would read.

Record only: no risk rule, strategy, sizing or kill reads these values yet.
Wiring RO-02 changes trading decisions and waits for the Code Owner's ruling
(issue #514).

Reference: pre-registration v1 §10 (SA-9 RO-02); GitHub issue #835.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Final

from hermes.domain.value_objects.pair import Pair

__all__ = ["SPREAD_MEDIAN_WINDOW", "SpreadSample", "median_spread"]

SPREAD_MEDIAN_WINDOW: Final[timedelta] = timedelta(hours=1)
"""Length of the rolling window RO-02's median covers."""

_TWO: Final[Decimal] = Decimal("2")


def median_spread(spreads: Sequence[Decimal]) -> Decimal | None:
    """Return the median of ``spreads``, or ``None`` when there is none.

    An even count takes the mean of the two middle values, so the result is
    exact in :class:`~decimal.Decimal` and independent of the input order.

    Args:
        spreads: Relative spreads (``(ask - bid) / mid``), in any order.

    Returns:
        The median, or ``None`` for an empty sequence.
    """
    if not spreads:
        return None
    ordered = sorted(spreads)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / _TWO


@dataclass(frozen=True, slots=True)
class SpreadSample:
    """The spread of one book a sampler read, with the 1-hour median it closes.

    Attributes:
        pair: Pair whose book was read.
        sampled_at: When the sampler asked for the book (the sampler's clock).
        book_time: When the book was received (the book's own stamp). Two
            samples of the same received book share it, so it is the key.
        spread: ``(best ask - best bid) / mid`` of the book.
        median_spread_1h: Median of this sample and every earlier sample of
            the pair in the :data:`SPREAD_MEDIAN_WINDOW` before ``book_time``.
        samples_1h: How many samples that median covers, this one included.
    """

    pair: Pair
    sampled_at: datetime
    book_time: datetime
    spread: Decimal
    median_spread_1h: Decimal
    samples_1h: int

    def __post_init__(self) -> None:
        """Reject a naive timestamp, a negative spread or an empty window.

        Raises:
            ValueError: When an invariant does not hold.
        """
        for name, value in (("sampled_at", self.sampled_at), ("book_time", self.book_time)):
            if value.tzinfo is None:
                raise ValueError(f"{name} must be timezone-aware")
        if self.spread < 0 or self.median_spread_1h < 0:
            raise ValueError("a spread cannot be negative")
        if self.samples_1h < 1:
            raise ValueError(f"samples_1h must be >= 1, got {self.samples_1h}")
