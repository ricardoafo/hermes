"""Compare live bars with reference bars for the same stream and day.

Used by the daily archive check (#359): stored live bars against the Binance
public archive. Bars are matched by ``open_time``. OHLCV must match within a
tolerance (exact by default). ``close_time`` is compared to the millisecond,
because the archive stores microseconds since 2025 while the live feed stores
milliseconds, so ``...59.999999`` and ``...59.999`` are the same close.

Reference: GitHub issues #359, #343 (risk R-6).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.value_objects.pair import Pair

__all__ = ["BarMismatch", "MismatchKind", "compare_bars"]

_OHLCV = ("open", "high", "low", "close", "volume")


class MismatchKind(StrEnum):
    """How a live bar disagrees with the reference."""

    MISSING_LIVE = "missing_live"
    """The reference has the bar, the live store does not."""

    EXTRA_LIVE = "extra_live"
    """The live store has a bar the reference does not."""

    VALUE = "value"
    """Both have the bar, with different values."""


@dataclass(frozen=True, slots=True)
class BarMismatch:
    """One disagreement, naming the stream and the bar.

    Args:
        kind: What kind of disagreement.
        pair: Pair of the stream.
        interval: Interval of the stream.
        open_time: Open time of the bar.
        detail: The differing fields as ``field live=X reference=Y``, or empty.
    """

    kind: MismatchKind
    pair: Pair
    interval: KlineInterval
    open_time: datetime
    detail: str = ""


def _ms(ts: datetime) -> datetime:
    return ts.replace(microsecond=ts.microsecond // 1000 * 1000)


def _differences(live: Bar, reference: Bar, tolerance: Decimal) -> list[str]:
    diffs = [
        f"{name} live={getattr(live, name)} reference={getattr(reference, name)}"
        for name in _OHLCV
        if abs(getattr(live, name) - getattr(reference, name)) > tolerance
    ]
    if _ms(live.close_time) != _ms(reference.close_time):
        diffs.append(
            f"close_time live={live.close_time.isoformat()} "
            f"reference={reference.close_time.isoformat()}"
        )
    return diffs


def compare_bars(
    live: Iterable[Bar],
    reference: Iterable[Bar],
    *,
    tolerance: Decimal = Decimal(0),
) -> tuple[BarMismatch, ...]:
    """Return every disagreement between ``live`` and ``reference`` bars.

    Both inputs must be bars of one pair and interval.

    Args:
        live: Stored live bars.
        reference: Reference bars for the same period.
        tolerance: Largest absolute OHLCV difference still counted as a match.

    Returns:
        Mismatches ordered by open time; empty when everything matches.

    Raises:
        ValueError: A negative tolerance.
    """
    if tolerance < 0:
        raise ValueError(f"tolerance must be >= 0, got {tolerance}")
    live_by_open = {b.open_time: b for b in live}
    reference_by_open = {b.open_time: b for b in reference}
    mismatches: list[BarMismatch] = []
    for open_time in sorted(live_by_open.keys() | reference_by_open.keys()):
        got, want = live_by_open.get(open_time), reference_by_open.get(open_time)
        if got is None and want is not None:
            mismatches.append(
                BarMismatch(MismatchKind.MISSING_LIVE, want.pair, want.interval, open_time)
            )
        elif want is None and got is not None:
            mismatches.append(
                BarMismatch(MismatchKind.EXTRA_LIVE, got.pair, got.interval, open_time)
            )
        elif got is not None and want is not None:
            diffs = _differences(got, want, tolerance)
            if diffs:
                mismatches.append(
                    BarMismatch(
                        MismatchKind.VALUE, got.pair, got.interval, open_time, "; ".join(diffs)
                    )
                )
    return tuple(mismatches)
