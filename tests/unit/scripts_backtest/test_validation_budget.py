"""Unit tests for scripts/backtest/validation_budget.py (ADR-026 §3, issue #1060).

Invariant under test: a Validation touch is refused once its idea family has
used five touches in the trial log, and a sibling's touches count against it.
"""

import importlib.util
import json
import re
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType

import pytest

from tests.support.paths import REPO_ROOT

_SCRIPT = REPO_ROOT / "scripts" / "backtest" / "validation_budget.py"


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
    """Write a trial log of ``(strategy_id, split, batch id)`` lines, eight lines per touch.

    Each touch is a distinct commit, as separate Validation runs are.
    """
    lines = [
        json.dumps(
            {
                "strategy_id": sid,
                "split": split,
                "parameters": {"batch.id": batch},
                "git_sha": f"sha{index}",
            }
        )
        for index, (sid, split, batch) in enumerate(touches)
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
    touches = [("swing-donchian-4h", "validation", None)] + [
        ("swing-mvp1-v2", "validation", f"x{i}") for i in range(4)
    ]
    log = _log(tmp_path / "log.jsonl", touches)

    code = _script().main(
        ["--trial-log", str(log), "--batch", str(batch), "--candidate", "swing-b1-a"]
    )

    assert code == 1


def test_a_rerun_of_one_touch_counts_once(tmp_path: Path) -> None:
    """Sixteen lines of one strategy, batch and commit are one touch logged twice, not sixteen."""
    batch = _batch(tmp_path, "b1", [("swing-b1-a", "fam-a"), ("swing-b1-b", "fam-b")])
    line = json.dumps(
        {
            "strategy_id": "swing-b1-a",
            "split": "validation",
            "parameters": {"batch.id": "b1"},
            "git_sha": "same",
        }
    )
    log = tmp_path / "log.jsonl"
    log.write_text((line + "\n") * 16, encoding="utf-8")

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


def test_two_commits_of_one_legacy_strategy_are_two_touches(tmp_path: Path) -> None:
    """v1 has no batch id, so its separate Validation runs are told apart by commit."""
    batch = _batch(tmp_path, "b1", [("swing-b1-a", "donchian-breakout-4h"), ("swing-b1-b", "x")])
    log = _log(tmp_path / "log.jsonl", [("swing-donchian-4h", "validation", None)] * 5)

    code = _script().main(
        ["--trial-log", str(log), "--batch", str(batch), "--candidate", "swing-b1-a"]
    )

    assert code == 1


def test_unknown_candidate_is_an_error_not_a_pass(tmp_path: Path) -> None:
    """A candidate outside every batch cannot be budgeted, so the step fails with exit 3."""
    batch = _batch(tmp_path, "b1", [("swing-b1-a", "fam-a"), ("swing-b1-b", "fam-b")])
    log = _log(tmp_path / "log.jsonl", [])

    code = _script().main(
        ["--trial-log", str(log), "--batch", str(batch), "--candidate", "swing-nope"]
    )

    assert code == 3


def test_v1_touches_cover_every_validation_entry_in_the_lab_notebook() -> None:
    """The real log counts at least as many v1 touches as the notebook has Validation entries.

    A smaller count would let the family run past its budget of five.
    """
    root = _SCRIPT.parents[2] / "docs" / "experiments" / "swing-mvp1"
    notebook = (root / "lab-notebook.md").read_text(encoding="utf-8")
    entries = len(re.findall(r"(?m)^### \S+ \| validation \| ", notebook))

    counted = _script().touches(root / "trial-log.jsonl", {"swing-donchian-4h"})

    assert counted >= entries
