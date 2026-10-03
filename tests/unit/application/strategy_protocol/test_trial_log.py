"""Unit tests for trial split classification and the log-backed summary.

Invariants under test (openspec/changes/357-trial-log/change.md R-1, R-3):

- A window touching Validation at all counts as a Validation touch.
- A window touching OOS cannot be a trial.
- The DSR summary is read from the trial log itself.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from hermes.adapters.trial_log.jsonl_file import JsonlTrialLog
from hermes.application.strategy_protocol.trial_log import trial_split, trial_summary
from hermes.domain.calibration.trial_record import DatasetRef, TrialRecord
from hermes.domain.market_data.dataset_manifest import DatasetManifest, DataSplit, SplitWindow

_TRAIN_START = datetime(2020, 9, 1, tzinfo=UTC)
_VAL_START = datetime(2024, 7, 1, tzinfo=UTC)
_OOS_START = datetime(2025, 9, 1, tzinfo=UTC)
_SECOND = timedelta(seconds=1)
_MANIFEST = DatasetManifest(
    experiment_id="swing-mvp1-v1",
    splits={
        DataSplit.TRAIN: SplitWindow(_TRAIN_START, _VAL_START - _SECOND),
        DataSplit.VALIDATION: SplitWindow(_VAL_START, _OOS_START - _SECOND),
        DataSplit.OOS: SplitWindow(_OOS_START, datetime(2026, 12, 31, 23, 59, 59, tzinfo=UTC)),
    },
)


def test_window_inside_train_is_a_train_trial() -> None:
    """A window ending exactly at Validation start never reads Validation."""
    assert trial_split(_MANIFEST, datetime(2024, 1, 1, tzinfo=UTC), _VAL_START) is DataSplit.TRAIN


def test_window_reaching_validation_is_a_validation_touch() -> None:
    """One second of Validation is enough to count as a touch."""
    got = trial_split(_MANIFEST, datetime(2024, 1, 1, tzinfo=UTC), _VAL_START + _SECOND)
    assert got is DataSplit.VALIDATION


def test_window_touching_oos_is_refused() -> None:
    """The OOS test is certification, never a trial."""
    with pytest.raises(ValueError, match="OOS"):
        trial_split(_MANIFEST, _OOS_START - timedelta(days=1), _OOS_START + _SECOND)


def test_window_outside_every_split_is_refused() -> None:
    """A window before Train has no pinned data and no split to count against."""
    with pytest.raises(ValueError, match="outside every split"):
        trial_split(_MANIFEST, datetime(2019, 1, 1, tzinfo=UTC), datetime(2019, 6, 1, tzinfo=UTC))


def test_summary_reads_the_count_from_the_log(tmp_path: Path) -> None:
    """The DSR trial count is what the log holds, verified on read."""
    log = JsonlTrialLog(tmp_path / "t.jsonl")
    for i, split in enumerate([DataSplit.TRAIN, DataSplit.VALIDATION, DataSplit.TRAIN]):
        log.append(
            TrialRecord(
                recorded_at=datetime(2026, 10, 3, 15, i, tzinfo=UTC),
                strategy_id="s1",
                parameters={},
                split=split,
                window_start=datetime(2024, 1, 1, tzinfo=UTC),
                window_end=datetime(2024, 3, 1, tzinfo=UTC),
                datasets=(DatasetRef("klines/BTCUSDT/4h/train", "a" * 64),),
                metrics={},
                sharpe=float(i),
                git_sha="e" * 40,
                git_dirty=False,
            )
        )
    summary = trial_summary(log, strategy_id="s1")
    assert (summary.count, summary.validation_count, summary.sharpe_variance) == (3, 1, 1.0)
