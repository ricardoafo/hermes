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

RO-01 reads the coefficients of :class:`DailyCloses`, in the backtest replay
(#822) and in serve (#514); the recorded values are kept for review.

Reference: pre-registration v1 §5.5; GitHub issues #514, #835.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from itertools import combinations, pairwise
from typing import Final

from hermes.domain.market_data.bar import Bar
from hermes.domain.statistics.correlation import pearson
from hermes.domain.value_objects.pair import Pair

__all__ = [
    "CORRELATION_WINDOW_DAYS",
    "DailyCloses",
    "PairCorrelation",
    "aligned_daily_returns",
]

CORRELATION_WINDOW_DAYS: Final[int] = 30
"""Daily returns per window, from pre-registration §5.5's "30-day"."""

_ONE_DAY: Final[timedelta] = timedelta(days=1)
_MIN_POINTS: Final[int] = 2


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


class DailyCloses:
    """The latest daily closes of each pair, for the §5.5 correlation lock (RO-01).

    The backtest replay and serve's shadow executor each hold one and feed it
    every closed 1D bar in delivery order, so RO-01 reads the same
    coefficients in both (#514). Only bars already delivered are held, so a
    coefficient never sees a close after the signal it is checked for. The
    series and estimator are the ones serve records (#835).
    """

    _KEEP: Final[int] = 2 * (CORRELATION_WINDOW_DAYS + 1)

    def __init__(self) -> None:
        """Start with no closes."""
        self._closes: dict[Pair, dict[datetime, Decimal]] = {}

    def add(self, bar: Bar) -> None:
        """Record a closed 1D bar, keeping a bounded tail per pair.

        Args:
            bar: A closed 1D bar.
        """
        series = self._closes.setdefault(bar.pair, {})
        series[bar.open_time] = bar.close
        if len(series) > self._KEEP:
            del series[min(series)]

    def correlations(self) -> dict[tuple[Pair, Pair], float]:
        """Return the coefficient of every two pairs with a full 30-return window.

        Returns:
            Coefficients keyed by the two pairs in name order; a pair pair
            with fewer than 30 aligned returns, or no variance, is left out.
        """
        out: dict[tuple[Pair, Pair], float] = {}
        for a, b in combinations(sorted(self._closes, key=str), 2):
            returns_a, returns_b = aligned_daily_returns(self._closes[a], self._closes[b])
            if len(returns_a) < CORRELATION_WINDOW_DAYS:
                continue
            coeff = pearson(returns_a, returns_b)
            if coeff is not None:
                out[(a, b)] = coeff
        return out
