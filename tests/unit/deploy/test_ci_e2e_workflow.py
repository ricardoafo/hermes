"""Invariants for where the end-to-end tier runs (issue #663).

PR #647 broke the e2e snapshot and the CD gate failed on every run after
it, because the tier ran only weekly. These tests pin the tier into PR CI.
"""

import re
from pathlib import Path
from typing import Any

import yaml

_WORKFLOWS = Path(__file__).resolve().parents[3] / ".github" / "workflows"


def _load(name: str) -> dict[str, Any]:
    """Parse one workflow file."""
    loaded = yaml.safe_load((_WORKFLOWS / name).read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def test_pr_ci_runs_the_e2e_check_for_every_pr() -> None:
    """A PR that breaks the serve cycle must fail PR CI, not the CD gate after merge."""
    steps = _load("ci.yml")["jobs"]["ci-success"]["steps"]

    runs = [str(step.get("run", "")) for step in steps]

    assert "bash scripts/dev/ci-checks.sh e2e" in runs


def test_pr_ci_gates_the_e2e_step_on_the_changeset_output() -> None:
    """The step must not add a run to PRs that cannot change the serve cycle."""
    steps = _load("ci.yml")["jobs"]["ci-success"]["steps"]

    step = next(s for s in steps if s.get("name") == "End-to-end tests")

    assert "steps.changeset.outputs.e2e == 'true'" in step["if"]


def test_pr_ci_and_merge_queue_use_the_same_e2e_paths() -> None:
    """The two path filters must stay equal, or a PR and its batch disagree on running e2e."""
    ci = (_WORKFLOWS / "ci.yml").read_text(encoding="utf-8")
    gates = (_WORKFLOWS.parents[1] / "scripts" / "dev" / "merge-gates.sh").read_text(
        encoding="utf-8"
    )

    ci_paths = re.search(r"grep -qE '(\^\(src/hermes/[^']+)'", ci)
    gate_paths = re.search(r"E2E_PATHS='([^']+)'", gates)

    assert ci_paths is not None
    assert gate_paths is not None
    assert ci_paths.group(1) == gate_paths.group(1)


def test_merge_queue_runs_e2e_after_pytest() -> None:
    """The batch tree gets one serial e2e run after the suite, never beside it."""
    gates = (_WORKFLOWS.parents[1] / "scripts" / "dev" / "merge-gates.sh").read_text(
        encoding="utf-8"
    )

    assert gates.index('gate "e2e"') > gates.index("run_pytest\n")


def test_scheduled_workflow_no_longer_owns_the_e2e_tier() -> None:
    """The weekly e2e job is gone, so the tier has one owner in PR CI."""
    assert "e2e" not in _load("scheduled.yml")["jobs"]


def test_cd_gate_still_runs_the_e2e_tier() -> None:
    """The CD gate keeps its own e2e run until the wheel e2e replaces it."""
    text = (_WORKFLOWS / "cd.yml").read_text(encoding="utf-8")

    assert "pytest tests/e2e -m e2e" in text
