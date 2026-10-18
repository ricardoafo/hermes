"""Unit tests for scripts/dev/runs-on-check.py (issue #1069).

Invariant under test: no workflow job can resolve to a GitHub-hosted runner, so
the deploy secrets and the write token stay on server-01.
"""

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from tests.support.paths import REPO_ROOT

_SCRIPT = REPO_ROOT / "scripts" / "dev" / "runs-on-check.py"
_FALLBACK = 'fromJSON(\'["self-hosted","linux","hermes"]\')'
_PINNED = "${{ vars.RUNNER_LABEL && fromJSON(vars.RUNNER_LABEL) || " + _FALLBACK + " }}"


def _script() -> ModuleType:
    """Import the script module, which is not part of a package."""
    spec = importlib.util.spec_from_file_location("runs_on_check_script", _SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _workflow(directory: Path, runs_on: str) -> None:
    """Write a one-job workflow whose job uses ``runs_on`` verbatim."""
    (directory / "w.yml").write_text(
        f"name: w\non: push\njobs:\n  j:\n    runs-on: {runs_on}\n    steps:\n      - run: echo\n",
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
