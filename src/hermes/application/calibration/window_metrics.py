"""Compute a window's §9 metrics for the certification gates.

Turns a daily equity curve, the closed trades and the trial statistics into
the :class:`~hermes.application.calibration.certification_gates.WindowMetrics`
a gate evaluates. A metric that cannot be computed stays ``None`` and its
gate row fails closed.

Reference: pre-registration-v1.md §8, §9;
openspec/changes/354-certification-statistics/change.md.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from hermes.application.calibration.certification_gates import WindowMetrics
from hermes.domain.statistics.bootstrap import block_bootstrap_sharpe_ci
from hermes.domain.statistics.performance import (
    TradeResult,
    daily_returns,
    fees_to_gross_pnl,
    max_drawdown,
    profit_factor,
    recovery_days,
    ulcer_index,
    win_rate,
)
from hermes.domain.statistics.sharpe import deflated_sharpe_ratio

__all__ = ["PerformanceRecord", "TrialStats", "compute_window_metrics"]

BOOTSTRAP_RESAMPLES = 10_000
"""Pre-registration §8.2."""

BOOTSTRAP_BLOCK_DAYS = 30
"""Pre-registration §8.2."""


@dataclass(frozen=True, slots=True)
class TrialStats:
    """Trials run while developing the strategy (§8.1; logged by #357)."""

    count: int
    sharpe_variance: float


@dataclass(frozen=True, slots=True)
class PerformanceRecord:
    """What one window produced: one equity point per UTC day and the closed trades."""

    daily_equity: tuple[float, ...]
    trades: tuple[TradeResult, ...]
    trials: TrialStats | None
    bootstrap_seed: int


def compute_window_metrics(
    record: PerformanceRecord,
    *,
    resamples: int = BOOTSTRAP_RESAMPLES,
    block_days: int = BOOTSTRAP_BLOCK_DAYS,
) -> WindowMetrics:
    """Return every §9 metric that ``record`` supports.

    Args:
        record: The window's equity curve, trades and trial statistics.
        resamples: Bootstrap resamples (tests pass fewer).
        block_days: Bootstrap block length.

    Returns:
        The metrics; DSR is ``None`` without trial statistics.
    """
    equity: Sequence[float] = record.daily_equity
    returns = daily_returns(equity)
    dsr = (
        None
        if record.trials is None
        else deflated_sharpe_ratio(returns, record.trials.count, record.trials.sharpe_variance)
    )
    interval = block_bootstrap_sharpe_ci(
        returns,
        seed=record.bootstrap_seed,
        resamples=resamples,
        block_length=block_days,
    )
    return WindowMetrics(
        dsr=dsr,
        bootstrap_ci_lower=None if interval is None else interval.lower,
        trade_count=len(record.trades),
        max_drawdown=max_drawdown(equity),
        recovery_days=recovery_days(equity),
        profit_factor=profit_factor(record.trades),
        ulcer_index=ulcer_index(equity),
        fees_to_gross_pnl=fees_to_gross_pnl(record.trades),
        win_rate=win_rate(record.trades),
    )
