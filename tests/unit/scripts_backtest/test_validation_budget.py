"""Unit tests for scripts/backtest/validation_budget.py (ADR-026 §3, issue #1060).

Invariant under test: a Validation touch is refused once its idea family has
used five touches in the trial log, and a sibling's touches count against it.
"""

import importlib.util
import json
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType

import pytest

_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "backtest" / "validation_budget.py"


def _script() -> ModuleType:
    """Import the script module, which is not part of a package."""
    spec = importlib.util.spec_from_file_location("validation_budget_script", _SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _batch(directory: Path, name: str, candidates: list[tuple[str, str]]) -> Path:
    """Write a minimal batch document whose front matter lists ``candidates``."""
    listing = "".join(
        f"  - strategy_id: {sid}\n    idea_family: {family}\n" for sid, family in candidates
    )
    path = directory / f"{name}.md"
    path.write_text(f"---\nbatch_id: {name}\ncandidates:\n{listing}---\n\nbody\n", encoding="utf-8")
    return path


def _log(path: Path, touches: Sequence[tuple[str, str, str | None]]) -> Path:
    """Write a trial log of ``(strategy_id, split, batch id)`` lines, eight lines per touch."""
    lines = [
        json.dumps({"strategy_id": sid, "split": split, "parameters": {"batch.id": batch}})
        for sid, split, batch in touches
        for _ in range(8)
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_touch_within_budget_passes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Four earlier touches leave room for the fifth, so the run is allowed."""
    batch = _batch(tmp_path, "b1", [("swing-b1-a", "fam-a"), ("swing-b1-b", "fam-b")])
    log = _log(tmp_path / "log.jsonl", [("swing-b1-a", "validation", f"old{i}") for i in range(4)])

    code = _script().main(
        ["--trial-log", str(log), "--batch", str(batch), "--candidate", "swing-b1-a"]
    )

    assert (code, capsys.readouterr().out.strip()) == (0, "PASS validation_budget")


def test_sixth_touch_is_refused(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Five earlier touches use the whole budget, so the next run is refused."""
    batch = _batch(tmp_path, "b1", [("swing-b1-a", "fam-a"), ("swing-b1-b", "fam-b")])
    log = _log(tmp_path / "log.jsonl", [("swing-b1-a", "validation", f"old{i}") for i in range(5)])

    code = _script().main(
        ["--trial-log", str(log), "--batch", str(batch), "--candidate", "swing-b1-a"]
    )

    assert (code, capsys.readouterr().out.strip()) == (1, "FAIL validation_budget")


def test_train_lines_and_other_families_do_not_count(tmp_path: Path) -> None:
    """Only Validation lines of the candidate's own family spend its budget."""
    batch = _batch(tmp_path, "b1", [("swing-b1-a", "fam-a"), ("swing-b1-b", "fam-b")])
    touches = [("swing-b1-a", "train", f"t{i}") for i in range(9)]
    touches += [("swing-b1-b", "validation", f"v{i}") for i in range(9)]
    log = _log(tmp_path / "log.jsonl", touches)

    code = _script().main(
        ["--trial-log", str(log), "--batch", str(batch), "--candidate", "swing-b1-a"]
    )

    assert code == 0


def test_a_sibling_in_an_earlier_batch_shares_the_budget(tmp_path: Path) -> None:
    """A candidate of an earlier batch in the same family spends the same five touches."""
    _batch(tmp_path, "b0", [("swing-b0-x", "fam-a")])
    batch = _batch(tmp_path, "b1", [("swing-b1-a", "fam-a"), ("swing-b1-b", "fam-b")])
    log = _log(tmp_path / "log.jsonl", [("swing-b0-x", "validation", f"b{i}") for i in range(5)])

    code = _script().main(
        ["--trial-log", str(log), "--batch", str(batch), "--candidate", "swing-b1-a"]
    )

    assert code == 1


def test_v2_validation_counts_for_the_donchian_family(tmp_path: Path) -> None:
    """Strategies signed before batches count in the 4h Donchian breakout family."""
    batch = _batch(tmp_path, "b1", [("swing-b1-a", "donchian-breakout-4h"), ("swing-b1-b", "x")])
    touches = [("swing-mvp1-v1", "validation", None)] + [
        ("swing-mvp1-v2", "validation", f"x{i}") for i in range(4)
    ]
    log = _log(tmp_path / "log.jsonl", touches)

    code = _script().main(
        ["--trial-log", str(log), "--batch", str(batch), "--candidate", "swing-b1-a"]
    )

    assert code == 1


def test_a_rerun_of_one_touch_counts_once(tmp_path: Path) -> None:
    """Sixteen lines of one strategy and batch are two logs of one touch, not sixteen touches."""
    batch = _batch(tmp_path, "b1", [("swing-b1-a", "fam-a"), ("swing-b1-b", "fam-b")])
    log = _log(tmp_path / "log.jsonl", [("swing-b1-a", "validation", "b1")] * 2)

    code = _script().main(
        [
            "--trial-log",
            str(log),
            "--batch",
            str(batch),
            "--candidate",
            "swing-b1-a",
            "--limit",
            "2",
        ]
    )

    assert code == 0


def test_unknown_candidate_is_an_error_not_a_pass(tmp_path: Path) -> None:
    """A candidate outside every batch cannot be budgeted, so the step fails with exit 3."""
    batch = _batch(tmp_path, "b1", [("swing-b1-a", "fam-a"), ("swing-b1-b", "fam-b")])
    log = _log(tmp_path / "log.jsonl", [])

    code = _script().main(
        ["--trial-log", str(log), "--batch", str(batch), "--candidate", "swing-nope"]
    )

    assert code == 3
