"""Daily check of stored live bars against the Binance public archive (#359).

For each traded pair and interval, compare the live bars stored for one UTC
day with the archived bars of that day, and raise one ``AnomalyDetected``
alert per mismatch naming the pair, timeframe and bar. Read-only: it never
writes the ledger or the bar store.

Reference: GitHub issues #359, #343 (risk R-6), #421.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Protocol

from hermes.domain.events.anomaly_detected import AnomalyDetected, AnomalySeverity
from hermes.domain.events.base import DomainEvent
from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.bar_comparison import BarMismatch, compare_bars
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.value_objects.pair import Pair
from hermes.ports.archive_bars import ArchiveBarsPort

__all__ = ["ALERT_SOURCE", "ArchiveCheckReport", "LiveBarReader", "run_archive_check"]

ALERT_SOURCE = "data.archive_check"


class LiveBarReader(Protocol):
    """Read side of the live bar store."""

    def live_bars(
        self, pair: Pair, interval: KlineInterval, start: datetime, end: datetime
    ) -> tuple[Bar, ...]:
        """Return stored bars with ``start <= open_time < end``."""
        ...


@dataclass(frozen=True, slots=True)
class ArchiveCheckReport:
    """Outcome of one day's check.

    Args:
        day: The UTC day checked.
        checked: Streams compared, as ``(pair, interval)``.
        unpublished: Streams whose archive file is not published yet.
        mismatches: Every disagreement found.
    """

    day: date
    checked: tuple[tuple[Pair, KlineInterval], ...] = ()
    unpublished: tuple[tuple[Pair, KlineInterval], ...] = ()
    mismatches: tuple[BarMismatch, ...] = field(default=())


def _alert(mismatch: BarMismatch, *, now: datetime, correlation_id: str) -> AnomalyDetected:
    detail = f": {mismatch.detail}" if mismatch.detail else ""
    reason = (
        f"Archive check {mismatch.kind.value} for {mismatch.pair} {mismatch.interval.value} "
        f"bar opening {mismatch.open_time.isoformat()}{detail}"
    )
    return AnomalyDetected(
        occurred_at=now,
        correlation_id=correlation_id,
        source=ALERT_SOURCE,
        severity=AnomalySeverity.WARNING,
        reason=reason,
    )


def run_archive_check(
    *,
    archive: ArchiveBarsPort,
    live: LiveBarReader,
    pairs: Sequence[Pair],
    intervals: Sequence[KlineInterval],
    day: date,
    notify: Callable[[DomainEvent], None],
    now: datetime,
    correlation_id: str,
    tolerance: Decimal = Decimal(0),
) -> ArchiveCheckReport:
    """Compare one UTC day of stored live bars with the archive and alert on mismatches.

    Args:
        archive: Source of archived bars.
        live: Read-only live bar store.
        pairs: Traded pairs.
        intervals: Traded intervals.
        day: UTC day to check.
        notify: Sends one alert event.
        now: Current UTC time, stamped on alerts.
        correlation_id: Shared by every alert of this run.
        tolerance: Largest OHLCV difference still counted as a match (exact by default).

    Returns:
        What was checked, what is not published yet, and every mismatch.
    """
    start = datetime.combine(day, time(0), tzinfo=UTC)
    end = start + timedelta(days=1)
    checked: list[tuple[Pair, KlineInterval]] = []
    unpublished: list[tuple[Pair, KlineInterval]] = []
    mismatches: list[BarMismatch] = []
    for pair in pairs:
        for interval in intervals:
            reference = archive.daily_bars(pair, interval, day)
            if reference is None:
                unpublished.append((pair, interval))
                continue
            found = compare_bars(
                live.live_bars(pair, interval, start, end), reference, tolerance=tolerance
            )
            checked.append((pair, interval))
            mismatches.extend(found)
    for mismatch in mismatches:
        notify(_alert(mismatch, now=now, correlation_id=correlation_id))
    return ArchiveCheckReport(
        day=day,
        checked=tuple(checked),
        unpublished=tuple(unpublished),
        mismatches=tuple(mismatches),
    )
