"""Pearson's correlation coefficient, the one copy every gate and lock uses.

The RO-01 correlation lock, the M-09 shadow parity check, the batch judge's
tie-break and the funnel's correlation gate all read this function, so the
same two series get the same answer, or the same ``None``, everywhere
(issue #1179). A flat series is undefined, never a noise value.

Reference: pre-registration v1 §5.5; ADR-021 §5; GitHub issue #1179.
"""

import math
from collections.abc import Sequence
from typing import Final

__all__ = ["pearson"]

_MIN_POINTS: Final[int] = 2


def pearson(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    """Return Pearson's correlation of two equal-length series.

    Args:
        xs: First series.
        ys: Second series, the same length as ``xs``.

    Returns:
        The coefficient in ``[-1, 1]``, or ``None`` when it is undefined:
        fewer than two points, a series with no variance, or a
        non-finite input.

    Raises:
        ValueError: The series differ in length.
    """
    if len(xs) != len(ys):
        raise ValueError(f"series differ in length: {len(xs)} vs {len(ys)}")
    n = len(xs)
    # A flat series is checked on its values: its float mean can differ from
    # the value by one ulp, which would leave a tiny variance and a noise result.
    if n < _MIN_POINTS or min(xs) == max(xs) or min(ys) == max(ys):
        return None
    if not all(math.isfinite(v) for v in (*xs, *ys)):
        return None
    mean_x = math.fsum(xs) / n
    mean_y = math.fsum(ys) / n
    dx = [x - mean_x for x in xs]
    dy = [y - mean_y for y in ys]
    var_x = math.fsum(d * d for d in dx)
    var_y = math.fsum(d * d for d in dy)
    # Values near the float limit overflow a variance to infinity, leaving no coefficient.
    if var_x == 0 or var_y == 0 or not math.isfinite(var_x + var_y):
        return None
    covariance = math.fsum(a * b for a, b in zip(dx, dy, strict=True))
    # Each root is taken on its own: the product of two tiny variances can
    # underflow to zero although neither is zero.
    scale = math.sqrt(var_x) * math.sqrt(var_y)
    if scale == 0:
        return None
    return max(-1.0, min(1.0, covariance / scale))
