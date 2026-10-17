"""Invariants for how the workflows check out the repository (#597).

``actions/checkout`` writes the job token into ``.git/config`` unless the
step sets ``persist-credentials: false``. Any code the job runs afterwards
(tests, ``uv sync`` of third-party packages, scripts) can then read the
token. Only a job that pushes needs it; these tests keep every other
checkout credential-free and the template-injected values out of shell.
"""

from pathlib import Path
from typing import Any

import yaml

_ROOT = Path(__file__).resolve().parents[3]
_WORKFLOWS = _ROOT / ".github" / "workflows"

# Jobs allowed to keep the token, and why:
# - merge-queue.yml: its Revalidate job merges; issue #779 splits it.
# - scheduled.yml branch-prune: deletes branches with `git push`.
_KEEPS_TOKEN = {("merge-queue.yml", None), ("scheduled.yml", "branch-prune")}


def _checkouts() -> list[tuple[str, str, dict[str, Any]]]:
    """Return (workflow file, job id, step) for every checkout step."""
    found: list[tuple[str, str, dict[str, Any]]] = []
    for path in sorted(_WORKFLOWS.glob("*.yml")):
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        for job_id, job in (workflow.get("jobs") or {}).items():
            found.extend(
                (path.name, job_id, step)
                for step in job.get("steps") or []
                if str(step.get("uses", "")).startswith("actions/checkout@")
            )
    return found


def _keeps_token(workflow: str, job: str) -> bool:
    return (workflow, None) in _KEEPS_TOKEN or (workflow, job) in _KEEPS_TOKEN


def test_only_pushing_jobs_keep_the_checkout_token() -> None:
    """Every checkout outside the allow-list sets persist-credentials: false."""
    keeping = [
        f"{workflow}:{job}"
        for workflow, job, step in _checkouts()
        if not _keeps_token(workflow, job)
        and (step.get("with") or {}).get("persist-credentials") is not False
    ]

    assert keeping == []


def test_the_allow_list_names_only_jobs_that_exist() -> None:
    """A renamed or removed job cannot leave a stale exemption behind."""
    present = {(workflow, job) for workflow, job, _ in _checkouts()}
    workflows = {workflow for workflow, _ in present}

    stale = [
        entry
        for entry in _KEEPS_TOKEN
        if (entry[1] is None and entry[0] not in workflows)
        or (entry[1] is not None and entry not in present)
    ]

    assert stale == []


def test_the_test_deletion_guard_reads_the_base_ref_from_env() -> None:
    """``github.base_ref`` reaches the shell as $BASE_REF, never as template text."""
    workflow = yaml.safe_load((_WORKFLOWS / "ci.yml").read_text(encoding="utf-8"))
    step = next(
        step
        for job in workflow["jobs"].values()
        for step in job.get("steps") or []
        if step.get("name") == "Test-deletion guard"
    )

    assert (step["env"].get("BASE_REF"), "${{" in step["run"]) == ("${{ github.base_ref }}", False)


def test_the_kill_rate_floor_reads_the_rate_from_env() -> None:
    """The mutation kill rate reaches the shell and Python as data, not as code."""
    workflow = yaml.safe_load((_WORKFLOWS / "scheduled.yml").read_text(encoding="utf-8"))
    step = next(
        step
        for job in workflow["jobs"].values()
        for step in job.get("steps") or []
        if step.get("name") == "Enforce kill-rate floor"
    )

    assert (step["env"].get("KILL_RATE"), "${{" in step["run"]) == (
        "${{ steps.rate.outputs.kill }}",
        False,
    )
