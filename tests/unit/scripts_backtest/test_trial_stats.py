"""Unit tests for scripts/backtest/trial_stats.py.

Invariant under test (openspec/changes/357-trial-log/change.md R-3): the
script prints the trial count from the log in the shape of the ``trials``
section of certification evidence, and fails on a broken log.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from hermes.adapters.trial_log.jsonl_file import JsonlTrialLog
from hermes.domain.calibration.trial_record import DatasetRef, TrialRecord
from hermes.domain.market_data.dataset_manifest import DataSplit


def _import_script():
    """Import the script module (not a package)."""
    root = Path(__file__).resolve().parents[3]
    spec = importlib.util.spec_from_file_location(
        "trial_stats_script", root / "scripts" / "backtest" / "trial_stats.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["trial_stats_script"] = module
    spec.loader.exec_module(module)
    return module


SCRIPT = _import_script()


def _append(log: JsonlTrialLog, sharpe: float) -> None:
    log.append(
        TrialRecord(
            recorded_at=datetime(2026, 10, 3, 16, tzinfo=UTC),
            strategy_id="s1",
            parameters={},
            split=DataSplit.TRAIN,
            window_start=datetime(2024, 1, 1, tzinfo=UTC),
            window_end=datetime(2024, 2, 1, tzinfo=UTC),
            datasets=(DatasetRef("klines/BTCUSDT/4h/train", "a" * 64),),
            metrics={},
            sharpe=sharpe,
            git_sha="f" * 40,
            git_dirty=False,
        )
    )


def test_prints_count_and_variance_from_the_log(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The printed count is the number of logged trials."""
    log = JsonlTrialLog(tmp_path / "t.jsonl")
    _append(log, 0.0)
    _append(log, 2.0)
    assert SCRIPT.main(["--trial-log", str(log.path)]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "count": 2,
        "validation_count": 0,
        "sharpe_variance": 2.0,
    }


def test_broken_log_exits_1(tmp_path: Path) -> None:
    """A tampered log never yields a trial count."""
    path = tmp_path / "t.jsonl"
    path.write_text("{not json\n", encoding="utf-8")
    assert SCRIPT.main(["--trial-log", str(path)]) == 1
