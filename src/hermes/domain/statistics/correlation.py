"""Rolling return correlation for the pre-registration §5.5 correlation lock.

The signed text says "30-day rolling correlation" without naming the series.
Prices trend, so a correlation of price levels reads high for any two rising
coins; the lock measures the Pearson correlation of simple daily returns over
the last 30 days, the standard reading.

Reference: pre-registration-v1.md §5.5; openspec/changes/822-portfolio-backtest D-3.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from itertools import pairwise

__all__ = ["CORRELATION_DAYS", "pearson", "return_correlation"]

CORRELATION_DAYS = 30
"""§5.5: the lock looks at the last 30 daily returns."""

_MIN_POINTS = 2
"""A correlation needs two points."""


def pearson(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    """Return the Pearson correlation of two equal-length series.

    Args:
        xs: First series.
        ys: Second series, the same length as ``xs``.

    Returns:
        The coefficient in ``[-1, 1]``, or ``None`` when it is undefined: fewer
        than two points, or a series with zero variance.

    Raises:
        ValueError: The series differ in length.
    """
    if len(xs) != len(ys):
        raise ValueError(f"series lengths differ: {len(xs)} != {len(ys)}")
    n = len(xs)
    if n < _MIN_POINTS:
        return None
    mean_x = math.fsum(xs) / n
    mean_y = math.fsum(ys) / n
    dx = [x - mean_x for x in xs]
    dy = [y - mean_y for y in ys]
    sxx = math.fsum(d * d for d in dx)
    syy = math.fsum(d * d for d in dy)
    if sxx == 0.0 or syy == 0.0:
        return None
    sxy = math.fsum(a * b for a, b in zip(dx, dy, strict=True))
    # Rounding can push a perfect correlation a hair past 1.
    return max(-1.0, min(1.0, sxy / math.sqrt(sxx * syy)))


def return_correlation(
    closes_a: Sequence[float], closes_b: Sequence[float], days: int = CORRELATION_DAYS
) -> float | None:
    """Return the correlation of the last ``days`` daily returns of two close series.

    Args:
        closes_a: Daily closes of the first pair, oldest first, aligned by
            date with ``closes_b``.
        closes_b: Daily closes of the second pair, same dates.
        days: Number of returns to correlate.

    Returns:
        The coefficient, or ``None`` when fewer than ``days + 1`` closes exist
        or a return series is constant.

    Raises:
        ValueError: The series differ in length, a close is not positive, or
            ``days`` is below 2.
    """
    if days < _MIN_POINTS:
        raise ValueError(f"days must be at least 2, got {days}")
    if len(closes_a) != len(closes_b):
        raise ValueError(f"series lengths differ: {len(closes_a)} != {len(closes_b)}")
    if len(closes_a) < days + 1:
        return None
    tail_a = closes_a[-(days + 1) :]
    tail_b = closes_b[-(days + 1) :]
    if any(c <= 0.0 for c in (*tail_a, *tail_b)):
        raise ValueError("closes must be positive")
    return pearson(_returns(tail_a), _returns(tail_b))


def _returns(closes: Sequence[float]) -> list[float]:
    return [after / before - 1.0 for before, after in pairwise(closes)]
