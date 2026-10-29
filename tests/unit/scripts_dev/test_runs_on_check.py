"""Unit tests for scripts/dev/runs-on-check.py (issue #1069).

Invariant under test: no workflow job can resolve to a GitHub-hosted runner, so
the deploy secrets and the write token stay on server-01. The one exception is
the fork route: a pull request from a fork runs on a hosted image, and that job
holds no secret but the job token.
"""

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml

from tests.support.paths import REPO_ROOT

_SCRIPT = REPO_ROOT / "scripts" / "dev" / "runs-on-check.py"
_FALLBACK = 'fromJSON(\'["self-hosted","linux","hermes"]\')'
_PINNED = "${{ vars.RUNNER_LABEL && fromJSON(vars.RUNNER_LABEL) || " + _FALLBACK + " }}"
_FORK = (
    "${{ github.event_name == 'pull_request'"
    " && github.event.pull_request.head.repo.full_name != github.repository"
    " && 'ubuntu-24.04' || (vars.RUNNER_LABEL && fromJSON(vars.RUNNER_LABEL) || "
    + _FALLBACK
    + ") }}"
)


def _script() -> ModuleType:
    """Import the script module, which is not part of a package."""
    spec = importlib.util.spec_from_file_location("runs_on_check_script", _SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _quoted(value: str) -> str:
    """Return ``value`` as a YAML single-quoted scalar."""
    return "'" + value.replace("'", "''") + "'"


def _workflow(directory: Path, runs_on: str, run: str = "echo") -> None:
    """Write a one-job workflow whose job uses ``runs_on`` verbatim and runs ``run``."""
    (directory / "w.yml").write_text(
        f"name: w\non: push\njobs:\n  j:\n    runs-on: {runs_on}\n    steps:\n      - run: {run}\n",
        encoding="utf-8",
    )


def test_every_workflow_on_main_is_pinned_to_self_hosted_runners() -> None:
    """The repository's own workflows pass, so the fallback cannot have crept back."""
    assert _script().check(REPO_ROOT / ".github" / "workflows") == []


def test_no_workflow_names_a_hosted_image_anywhere() -> None:
    """Issue #1069: no `ubuntu-latest`, `windows-latest` or `macos-latest` in the workflows."""
    text = "".join(
        p.read_text(encoding="utf-8") for p in (REPO_ROOT / ".github" / "workflows").glob("*.yml")
    )

    found = [i for i in ("ubuntu-latest", "windows-latest", "macos-latest") if i in text]

    assert found == []


@pytest.mark.parametrize(
    "runs_on",
    [
        "ubuntu-latest",
        "${{ vars.RUNNER_LABEL && fromJSON(vars.RUNNER_LABEL) || 'ubuntu-latest' }}",
        "${{ vars.RUNNER_LABEL && fromJSON(vars.RUNNER_LABEL) }}",
        "[ubuntu-22.04]",
        "[linux, x64]",
        "${{ matrix.os }}",
        "macos-14",
    ],
)
def test_a_job_that_can_reach_a_hosted_runner_fails(tmp_path: Path, runs_on: str) -> None:
    """Hosted images, missing fallbacks and unresolvable expressions are all refused."""
    _workflow(tmp_path, runs_on)

    problems = _script().check(tmp_path)

    assert len(problems) == 1
    assert problems[0].startswith("w.yml: job 'j':")


@pytest.mark.parametrize("runs_on", [_PINNED, "[self-hosted, hermes-backtest]", "self-hosted"])
def test_a_job_pinned_to_self_hosted_passes(tmp_path: Path, runs_on: str) -> None:
    """A label chain ending in the fixed self-hosted set, or a self-hosted list, is accepted."""
    _workflow(tmp_path, runs_on)

    assert _script().check(tmp_path) == []


def test_a_reusable_workflow_call_is_checked_where_it_runs(tmp_path: Path) -> None:
    """A `uses:` job has no runs-on of its own; the callee workflow is the one checked."""
    (tmp_path / "w.yml").write_text(
        "name: w\non: push\njobs:\n  j:\n    uses: ./.github/workflows/_x.yml\n", encoding="utf-8"
    )

    assert _script().check(tmp_path) == []


def test_the_self_test_passes() -> None:
    """The script's own sanity cases accept pinned forms and reject hosted ones."""
    result: Any = _script().self_test()

    assert result == 0


def test_the_fork_route_passes_when_the_job_holds_only_the_job_token(tmp_path: Path) -> None:
    """A fork's pull request may run on a hosted image when nothing secret is in reach."""
    _workflow(tmp_path, _quoted(_FORK), run='"echo ${{ secrets.GITHUB_TOKEN }}"')

    assert _script().check(tmp_path) == []


@pytest.mark.parametrize(
    ("read", "names"),
    [
        ("secrets.VPS_SSH_KEY", "VPS_SSH_KEY"),
        ("secrets['VPS_SSH_KEY']", "VPS_SSH_KEY"),
        ("toJSON(secrets)", "*"),
    ],
)
def test_the_fork_route_fails_when_the_job_reads_a_secret(
    tmp_path: Path, read: str, names: str
) -> None:
    """A job that can run on a hosted image must not read a deploy secret, in any syntax."""
    _workflow(tmp_path, _quoted(_FORK), run=_quoted("echo ${{ " + read + " }}"))

    problems = _script().check(tmp_path)

    assert problems == [
        f"w.yml: job 'j': fork route: the job reads secrets a fork's run must not hold: {names}"
    ]


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        (("!=", "=="), "names a GitHub-hosted image"),
        (("'ubuntu-24.04'", "'ubuntu-latest'"), "names a GitHub-hosted image"),
        (
            ('"self-hosted",', ""),
            "fork route: the non-fork branch the fallback list has no self-hosted",
        ),
    ],
)
def test_a_fork_route_that_is_not_exact_fails(
    tmp_path: Path, change: tuple[str, str], expected: str
) -> None:
    """Only the exact fork condition reaches the hosted image, and the rest stays pinned."""
    _workflow(tmp_path, _quoted(_FORK.replace(*change)))

    assert _script().check(tmp_path) == [f"w.yml: job 'j': {expected}"]


def test_ci_routes_forks_to_a_hosted_image_and_everything_else_to_the_pool(
    tmp_path: Path,
) -> None:
    """ci.yml's job uses the exact fork route, and the guard accepts it as it stands."""
    ci = REPO_ROOT / ".github" / "workflows" / "ci.yml"
    (tmp_path / "ci.yml").write_text(ci.read_text(encoding="utf-8"), encoding="utf-8")
    jobs = yaml.safe_load(ci.read_text(encoding="utf-8"))["jobs"]
    script = _script()

    routed = [name for name, job in jobs.items() if script.violation(job.get("runs-on"))]

    assert routed == ["ci-success"]
    assert script.check(tmp_path) == []
