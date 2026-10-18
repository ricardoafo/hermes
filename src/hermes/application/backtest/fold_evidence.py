"""The ``folds`` section of a run's funnel evidence: pre-registration §8.3 cross-validation.

§8.3 promises walk-forward (expanding window, retrain every 6 months, test on
the next 3) and purged combinatorial CV (5 groups, 5-day purge, 3-day
embargo) on Train and Validation only. The strategies have fixed parameters,
so "retrain" re-evaluates the same rule on each fold: every fold reads the
run's own daily returns, and no extra backtest or trial runs.

The section is reported evidence only. The funnel evidence reader skips it
and no gate, the BH step, the DSR or the champion choice reads it (the Code
Owner's decision of 18 Oct 2026 on issue #1181; the
``fold-evidence-is-reported-only`` import contract keeps it that way).

The folds cover exactly the run's window: a Train run's evidence ends at the
Train end, a Validation run's at the Validation end (its stage already reads
those bars); the OOS window is refused before any run (ADR-023 §4).

Reference: pre-registration-v1.md §8.3 (v2 keeps it); openspec/changes/354-certification-statistics
D-5; openspec/changes/1181-fold-evidence/change.md.
"""

from collections.abc import Sequence
from datetime import date, timedelta
from typing import Final

from hermes.domain.statistics.fold_metrics import (
    Fold,
    FoldSide,
    evaluate_folds,
    summarise_folds,
)
from hermes.domain.statistics.performance import daily_returns
from hermes.domain.statistics.splits import Split, purged_cpcv_splits, walk_forward_splits

__all__ = ["fold_evidence"]

_WALK_FORWARD: Final = {"min_train_months": 12, "step_months": 6, "test_months": 3}
"""§8.3: retrain every 6 months, test on the next 3; D-5: a 12-month first train window."""

_PURGED_CPCV: Final = {"groups": 5, "test_groups": 2, "purge_days": 5, "embargo_days": 3}
"""§8.3: 5 groups, 5-day purge, 3-day embargo; D-5: 2 groups held out per fold."""


def fold_evidence(start_date: date, daily_equity: Sequence[float]) -> dict[str, object]:
    """Return the ``folds`` section for a run's daily equity curve.

    Args:
        start_date: The day of ``daily_equity[0]``; ``daily_equity[i]`` is the
            equity at the end of ``start_date + i``, so the first return is
            earned on ``start_date + 1``.
        daily_equity: The base run's daily equity.

    Returns:
        ``{"walk_forward": ..., "purged_cpcv": ...}``, each with its settings,
        its ``folds`` and their ``summary``. A curve too short for a scheme
        gives it no fold; it never refuses.

    Raises:
        ValueError: A non-positive equity value.
    """
    returns = daily_returns(daily_equity)
    n = len(returns)
    cpcv: list[Split] = purged_cpcv_splits(n, **_PURGED_CPCV) if n >= _PURGED_CPCV["groups"] else []
    first_day = start_date + timedelta(days=1)
    return {
        "walk_forward": _scheme(
            _WALK_FORWARD, first_day, returns, walk_forward_splits(n, **_WALK_FORWARD)
        ),
        "purged_cpcv": _scheme(_PURGED_CPCV, first_day, returns, cpcv),
    }


def _scheme(
    settings: dict[str, int], first_day: date, returns: Sequence[float], splits: list[Split]
) -> dict[str, object]:
    folds = evaluate_folds(first_day, returns, splits)
    summary = summarise_folds(folds)
    return {
        **settings,
        "folds": [_fold(f) for f in folds],
        "summary": {
            "folds": summary.folds,
            "positive_test_folds": summary.positive_test_folds,
            "median_test_sharpe": summary.median_test_sharpe,
        },
    }


def _fold(fold: Fold) -> dict[str, object]:
    return {"train": _side(fold.train), "test": _side(fold.test)}


def _side(side: FoldSide) -> dict[str, object]:
    return {
        "ranges": [[lo.isoformat(), hi.isoformat()] for lo, hi in side.ranges],
        "days": side.days,
        "sharpe": side.sharpe,
        "total_return": side.total_return,
    }
