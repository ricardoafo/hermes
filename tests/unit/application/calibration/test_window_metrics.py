"""Unit tests for computing a window's §9 metrics (pre-registration §8, §9; #354)."""

from hermes.application.calibration.window_metrics import (
    PerformanceRecord,
    TrialStats,
    compute_window_metrics,
)
from hermes.domain.statistics.performance import TradeResult


def _equity(days: int) -> tuple[float, ...]:
    """Daily equity growing 0.3% and 0.1% on alternate days."""
    values = [5000.0]
    for i in range(days - 1):
        values.append(values[-1] * (1.003 if i % 2 == 0 else 1.001))
    return tuple(values)


_TRADES = (TradeResult(50.0, 2.0),) * 30 + (TradeResult(-20.0, 2.0),) * 10


def _record(trials: TrialStats | None) -> PerformanceRecord:
    return PerformanceRecord(
        daily_equity=_equity(120), trades=_TRADES, trials=trials, bootstrap_seed=11
    )


def test_every_metric_is_computed_with_trial_statistics() -> None:
    """With trials supplied, no §9 metric is left uncomputed."""
    metrics = compute_window_metrics(_record(TrialStats(5, 1e-4)), resamples=200)
    assert metrics.is_complete()


def test_values_reach_the_gate_unchanged() -> None:
    """Trade-based metrics equal the hand-computed values for 30 winners and 10 losers."""
    metrics = compute_window_metrics(_record(TrialStats(5, 1e-4)), resamples=200)
    assert (metrics.trade_count, metrics.win_rate, metrics.max_drawdown) == (40, 0.75, 0.0)
    assert metrics.profit_factor == (30 * 48) / (10 * 22)
    assert metrics.fees_to_gross_pnl == 80 / 1300


# SEV-1: without logged trials the DSR must stay uncomputed so its §9 row fails closed
def test_without_trial_statistics_dsr_is_not_computed() -> None:
    """No trial count, no DSR (#357 supplies it)."""
    metrics = compute_window_metrics(_record(None), resamples=200)
    assert (metrics.dsr, metrics.is_complete()) == (None, False)
