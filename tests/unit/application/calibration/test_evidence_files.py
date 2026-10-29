"""Tests for the evidence file readers shared by certify, funnel and the generic gate.

``load_evidence``, ``apply_performance`` and ``load_funnel_evidence`` moved here
from the CLIs unchanged; their behaviour stays pinned by
``tests/unit/entrypoints/test_certify.py`` and ``test_funnel.py``. These tests
cover what is new: :func:`load_performance`, and that the §8.3 ``folds`` key is
read past, never parsed (#1181).
"""

import json
from pathlib import Path

import pytest

from hermes.adapters.files import LocalFiles
from hermes.application.calibration.evidence_files import (
    EvidenceError,
    load_funnel_evidence,
    load_performance,
)
from hermes.application.calibration.window_metrics import TrialStats

_FILES = LocalFiles()


def _write(tmp_path: Path, payload: object) -> Path:
    path = tmp_path / "performance.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _performance(window: str) -> dict[str, object]:
    return {
        "window": window,
        "daily_equity": [5000.0, 5010.0, 5020.0],
        "trades": [{"gross_pnl": 30.0, "fees": 1.0}],
        "trials": {"count": 4, "sharpe_variance": 0.01},
        "bootstrap_seed": 7,
    }


@pytest.mark.parametrize("window", ["train_val", "oos"])
def test_load_performance_names_the_window_and_keeps_the_record(
    tmp_path: Path, window: str
) -> None:
    """The gate needs the raw record, not computed metrics, to deflate it by its own N."""
    loaded_window, record = load_performance(_write(tmp_path, _performance(window)), files=_FILES)

    assert (loaded_window, record.daily_equity, record.trials) == (
        window,
        (5000.0, 5010.0, 5020.0),
        TrialStats(4, 0.01),
    )


@pytest.mark.parametrize("payload", [[1, 2], {"window": "live"}, {**_performance("oos"), "x": 1}])
def test_load_performance_refuses_what_certify_refuses(tmp_path: Path, payload: object) -> None:
    """The generic gate reads performance files exactly as ``certify --performance`` does."""
    with pytest.raises(EvidenceError):
        load_performance(_write(tmp_path, payload), files=_FILES)


def _funnel(tmp_path: Path, name: str, payload: dict[str, object]) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_folds_never_reach_the_parsed_funnel_evidence(tmp_path: Path) -> None:
    """Pre-registration §8.3 folds are reported only: the gate's input is the same without them.

    Even a ``folds`` value no producer would write is read past, so a fold
    report can never fail or change a judged run.
    """
    plain: dict[str, object] = {
        "strategy_id": "swing-x",
        "start_date": "2020-12-31",
        "daily_equity": [5000.0, 5010.0, 5020.0],
        "trade_count": 3,
    }
    folded = {**plain, "folds": "not even an object"}

    assert load_funnel_evidence(
        _funnel(tmp_path, "folds.json", folded), strategy_id="swing-x", files=_FILES
    ) == load_funnel_evidence(
        _funnel(tmp_path, "plain.json", plain), strategy_id="swing-x", files=_FILES
    )
