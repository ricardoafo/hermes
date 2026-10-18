"""Metrics of a fixed rule over the folds of pre-registration §8.3.

The strategies under test have fixed parameters, so a fold "retrain" fits
nothing: each fold reads the one run's daily returns on its train days and on
its test days. A side reports its days as date ranges, the per-day Sharpe of
its returns and their compounded return. The summary counts the folds whose
test days made money and takes the median test Sharpe.

These numbers are reported evidence. No gate, threshold or selection reads
them (issue #1181; the ``fold-evidence-is-reported-only`` import contract).

Reference: pre-registration-v1.md §8.3; openspec/changes/354-certification-statistics
D-5; openspec/changes/1181-fold-evidence/change.md.
"""

import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from hermes.domain.statistics.sharpe import sharpe_ratio
from hermes.domain.statistics.splits import Split

__all__ = ["Fold", "FoldSide", "FoldSummary", "evaluate_folds", "summarise_folds"]


@dataclass(frozen=True, slots=True)
class FoldSide:
    """The train or the test days of one fold.

    Args:
        ranges: The days as inclusive ``(first, last)`` date ranges, in order.
        days: The number of days.
        sharpe: Per-day Sharpe of the side's returns; ``None`` when undefined.
        total_return: The compounded return; ``None`` for a side with no day.
    """

    ranges: tuple[tuple[date, date], ...]
    days: int
    sharpe: float | None
    total_return: float | None


@dataclass(frozen=True, slots=True)
class Fold:
    """One fold: its train side and its test side."""

    train: FoldSide
    test: FoldSide


@dataclass(frozen=True, slots=True)
class FoldSummary:
    """What the folds of one scheme add up to.

    Args:
        folds: How many folds the scheme had.
        positive_test_folds: Folds whose test return is above zero.
        median_test_sharpe: The median of the defined test Sharpes; ``None``
            when none is defined.
    """

    folds: int
    positive_test_folds: int
    median_test_sharpe: float | None


def evaluate_folds(
    first_day: date, returns: Sequence[float], splits: Sequence[Split]
) -> tuple[Fold, ...]:
    """Measure the fixed rule's returns on each split's train and test days.

    Args:
        first_day: The day ``returns[0]`` was earned; ``returns[i]`` is earned
            on ``first_day + i``.
        returns: The run's daily returns.
        splits: Index splits over ``returns``.

    Returns:
        One fold per split, in order.

    Raises:
        ValueError: A split names a day past the end of ``returns``.
    """
    last = max((max(s.train + s.test, default=-1) for s in splits), default=-1)
    if last >= len(returns):
        raise ValueError(f"split reaches day {last} of a {len(returns)}-day series")
    return tuple(
        Fold(
            train=_side(first_day, returns, split.train),
            test=_side(first_day, returns, split.test),
        )
        for split in splits
    )


def summarise_folds(folds: Sequence[Fold]) -> FoldSummary:
    """Count the money-making test sides and take the median test Sharpe."""
    sharpes = [f.test.sharpe for f in folds if f.test.sharpe is not None]
    positive = sum(
        1 for f in folds if f.test.total_return is not None and f.test.total_return > 0.0
    )
    return FoldSummary(
        folds=len(folds),
        positive_test_folds=positive,
        median_test_sharpe=statistics.median(sharpes) if sharpes else None,
    )


def _side(first_day: date, returns: Sequence[float], days: tuple[int, ...]) -> FoldSide:
    picked = [returns[i] for i in days]
    return FoldSide(
        ranges=_ranges(first_day, days),
        days=len(days),
        sharpe=sharpe_ratio(picked),
        total_return=math.prod(1.0 + r for r in picked) - 1.0 if picked else None,
    )


def _ranges(first_day: date, days: tuple[int, ...]) -> tuple[tuple[date, date], ...]:
    runs: list[tuple[int, int]] = []
    for day in sorted(days):
        if runs and runs[-1][1] == day - 1:
            runs[-1] = (runs[-1][0], day)
        else:
            runs.append((day, day))
    return tuple(
        (first_day + timedelta(days=lo), first_day + timedelta(days=hi)) for lo, hi in runs
    )
