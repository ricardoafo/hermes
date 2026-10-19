"""Pre-registration §9 secondary metrics from an equity curve and a trade list.

Drawdowns are fractions of the running peak (0.12 = 12%). The ulcer index uses
percentage points, matching the §9 threshold of 10. Recovery is counted in
equity points, so a daily curve gives days.

Reference: pre-registration-v1.md §9; openspec/archive/354-certification-statistics
D-4, D-6.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise

__all__ = [
    "TradeResult",
    "daily_returns",
    "fees_to_gross_pnl",
    "max_drawdown",
    "profit_factor",
    "recovery_days",
    "ulcer_index",
    "win_rate",
]

_RECOVERY_DRAWDOWN = 0.08
"""§9: time-to-recovery is measured from any drawdown of 8% or more."""


@dataclass(frozen=True, slots=True)
class TradeResult:
    """One closed trade: PnL before fees, and the fees it paid."""

    gross_pnl: float
    fees: float

    @property
    def net_pnl(self) -> float:
        """PnL after fees."""
        return self.gross_pnl - self.fees


def daily_returns(equity: Sequence[float]) -> list[float]:
    """Return simple period returns of an equity curve.

    Raises:
        ValueError: A non-positive equity value.
    """
    if any(value <= 0.0 for value in equity):
        raise ValueError("equity values must be positive")
    return [after / before - 1.0 for before, after in pairwise(equity)]


def _drawdowns(equity: Sequence[float]) -> list[float]:
    peak = -math.inf
    out: list[float] = []
    for value in equity:
        peak = max(peak, value)
        out.append((peak - value) / peak)
    return out


def max_drawdown(equity: Sequence[float]) -> float | None:
    """Return the deepest peak-to-trough fall as a fraction, or ``None`` if empty."""
    if not equity:
        return None
    return max(_drawdowns(equity))


def recovery_days(equity: Sequence[float]) -> float | None:
    """Return the longest time from a peak back to it, over drawdowns of 8% or more.

    Measured from the peak to the first point at or above it again. A drawdown
    of 8% or more that has not recovered by the end is infinite (D-4).

    Returns:
        Points (days on a daily curve); 0 when no drawdown reaches 8%;
        ``None`` for an empty curve.
    """
    if not equity:
        return None
    longest = 0.0
    peak_value = equity[0]
    peak_index = 0
    deepest = 0.0
    for index, value in enumerate(equity):
        if value >= peak_value:
            if deepest >= _RECOVERY_DRAWDOWN:
                longest = max(longest, float(index - peak_index))
            peak_value, peak_index, deepest = value, index, 0.0
        else:
            deepest = max(deepest, (peak_value - value) / peak_value)
    if deepest >= _RECOVERY_DRAWDOWN:
        return math.inf
    return longest


def ulcer_index(equity: Sequence[float]) -> float | None:
    """Return the root-mean-square drawdown in percentage points, or ``None`` if empty."""
    if not equity:
        return None
    drawdowns = _drawdowns(equity)
    return math.sqrt(sum((100.0 * d) ** 2 for d in drawdowns) / len(drawdowns))


def profit_factor(trades: Sequence[TradeResult]) -> float | None:
    """Return net profit of winners over net loss of losers.

    Returns:
        ``None`` with no trades; infinity with no losing trade (D-6).
    """
    if not trades:
        return None
    wins = sum(t.net_pnl for t in trades if t.net_pnl > 0.0)
    losses = -sum(t.net_pnl for t in trades if t.net_pnl < 0.0)
    if losses == 0.0:
        return math.inf if wins > 0.0 else None
    return wins / losses


def fees_to_gross_pnl(trades: Sequence[TradeResult]) -> float | None:
    """Return total fees over total gross PnL.

    Returns:
        ``None`` with no trades; infinity when gross PnL is zero or negative,
        so the §9 row fails (D-6).
    """
    if not trades:
        return None
    gross = sum(t.gross_pnl for t in trades)
    if gross <= 0.0:
        return math.inf
    return sum(t.fees for t in trades) / gross


def win_rate(trades: Sequence[TradeResult]) -> float | None:
    """Return the share of trades with positive net PnL, or ``None`` with no trades."""
    if not trades:
        return None
    return sum(1 for t in trades if t.net_pnl > 0.0) / len(trades)
