"""Readings of the risk inputs shadow records but never acts on (#1030).

Under the #514 ruling (the shadow set) serve runs what the backtest that gated
the strategy runs. RO-02 (spread spike) and the §10 live-phase recovery kill
stay off in shadow, but their inputs are on record: the spread samples (#835)
and the portfolio marks. These functions turn those records into what each
rule would have read, so ``hermes risk-inputs`` can show whether it would have
fired. Nothing here feeds a decision; the import-linter contract
``risk-inputs-are-record-only`` keeps this package out of every decision
module.

Reference: pre-registration v1 §5.5 (RO-02), §10 (recovery kill); issues #514,
#835, #1030.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Final

from hermes.domain.market_data.spread_sample import SpreadSample
from hermes.domain.portfolio.mark import PortfolioMark

__all__ = [
    "RECOVERY_DAYS",
    "RECOVERY_DRAWDOWN",
    "SPREAD_SPIKE_MULTIPLE",
    "RecoveryReading",
    "SpreadReading",
    "recovery_reading",
    "spread_reading",
]

SPREAD_SPIKE_MULTIPLE: Final = Decimal("10")
"""RO-02 fires at a spread of at least this many times its 1-hour median (§5.5)."""

RECOVERY_DAYS: Final = 90
"""§10: the recovery kill fires after more than this many days without a new high."""

RECOVERY_DRAWDOWN: Final = Decimal("0.08")
"""§10: ...counted from a drawdown of at least this share of the peak."""

_ONE_DAY: Final = timedelta(days=1)


@dataclass(frozen=True, slots=True)
class SpreadReading:
    """What RO-02 would read from one recorded spread sample.

    Attributes:
        sample: The recorded sample.
        multiple: The spread as a multiple of its 1-hour median; ``None`` when
            the median is zero, so no multiple is defined.
        would_fire: ``True`` when the spread is at least ten times the median.
    """

    sample: SpreadSample
    multiple: Decimal | None
    would_fire: bool


@dataclass(frozen=True, slots=True)
class RecoveryReading:
    """What the §10 recovery kill would read from the portfolio marks.

    Attributes:
        high_at: Close time of the bar that last set the equity high, or of
            the first mark when no mark reached the seed's peak.
        days_since_high: Whole days from ``high_at`` to ``now``.
        deepest_drawdown: Largest share of the peak lost since ``high_at``.
        would_fire: ``True`` after more than 90 days without a new high that
            include a drawdown of at least 8%.
    """

    high_at: datetime
    days_since_high: int
    deepest_drawdown: Decimal
    would_fire: bool


def spread_reading(sample: SpreadSample) -> SpreadReading:
    """Return what RO-02 would read from ``sample``.

    Args:
        sample: A recorded spread sample.

    Returns:
        The :class:`SpreadReading`.
    """
    median = sample.median_spread_1h
    if median <= 0:
        return SpreadReading(sample=sample, multiple=None, would_fire=False)
    multiple = sample.spread / median
    return SpreadReading(
        sample=sample, multiple=multiple, would_fire=multiple >= SPREAD_SPIKE_MULTIPLE
    )


def recovery_reading(marks: Sequence[PortfolioMark], *, now: datetime) -> RecoveryReading | None:
    """Return what the recovery kill would read from ``marks``.

    Args:
        marks: The strategy's portfolio marks, oldest first.
        now: The time the days are counted to, timezone-aware.

    Returns:
        The :class:`RecoveryReading`, or ``None`` when there is no mark.
    """
    if not marks:
        return None
    high_index = 0
    for index, mark in enumerate(marks):
        if mark.snapshot.equity >= mark.snapshot.peak_equity:
            high_index = index
    since = marks[high_index:]
    deepest = max(_drawdown(mark) for mark in since)
    high_at = marks[high_index].bar_close_time
    days = (now - high_at) // _ONE_DAY
    return RecoveryReading(
        high_at=high_at,
        days_since_high=days,
        deepest_drawdown=deepest,
        would_fire=days > RECOVERY_DAYS and deepest >= RECOVERY_DRAWDOWN,
    )


def _drawdown(mark: PortfolioMark) -> Decimal:
    """Return the share of the peak ``mark``'s equity is below it."""
    peak = mark.snapshot.peak_equity
    if peak <= 0:
        return Decimal(0)
    return max(Decimal(0), (peak - mark.snapshot.equity) / peak)
