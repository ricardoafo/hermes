"""Unit tests for scripts/backtest/logged_touch.py (ADR-026 §3, issue #1072).

Invariant under test: a recovery dispatch is refused for a candidate stage that
already has a trial line in this batch, and only for that candidate, stage and
batch.
"""

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "backtest" / "logged_touch.py"


def _script() -> ModuleType:
    """Import the script module, which is not part of a package."""
    spec = importlib.util.spec_from_file_location("logged_touch_script", _SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _log(tmp_path: Path, *trials: dict[str, Any]) -> Path:
    """Write a trial log holding ``trials``, one JSON object per line."""
    path = tmp_path / "trial-log.jsonl"
    path.write_text("".join(json.dumps(t) + "\n" for t in trials), encoding="utf-8")
    return path


def _trial(strategy: str, split: str, batch: str | None) -> dict[str, Any]:
    """Return a trial line for ``strategy`` in ``split``, tagged with ``batch`` when given."""
    parameters = {"batch.id": batch} if batch else {}
    return {"strategy_id": strategy, "split": split, "parameters": parameters, "git_sha": "a" * 40}


def _main(log: Path, stage: str, capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    """Run the script's main for candidate ``swing-b1-a`` in batch ``b1``."""
    argv = [
        "--trial-log", str(log),
        "--batch-id", "b1",
        "--candidate", "swing-b1-a",
        "--stage", stage,
    ]  # fmt: skip
    code = int(_script().main(argv))
    return code, capsys.readouterr().out.strip()


def test_a_logged_touch_of_the_same_candidate_stage_and_batch_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """One validation line for the candidate in this batch means the touch is spent."""
    log = _log(tmp_path, _trial("swing-b1-a", "validation", "b1"))

    assert _main(log, "validation", capsys) == (1, "FAIL no_logged_touch")


@pytest.mark.parametrize(
    "other",
    [
        _trial("swing-b1-a", "train", "b1"),
        _trial("swing-b1-b", "validation", "b1"),
        _trial("swing-b1-a", "validation", "b0"),
        _trial("swing-b1-a", "validation", None),
    ],
    ids=["other-stage", "other-candidate", "other-batch", "no-batch"],
)
def test_a_line_for_another_stage_candidate_or_batch_does_not_block_recovery(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], other: dict[str, Any]
) -> None:
    """Only the exact (candidate, stage, batch) triple is a spent touch."""
    log = _log(tmp_path, other)

    assert _main(log, "validation", capsys) == (0, "PASS no_logged_touch")


def test_an_empty_log_allows_recovery(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A job that died before logging leaves no line, which is the case recovery exists for."""
    log = _log(tmp_path)

    assert _main(log, "train", capsys) == (0, "PASS no_logged_touch")


def test_an_unreadable_log_is_an_error_not_a_pass(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A missing log must not read as "nothing logged"."""
    code, _ = _main(tmp_path / "absent.jsonl", "train", capsys)

    assert code == 3
