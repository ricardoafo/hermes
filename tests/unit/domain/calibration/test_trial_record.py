"""Unit tests for trial records and the trial summary behind the DSR.

Invariants under test (openspec/changes/357-trial-log/change.md R-1, R-3):

- A trial record cannot describe an OOS run, a naive time, an empty dataset
  list or a missing commit.
- Its parameter and metric mappings cannot be changed after creation.
- The summary counts every trial, counts Validation touches separately, and
  gives a Sharpe variance only when two or more trials report a Sharpe.
"""

from __future__ import annotations

import statistics
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hermes.domain.calibration.trial_record import (
    DatasetRef,
    TrialRecord,
    summarise_trials,
)
from hermes.domain.market_data.dataset_manifest import DataSplit

_SHA = "c" * 40


def _record(
    split: DataSplit = DataSplit.TRAIN, sharpe: float | None = None, strategy_id: str = "s1"
) -> TrialRecord:
    return TrialRecord(
        recorded_at=datetime(2026, 10, 3, 14, tzinfo=UTC),
        strategy_id=strategy_id,
        parameters={"donchian_period": "20"},
        split=split,
        window_start=datetime(2024, 1, 1, tzinfo=UTC),
        window_end=datetime(2024, 3, 31, tzinfo=UTC),
        datasets=(DatasetRef("klines/BTCUSDT/4h/train", "a" * 64),),
        metrics={"fills_count": "8"},
        sharpe=sharpe,
        git_sha=_SHA,
        git_dirty=False,
    )


def test_oos_split_is_rejected() -> None:
    """The one-shot OOS test is certification and must never be logged as a trial."""
    with pytest.raises(ValueError, match="cannot be OOS"):
        _record(split=DataSplit.OOS)


def test_naive_window_is_rejected() -> None:
    """Naive datetimes would misclassify the split, so they are rejected."""
    with pytest.raises(ValueError, match="timezone-aware"):
        replace(_record(), window_start=datetime(2024, 1, 1))


def test_inverted_window_is_rejected() -> None:
    """A window must start before it ends."""
    with pytest.raises(ValueError, match="precede"):
        replace(_record(), window_end=datetime(2023, 1, 1, tzinfo=UTC))


def test_trial_without_datasets_is_rejected() -> None:
    """A trial must name the pinned bars it read, or it cannot be reproduced."""
    with pytest.raises(ValueError, match="at least one pinned dataset"):
        replace(_record(), datasets=())


@pytest.mark.parametrize("sha", ["", "abc", "C" * 40, "c" * 39])
def test_git_sha_must_be_a_full_commit(sha: str) -> None:
    """Only a full 40-char lowercase SHA pins the code a trial ran."""
    with pytest.raises(ValueError, match="git_sha"):
        replace(_record(), git_sha=sha)


def test_empty_strategy_id_is_rejected() -> None:
    """A trial must say which strategy it tested."""
    with pytest.raises(ValueError, match="strategy_id"):
        replace(_record(), strategy_id="")


def test_dataset_ref_rejects_malformed_hash() -> None:
    """A dataset reference carries the manifest's sha256, not an arbitrary string."""
    with pytest.raises(ValueError, match="sha256"):
        DatasetRef("klines/BTCUSDT/4h/train", "xyz")


def test_parameters_and_metrics_cannot_be_mutated() -> None:
    """A logged trial is immutable, including through its mappings."""
    source = {"donchian_period": "20"}
    record = replace(_record(), parameters=source)
    source["donchian_period"] = "30"
    assert record.parameters["donchian_period"] == "20"
    metrics: Any = record.metrics
    with pytest.raises(TypeError):
        metrics["fills_count"] = "9"


def test_summary_counts_every_trial_and_validation_touches() -> None:
    """Validation touches are counted separately, and also count toward the total."""
    summary = summarise_trials(
        [_record(), _record(DataSplit.VALIDATION), _record(DataSplit.VALIDATION)]
    )
    assert (summary.count, summary.validation_count) == (3, 2)


def test_summary_filters_by_strategy() -> None:
    """Another strategy's trials do not inflate this strategy's DSR penalty."""
    summary = summarise_trials([_record(), _record(strategy_id="s2")], strategy_id="s2")
    assert summary.count == 1


def test_variance_needs_two_sharpes() -> None:
    """With fewer than two Sharpe ratios there is no sample variance to report."""
    assert summarise_trials([_record(sharpe=0.5), _record()]).sharpe_variance is None
    assert summarise_trials([]).count == 0


@given(st.lists(st.floats(-3, 3, allow_nan=False), min_size=2, max_size=20))
def test_variance_is_the_sample_variance_of_reported_sharpes(sharpes: list[float]) -> None:
    """Trials without a Sharpe are counted but excluded from the variance."""
    records = [_record(sharpe=s) for s in sharpes] + [_record()]
    summary = summarise_trials(records)
    assert summary.count == len(sharpes) + 1
    assert summary.sharpe_variance == pytest.approx(statistics.variance(sharpes))
