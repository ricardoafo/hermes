"""Compare closing prices of the same bars on the primary and backup venue.

Used by ``hermes failover-check`` (#31), the read-only first step of the
Binance to Kraken failover drill in ``docs/runbook/failover-to-kraken.md``.
Two venues never print identical candles, so the check is a tolerance in
basis points on each close, not equality. A bar that only one venue has
makes the pair incomplete: after a failover the strategy would see a gap.

Reference: GitHub issue #31; ADR-004 Decision 3 and 6.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Final

from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.value_objects.pair import Pair

__all__ = ["BarDivergence", "PairParity", "ParityVerdict", "compare_closes", "divergence_bps"]

_BPS_PER_UNIT: Final = Decimal(10_000)


class ParityVerdict(StrEnum):
    """Whether the backup venue could stand in for the primary on one stream."""

    OK = "ok"
    """Every bar is on both venues and every close is within the tolerance."""

    DIVERGED = "diverged"
    """At least one close differs by more than the tolerance."""

    INCOMPLETE = "incomplete"
    """A bar is missing on one venue, or there was nothing to compare."""


@dataclass(frozen=True, slots=True)
class BarDivergence:
    """The two closes of one bar and how far apart they are.

    Args:
        open_time: Open time of the bar.
        primary_close: Close on the primary venue.
        backup_close: Close on the backup venue.
        bps: Absolute difference relative to the primary close, in basis points.
    """

    open_time: datetime
    primary_close: Decimal
    backup_close: Decimal
    bps: Decimal


@dataclass(frozen=True, slots=True)
class PairParity:
    """Result for one ``(pair, interval)`` stream.

    Args:
        pair: Pair of the stream.
        interval: Interval of the stream.
        verdict: See :class:`ParityVerdict`.
        compared: Bars present on both venues.
        worst: The bar with the largest divergence, or ``None`` when no bar
            was on both venues.
        missing_on_backup: Open times the primary has and the backup lacks.
        missing_on_primary: Open times the backup has and the primary lacks.
    """

    pair: Pair
    interval: KlineInterval
    verdict: ParityVerdict
    compared: int
    worst: BarDivergence | None
    missing_on_backup: tuple[datetime, ...]
    missing_on_primary: tuple[datetime, ...]


def divergence_bps(primary: Decimal, backup: Decimal) -> Decimal:
    """Return ``|backup - primary| / primary`` in basis points.

    Args:
        primary: Reference price; must be positive.
        backup: Price to compare.

    Raises:
        ValueError: ``primary`` is zero or negative.
    """
    if primary <= 0:
        raise ValueError(f"primary close must be positive, got {primary}")
    return abs(backup - primary) / primary * _BPS_PER_UNIT


def compare_closes(
    *,
    pair: Pair,
    interval: KlineInterval,
    primary: Mapping[datetime, Decimal],
    backup: Mapping[datetime, Decimal],
    tolerance_bps: Decimal,
) -> PairParity:
    """Compare the closes of the bars two venues report for one stream.

    Args:
        pair: Pair of the stream.
        interval: Interval of the stream.
        primary: Close per bar open time on the primary venue.
        backup: Close per bar open time on the backup venue.
        tolerance_bps: Largest divergence that still counts as the same bar.

    Returns:
        The stream's :class:`PairParity`. A missing bar wins over a divergent
        close, because a gap is the harder failure to recover from.
    """
    shared = sorted(primary.keys() & backup.keys())
    divergences = [
        BarDivergence(
            open_time=open_time,
            primary_close=primary[open_time],
            backup_close=backup[open_time],
            bps=divergence_bps(primary[open_time], backup[open_time]),
        )
        for open_time in shared
    ]
    worst = max(divergences, key=lambda d: d.bps, default=None)
    missing_on_backup = tuple(sorted(primary.keys() - backup.keys()))
    missing_on_primary = tuple(sorted(backup.keys() - primary.keys()))
    if not shared or missing_on_backup or missing_on_primary:
        verdict = ParityVerdict.INCOMPLETE
    elif worst is not None and worst.bps > tolerance_bps:
        verdict = ParityVerdict.DIVERGED
    else:
        verdict = ParityVerdict.OK
    return PairParity(
        pair=pair,
        interval=interval,
        verdict=verdict,
        compared=len(shared),
        worst=worst,
        missing_on_backup=missing_on_backup,
        missing_on_primary=missing_on_primary,
    )
