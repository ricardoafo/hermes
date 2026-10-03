"""Train/test splits for Train+Val research: expanding walk-forward and purged CPCV.

Splits are index ranges over a daily series. They are for the Train and
Validation windows only; the OOS window is never split (pre-registration §8.3,
enforced by the data-split wall, not here).

Reference: pre-registration-v1.md §8.3; López de Prado (2018), *Advances in
Financial Machine Learning*, ch. 7 and 12;
openspec/changes/354-certification-statistics D-5.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations

__all__ = ["Split", "purged_cpcv_splits", "walk_forward_splits"]

_DAYS_PER_MONTH = 365.25 / 12


@dataclass(frozen=True, slots=True)
class Split:
    """One train/test split as sorted index tuples."""

    train: tuple[int, ...]
    test: tuple[int, ...]


def walk_forward_splits(
    n_days: int,
    *,
    min_train_months: int = 12,
    step_months: int = 6,
    test_months: int = 3,
) -> list[Split]:
    """Return expanding-window walk-forward splits over ``n_days`` daily points.

    Each split trains on everything before its test window, which is
    ``test_months`` long; the next split starts ``step_months`` later
    (§8.3: retrain every 6 months, test on the next 3).

    Args:
        n_days: Length of the daily series.
        min_train_months: History required before the first test window.
        step_months: Months between retrains.
        test_months: Length of each test window.

    Returns:
        The splits in time order; empty when the series is too short.

    Raises:
        ValueError: A non-positive month argument.
    """
    if min(min_train_months, step_months, test_months) < 1:
        raise ValueError("month arguments must be positive")
    step = round(step_months * _DAYS_PER_MONTH)
    test = round(test_months * _DAYS_PER_MONTH)
    start = round(min_train_months * _DAYS_PER_MONTH)
    splits: list[Split] = []
    while start + test <= n_days:
        splits.append(Split(tuple(range(start)), tuple(range(start, start + test))))
        start += step
    return splits


def purged_cpcv_splits(
    n_days: int,
    *,
    groups: int = 5,
    test_groups: int = 2,
    purge_days: int = 5,
    embargo_days: int = 3,
) -> list[Split]:
    """Return purged combinatorial cross-validation splits.

    The series is cut into ``groups`` contiguous groups. Every combination of
    ``test_groups`` of them is a test set. Training drops the ``purge_days``
    before each test group (labels that reach into it) and the
    ``embargo_days`` after it (features computed from it).

    Args:
        n_days: Length of the daily series.
        groups: Number of contiguous groups (§8.3: 5).
        test_groups: Groups held out per split (D-5: 2).
        purge_days: Training days dropped before each test group (§8.3: 5).
        embargo_days: Training days dropped after each test group (§8.3: 3).

    Returns:
        C(groups, test_groups) splits.

    Raises:
        ValueError: Invalid group counts, negative gaps, or fewer days than groups.
    """
    if not 1 <= test_groups < groups:
        raise ValueError("need 1 <= test_groups < groups")
    if purge_days < 0 or embargo_days < 0:
        raise ValueError("purge_days and embargo_days must be non-negative")
    if n_days < groups:
        raise ValueError(f"need at least {groups} days, got {n_days}")
    bounds = [n_days * g // groups for g in range(groups + 1)]
    splits: list[Split] = []
    for chosen in combinations(range(groups), test_groups):
        excluded: set[int] = set()
        test: list[int] = []
        for g in chosen:
            lo, hi = bounds[g], bounds[g + 1]
            test.extend(range(lo, hi))
            excluded.update(range(max(0, lo - purge_days), min(n_days, hi + embargo_days)))
        train = tuple(i for i in range(n_days) if i not in excluded)
        splits.append(Split(train, tuple(test)))
    return splits
