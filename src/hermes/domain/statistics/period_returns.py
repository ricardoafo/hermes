"""Returns over dated periods of a daily equity curve.

Point ``i`` of a curve is the equity at the end of day ``start + i``. Return
``i`` (``i >= 1``) is ``equity[i] / equity[i - 1] - 1`` and belongs to day
``start + i``; point 0 is the base and has no return of its own.

Reference: ADR-021 §5; openspec/changes/v2-backtest-funnel D-6.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta

__all__ = ["PeriodReturn", "annualised_return", "calendar_year_returns", "stretch_return"]

_MIN_POINTS = 2
"""A return needs a base point and one more."""
_DAYS_PER_YEAR = 365
"""Crypto trades every calendar day."""


@dataclass(frozen=True, slots=True)
class PeriodReturn:
    """Compounded return over a period, and how many return days it holds."""

    total: float
    days: int


def _index_of(start: date, day: date) -> int:
    return (day - start).days


def stretch_return(start: date, equity: Sequence[float], first: date, last: date) -> PeriodReturn:
    """Return the compounded return of the days ``first`` to ``last``, inclusive.

    Args:
        start: The day of point 0.
        equity: One positive equity point per day.
        first: First day of the stretch.
        last: Last day of the stretch.

    Returns:
        ``equity[last] / equity[first - 1] - 1``, with point 0 as the base when
        ``first`` is ``start``.

    Raises:
        ValueError: The stretch is reversed, leaves the curve, or holds no
            return day.
    """
    lo = max(_index_of(start, first), 1)
    hi = _index_of(start, last)
    if first < start or hi >= len(equity) or hi < lo:
        raise ValueError(
            f"stretch {first} to {last} is not inside the curve "
            f"{start} to {start + timedelta(days=len(equity) - 1)} with a return day"
        )
    return PeriodReturn(total=equity[hi] / equity[lo - 1] - 1.0, days=hi - lo + 1)


def calendar_year_returns(start: date, equity: Sequence[float]) -> dict[int, float]:
    """Return each calendar year's compounded return, for years with a return day.

    Args:
        start: The day of point 0.
        equity: One positive equity point per day.

    Returns:
        Year to return, in year order. Partial first and last years are included.
    """
    if len(equity) < _MIN_POINTS:
        return {}
    end = start + timedelta(days=len(equity) - 1)
    first_return_day = start + timedelta(days=1)
    years: dict[int, float] = {}
    for year in range(first_return_day.year, end.year + 1):
        first = max(date(year, 1, 1), first_return_day)
        last = min(date(year, 12, 31), end)
        years[year] = stretch_return(start, equity, first, last).total
    return years


def annualised_return(period: PeriodReturn) -> float:
    """Return ``period`` as a 365-day compound rate.

    Raises:
        ValueError: The period holds no return day.
    """
    if period.days < 1:
        raise ValueError("annualising needs at least one day")
    return float((1.0 + period.total) ** (_DAYS_PER_YEAR / period.days) - 1.0)
