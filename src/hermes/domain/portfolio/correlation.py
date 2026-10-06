"""30-day pair correlation of daily close-to-close returns (RO-01 input, #835).

Pre-registration §5.5 defines the correlation lock on the "30-day rolling
correlation between two open pairs". It names neither the series nor the
estimator, so this module fixes both, and the ledger records the window used
with every value:

- **Series**: simple daily returns ``close[t] / close[t-1] - 1`` of closed 1D
  bars. A return counts only when both pairs have both closes and the two
  bars are exactly one day apart, so a gap never stretches a return.
- **Window**: the 30 daily returns ending at the latest closed 1D bar, which
  needs 31 closes. Fewer stored bars give fewer returns; the sample count
  records how many the value covers.
- **Estimator**: Pearson's coefficient, clamped to ``[-1, 1]`` against
  floating-point rounding.

Record only: no rule reads these values. Which threshold comparison applies
(§5.5 says "exceeds 0.80", ``correlation_lock.py`` uses ``>=``) and whether
the window is 30 days (§5.5) or 7 days (ADR-011) are open questions for the
Code Owner (issue #514); recording the value does not settle them.

Reference: pre-registration v1 §5.5; GitHub issues #514, #835.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from itertools import pairwise
from typing import Final

from hermes.domain.value_objects.pair import Pair

__all__ = [
    "CORRELATION_WINDOW_DAYS",
    "PairCorrelation",
    "aligned_daily_returns",
    "pearson",
]

CORRELATION_WINDOW_DAYS: Final[int] = 30
"""Daily returns per window, from pre-registration §5.5's "30-day"."""

_ONE_DAY: Final[timedelta] = timedelta(days=1)
_MIN_POINTS: Final[int] = 2


def pearson(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    """Return Pearson's correlation of two equal-length series.

    Args:
        xs: First series.
        ys: Second series, the same length as ``xs``.

    Returns:
        The coefficient in ``[-1, 1]``, or ``None`` when it is undefined:
        fewer than two points or a series with no variance.

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
    mean_x = math.fsum(xs) / n
    mean_y = math.fsum(ys) / n
    dx = [x - mean_x for x in xs]
    dy = [y - mean_y for y in ys]
    var_x = math.fsum(d * d for d in dx)
    var_y = math.fsum(d * d for d in dy)
    if var_x == 0 or var_y == 0:
        return None
    covariance = math.fsum(a * b for a, b in zip(dx, dy, strict=True))
    return max(-1.0, min(1.0, covariance / math.sqrt(var_x * var_y)))


def aligned_daily_returns(
    closes_a: Mapping[datetime, Decimal],
    closes_b: Mapping[datetime, Decimal],
    *,
    window_days: int = CORRELATION_WINDOW_DAYS,
) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """Return the two pairs' daily returns on the days both have, newest last.

    Args:
        closes_a: Close of each 1D bar of the first pair, keyed by open time.
        closes_b: The same for the second pair.
        window_days: Returns to keep at most, counted back from the newest.

    Returns:
        Two equal-length tuples: the first pair's returns and the second's.
        A return enters only when both pairs have closes on two open times
        exactly one day apart.

    Raises:
        ValueError: ``window_days`` is below 1, or a close is not positive.
    """
    if window_days < 1:
        raise ValueError(f"window_days must be >= 1, got {window_days}")
    for close in (*closes_a.values(), *closes_b.values()):
        if close <= 0:
            raise ValueError(f"a close must be positive, got {close}")
    common = sorted(closes_a.keys() & closes_b.keys())
    returns_a: list[float] = []
    returns_b: list[float] = []
    for previous, current in pairwise(common):
        if current - previous != _ONE_DAY:
            continue
        returns_a.append(float(closes_a[current] / closes_a[previous] - 1))
        returns_b.append(float(closes_b[current] / closes_b[previous] - 1))
    return tuple(returns_a[-window_days:]), tuple(returns_b[-window_days:])


@dataclass(frozen=True, slots=True)
class PairCorrelation:
    """The correlation of two pairs over one window, as recorded.

    Attributes:
        pair_a: The pair whose name sorts first (the key is unordered).
        pair_b: The other pair.
        window_days: Daily returns the window allows, 30 per §5.5.
        as_of: Close time of the newest 1D bar in the window.
        correlation: Pearson's coefficient in ``[-1, 1]``.
        sample_count: Daily returns the value covers, at most ``window_days``.
    """

    pair_a: Pair
    pair_b: Pair
    window_days: int
    as_of: datetime
    correlation: float
    sample_count: int

    def __post_init__(self) -> None:
        """Reject an unordered or repeated pair, or an out-of-range value.

        Raises:
            ValueError: When an invariant does not hold.
        """
        if str(self.pair_a) >= str(self.pair_b):
            raise ValueError(f"pairs must be distinct and sorted: {self.pair_a}, {self.pair_b}")
        if self.as_of.tzinfo is None:
            raise ValueError("as_of must be timezone-aware")
        if not -1.0 <= self.correlation <= 1.0:
            raise ValueError(f"correlation must be in [-1, 1], got {self.correlation}")
        if not _MIN_POINTS <= self.sample_count <= self.window_days:
            raise ValueError(
                f"sample_count must be in [{_MIN_POINTS}, {self.window_days}], "
                f"got {self.sample_count}"
            )
