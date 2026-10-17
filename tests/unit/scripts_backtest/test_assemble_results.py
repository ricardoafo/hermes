"""Unit tests for scripts/backtest/assemble_results.py (ADR-026 §6, issue #1061).

Invariants under test: the results PR gets the batch's new trial lines byte for
byte, one value-free verdict record per candidate and stage at the documented
path, and one PASS/FAIL notebook entry per verdict. No metric value is ever
written, whatever the artifact holds.
"""

import copy
import importlib.util
import json
import re
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT = _ROOT / "scripts" / "backtest" / "assemble_results.py"
_NOTEBOOK_HEADER = "# Lab notebook\n\n## Entries\n"

_VERDICT: dict[str, Any] = {
    "schema": "hermes.verdict/1",
    "batch_id": "b1",
    "strategy_id": "swing-b1-a",
    "idea_family": "fam-a",
    "stage": "train",
    "run_id": None,
    "commit": None,
    "window": {"start": "2020-09-01", "end": "2024-06-30"},
    "trial_lines": 8,
    "checks": [
        {"check": "dsr", "status": "PASS"},
        {"check": "validation_vs_train", "status": "NA"},
    ],
    "passed": True,
    "goes_on": True,
    "champion": None,
}


def _script() -> ModuleType:
    """Import the script module, which is not part of a package."""
    spec = importlib.util.spec_from_file_location("assemble_results_script", _SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _tree(tmp_path: Path, committed: bytes = b'{"n": 1}\n') -> Path:
    """Create a repo root holding an experiment with a trial log and a notebook."""
    experiment = tmp_path / "repo" / "docs" / "experiments" / "swing-mvp1"
    experiment.mkdir(parents=True)
    (experiment / "trial-log.jsonl").write_bytes(committed)
    (experiment / "lab-notebook.md").write_text(_NOTEBOOK_HEADER, encoding="utf-8")
    return tmp_path / "repo"


def _verdict_dir(tmp_path: Path, *verdicts: dict[str, Any]) -> Path:
    """Write verdicts the way the judge job's artifact holds them."""
    directory = tmp_path / "verdicts"
    directory.mkdir(exist_ok=True)
    for verdict in verdicts:
        name = f"{verdict['strategy_id']}.{verdict['stage']}.json"
        (directory / name).write_text(json.dumps(verdict), encoding="utf-8")
    return directory


def _run(tmp_path: Path, repo: Path, verdicts: Path, **extra: str) -> int:
    """Run the script's main over ``repo`` and ``verdicts``."""
    argv = [
        "--repo-root", str(repo),
        "--experiment", "swing-mvp1",
        "--batch-id", "b1",
        "--run-id", "39056430551",
        "--commit", "a" * 40,
        "--verdicts", str(verdicts),
        "--notes-out", str(tmp_path / "notes.md"),
    ]  # fmt: skip
    for key, value in extra.items():
        argv += [f"--{key.replace('_', '-')}", value]
    return int(_script().main(argv))


def test_new_trial_lines_are_appended_byte_for_byte(tmp_path: Path) -> None:
    """The committed log grows by exactly the chain's extra bytes, in order."""
    repo = _tree(tmp_path)
    chain = tmp_path / "chain.jsonl"
    chain.write_bytes(b'{"n": 1}\n{"n":2}\n{"n": 3 }\n')

    code = _run(tmp_path, repo, _verdict_dir(tmp_path, _VERDICT), chain=str(chain))

    log = repo / "docs/experiments/swing-mvp1/trial-log.jsonl"
    assert (code, log.read_bytes()) == (0, b'{"n": 1}\n{"n":2}\n{"n": 3 }\n')


def test_a_chain_that_rewrites_the_committed_log_is_refused(tmp_path: Path) -> None:
    """A chain not starting with the committed bytes would reorder history; nothing is written."""
    repo = _tree(tmp_path)
    chain = tmp_path / "chain.jsonl"
    chain.write_bytes(b'{"n": 9}\n{"n": 2}\n')

    code = _run(tmp_path, repo, _verdict_dir(tmp_path, _VERDICT), chain=str(chain))

    log = repo / "docs/experiments/swing-mvp1/trial-log.jsonl"
    assert (code, log.read_bytes()) == (3, b'{"n": 1}\n')


def test_verdict_lands_at_the_documented_path_with_run_and_commit(tmp_path: Path) -> None:
    """Issue #1056 reads verdicts/<batch>/<strategy>.<stage>.json, with run and commit set."""
    repo = _tree(tmp_path)

    code = _run(tmp_path, repo, _verdict_dir(tmp_path, _VERDICT))

    path = repo / "docs/experiments/swing-mvp1/verdicts/b1/swing-b1-a.train.json"
    written = json.loads(path.read_text(encoding="utf-8"))
    assert (code, written["run_id"], written["commit"]) == (0, 39056430551, "a" * 40)


def test_the_only_numbers_in_a_written_verdict_are_run_id_and_trial_lines(tmp_path: Path) -> None:
    """ADR-012 D-06: no metric value reaches the repo through a verdict record."""
    repo = _tree(tmp_path)
    _run(tmp_path, repo, _verdict_dir(tmp_path, _VERDICT))
    path = repo / "docs/experiments/swing-mvp1/verdicts/b1/swing-b1-a.train.json"

    written = json.loads(path.read_text(encoding="utf-8"))

    numbers = {
        key
        for key, value in written.items()
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    }
    assert numbers == {"run_id", "trial_lines"}


@pytest.mark.parametrize(
    "mutate",
    [
        lambda v: v.update({"dsr": 0.97}),
        lambda v: v.update({"sharpe": 1.87}),
        lambda v: v.update({"passed": 0.5}),
        lambda v: v["checks"].append({"check": "dsr", "status": "PASS", "value": 0.97}),
        lambda v: v["checks"].append({"check": "dsr", "status": 0.97}),
        lambda v: v["checks"].append({"check": "dsr=0.97", "status": "PASS"}),
        lambda v: v["checks"].append({"check": "dsr", "status": "MAYBE"}),
        lambda v: v.update({"strategy_id": "../escape"}),
        lambda v: v.update({"schema": "hermes.verdict/2"}),
    ],
)
def test_a_verdict_that_could_carry_a_value_is_refused_and_nothing_is_written(
    tmp_path: Path, mutate: Any
) -> None:
    """Unknown fields, extra numbers, free-text statuses and odd names stop the whole assembly."""
    repo = _tree(tmp_path)
    verdict = copy.deepcopy(_VERDICT)
    mutate(verdict)
    directory = tmp_path / "verdicts"
    directory.mkdir()
    (directory / "v.json").write_text(json.dumps(verdict), encoding="utf-8")

    code = _run(tmp_path, repo, directory)

    notebook = repo / "docs/experiments/swing-mvp1/lab-notebook.md"
    assert (code, notebook.read_text(encoding="utf-8")) == (3, _NOTEBOOK_HEADER)
    assert not (repo / "docs/experiments/swing-mvp1/verdicts").exists()


def test_notebook_entry_holds_pass_fail_lines_and_no_other_number(tmp_path: Path) -> None:
    """One entry per verdict, with PASS/FAIL/NA per check, and no digit outside dates and names."""
    repo = _tree(tmp_path)

    _run(tmp_path, repo, _verdict_dir(tmp_path, _VERDICT))

    text = (repo / "docs/experiments/swing-mvp1/lab-notebook.md").read_text(encoding="utf-8")
    assert "- dsr = PASS" in text
    assert "- validation_vs_train = NA" in text
    assert "| train | engine | swing-b1-a train verdict in batch b1" in text
    body = re.sub(r"### \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", "", text)
    body = re.sub(r"entry-sha256: [0-9a-f]{64}", "", body)
    body = re.sub(r"swing-b1-a|§\d+|ADR-\d+|D-\d+|b1|v/1|\.json|\.jsonl|\.md|swing-mvp1", "", body)
    assert not re.search(r"\d", body)


def test_champion_is_recorded_from_the_selection(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The PR summary names the champion's id, or none when the batch has none."""
    repo = _tree(tmp_path)
    selection = tmp_path / "selection.json"
    selection.write_text(json.dumps({"champion": "swing-b1-a"}), encoding="utf-8")
    verdict = {**_VERDICT, "stage": "validation", "champion": True}

    code = _run(tmp_path, repo, _verdict_dir(tmp_path, verdict), selection=str(selection))

    assert (code, "- Champion: swing-b1-a" in capsys.readouterr().out) == (0, True)


def test_no_champion_is_recorded_as_none(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A batch with no champion says so, so the post-mortem clock is visible in the PR."""
    repo = _tree(tmp_path)
    selection = tmp_path / "selection.json"
    selection.write_text(json.dumps({"champion": None}), encoding="utf-8")
    verdict = {**_VERDICT, "stage": "validation", "passed": False, "goes_on": None}

    code = _run(tmp_path, repo, _verdict_dir(tmp_path, verdict), selection=str(selection))

    assert (code, "- Champion: none" in capsys.readouterr().out) == (0, True)


def test_a_verdict_from_another_batch_is_refused(tmp_path: Path) -> None:
    """Artifacts of two batches must never mix into one results PR."""
    repo = _tree(tmp_path)
    other = {**_VERDICT, "batch_id": "b2"}

    code = _run(tmp_path, repo, _verdict_dir(tmp_path, other))

    assert code == 3


def test_missing_notebook_is_an_error(tmp_path: Path) -> None:
    """The notebook is created at signing; its absence means the wrong experiment, so fail."""
    repo = _tree(tmp_path)
    (repo / "docs/experiments/swing-mvp1/lab-notebook.md").unlink()

    code = _run(tmp_path, repo, _verdict_dir(tmp_path, _VERDICT))

    assert code == 3
