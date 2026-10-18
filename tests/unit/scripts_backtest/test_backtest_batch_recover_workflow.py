"""Invariants for ``.github/workflows/backtest-batch-recover.yml`` (ADR-026 §3, issue #1072).

The recovery door re-runs one candidate stage of a signed batch whose job died
before it logged. These tests pin who may open it, what it refuses, and that it
spends no touch the trial log already shows. Steps with plain shell run against
temporary files, with a stub in place of ``uv run``.
"""

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[3]
_WORKFLOW = _ROOT / ".github" / "workflows" / "backtest-batch-recover.yml"

needs_tools = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("jq") is None,
    reason="the steps' shell needs bash and jq, which the runner image has",
)


def _workflow() -> dict[Any, Any]:
    """Parse backtest-batch-recover.yml."""
    loaded = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _job() -> dict[str, Any]:
    """Return the one recovery job."""
    jobs = _workflow()["jobs"]
    assert list(jobs) == ["recover"]
    job: dict[str, Any] = jobs["recover"]
    return job


def _step(name: str) -> dict[str, Any]:
    """Return one step of the recovery job by name."""
    return next(s for s in _job()["steps"] if s.get("name") == name)


def _run(
    name: str, env: dict[str, str], cwd: Path, path_prefix: Path | None = None
) -> subprocess.CompletedProcess[str]:
    """Run a step's shell with ``env`` on top of a minimal environment."""
    path = os.environ.get("PATH", "/usr/bin:/bin")
    if path_prefix is not None:
        path = f"{path_prefix}:{path}"
    return subprocess.run(  # noqa: S603 — fixed interpreter, the workflow's own script
        ["bash", "-c", str(_step(name)["run"])],  # noqa: S607 — bash from PATH, as on the runner
        env={"PATH": path, **env},
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def test_recovery_starts_only_from_a_manual_dispatch_with_the_documented_inputs() -> None:
    """No push, schedule or PR event reaches the recovery door."""
    on = _workflow()[True]

    assert list(on) == ["workflow_dispatch"]
    inputs = on["workflow_dispatch"]["inputs"]
    assert {k: v["required"] for k, v in inputs.items()} == {
        "batch": True,
        "candidate": True,
        "stage": True,
        "chain_run_id": False,
    }
    assert inputs["stage"]["options"] == ["train", "validation"]


def test_recovery_shares_the_backtest_concurrency_group_and_queues() -> None:
    """It never runs beside another backtest on the one runner (ADR-023 §1, §8)."""
    assert _workflow()["concurrency"] == {"group": "backtest", "cancel-in-progress": False}


def test_recovery_reads_the_repo_and_only_adds_actions_read_for_the_lost_chain() -> None:
    """The one extra permission is what downloading an earlier run's artifact needs."""
    assert (_workflow()["permissions"], _job()["permissions"]) == (
        {"contents": "read"},
        {"contents": "read", "actions": "read"},
    )


def test_each_stage_uses_its_own_main_only_environment() -> None:
    """A Validation recovery needs the Validation environment's rules, a Train one the Train's."""
    environment = _job()["environment"]

    assert "backtest-validation" in environment
    assert "backtest-train" in environment
    assert "inputs.stage == 'validation'" in environment


@needs_tools
def test_only_the_code_owner_may_dispatch_a_recovery(tmp_path: Path) -> None:
    """Any other account is refused, so no agent and no collaborator spends a touch."""
    env = {"OWNER": "ricardoafo", "REF": "refs/heads/main"}

    owner = _run("Code Owner dispatched this", {**env, "ACTOR": "ricardoafo"}, tmp_path)
    bot = _run("Code Owner dispatched this", {**env, "ACTOR": "rafo-claude-bot"}, tmp_path)

    assert (owner.returncode, bot.returncode) == (0, 1)


@needs_tools
def test_a_branch_never_runs_a_recovery(tmp_path: Path) -> None:
    """Only reviewed code on main runs on server-01, even for the owner."""
    result = _run(
        "Code Owner dispatched this",
        {"OWNER": "ricardoafo", "ACTOR": "ricardoafo", "REF": "refs/heads/feature"},
        tmp_path,
    )

    assert result.returncode == 1


@needs_tools
def test_plain_inputs_are_split_into_experiment_and_batch_id(tmp_path: Path) -> None:
    """The experiment and batch id come from the path, never from free text."""
    output = tmp_path / "out"
    output.write_text("")
    env = {
        "BATCH_FILE": "docs/experiments/swing-mvp1/batches/b1.md",
        "CANDIDATE": "swing-b1-a",
        "CHAIN_RUN_ID": "39056430551",
        "GITHUB_OUTPUT": str(output),
    }

    result = _run("Inputs are plain names", env, tmp_path)

    assert (result.returncode, output.read_text().splitlines()) == (
        0,
        ["experiment=swing-mvp1", "batch_id=b1"],
    )


@needs_tools
@pytest.mark.parametrize(
    ("batch", "candidate", "chain"),
    [
        ("docs/experiments/swing-mvp1/batches/../x.md", "swing-b1-a", ""),
        ("docs/experiments/swing-mvp1/batches/b1.md; rm", "swing-b1-a", ""),
        ("docs/other/b1.md", "swing-b1-a", ""),
        ("docs/experiments/swing-mvp1/batches/b1.md", "Swing B1", ""),
        ("docs/experiments/swing-mvp1/batches/b1.md", "a/../b", ""),
        ("docs/experiments/swing-mvp1/batches/b1.md", "swing-b1-a", "12x"),
    ],
)
def test_an_input_that_is_not_a_plain_name_stops_the_run(
    tmp_path: Path, batch: str, candidate: str, chain: str
) -> None:
    """The inputs reach file paths and artifact lookups, so anything odd stops here."""
    output = tmp_path / "out"
    output.write_text("")
    env = {
        "BATCH_FILE": batch,
        "CANDIDATE": candidate,
        "CHAIN_RUN_ID": chain,
        "GITHUB_OUTPUT": str(output),
    }

    result = _run("Inputs are plain names", env, tmp_path)

    assert (result.returncode, output.read_text()) == (1, "")


def _stub_uv_python(directory: Path) -> None:
    """Put a ``uv`` on PATH whose ``run python`` runs the system interpreter."""
    directory.mkdir(parents=True, exist_ok=True)
    script = directory / "uv"
    script.write_text('#!/usr/bin/env bash\nshift 2\nexec python3 "$@"\n')
    script.chmod(script.stat().st_mode | stat.S_IXUSR)


def _logged_touch(tmp_path: Path, trials: list[dict[str, Any]], stage: str) -> int:
    """Run the 'No touch already logged' step over a chain holding ``trials``."""
    chain = tmp_path / "chain"
    chain.mkdir()
    (chain / "trial-log.jsonl").write_text("".join(json.dumps(t) + "\n" for t in trials))
    summary = tmp_path / "summary"
    summary.write_text("")
    _stub_uv_python(tmp_path / "bin")
    env = {
        "RUNNER_TEMP": str(tmp_path),
        "GITHUB_STEP_SUMMARY": str(summary),
        "BATCH_ID": "b1",
        "CANDIDATE": "swing-b1-a",
        "STAGE": stage,
    }
    return _run("No touch already logged", env, _ROOT, path_prefix=tmp_path / "bin").returncode


def _trial(strategy: str, split: str) -> dict[str, Any]:
    """Return a trial line of ``strategy`` in ``split`` for batch ``b1``."""
    return {"strategy_id": strategy, "split": split, "parameters": {"batch.id": "b1"}}


@needs_tools
def test_a_candidate_stage_with_a_logged_touch_is_refused(tmp_path: Path) -> None:
    """A trial line means the touch is spent, so a dispatch cannot run it again."""
    assert _logged_touch(tmp_path, [_trial("swing-b1-a", "validation")], "validation") == 1


@needs_tools
def test_a_candidate_stage_with_no_logged_touch_is_allowed(tmp_path: Path) -> None:
    """Another stage or another candidate in the log does not block this one."""
    others = [_trial("swing-b1-a", "train"), _trial("swing-b1-b", "validation")]

    assert _logged_touch(tmp_path, others, "validation") == 0


def test_the_validation_budget_is_checked_only_for_a_validation_recovery() -> None:
    """Train has no family budget; Validation does (ADR-026 §3)."""
    step = _step("Family Validation budget")

    assert step["if"] == "${{ inputs.stage == 'validation' }}"
    assert "validation_budget.py" in step["run"]


def test_the_signed_batch_is_checked_again_before_anything_runs() -> None:
    """The same --require-signed check as the push path, and the candidate must be the batch's."""
    run = _step("Batch check")["run"]
    names = [s.get("name") for s in _job()["steps"]]

    assert "--require-signed" in run
    assert "is not a candidate of" in run
    assert names.index("Batch check") < names.index("Backtest")
    assert names.index("No touch already logged") < names.index("Backtest")


def test_every_action_is_pinned_to_a_commit() -> None:
    """A moving tag could change what runs next to the data and the Validation touch."""
    uses = [s["uses"] for s in _job()["steps"] if "uses" in s]

    assert [len(u.split("@")[1]) for u in uses] == [40] * len(uses)
