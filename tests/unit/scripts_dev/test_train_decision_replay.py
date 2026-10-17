"""Invariants for the Train decision replay on engine PRs (#798, ADR-025).

Covers the CI wiring, ``scripts/dev/train-decision-replay.sh`` and
``scripts/dev/decision_change_approval.py``:

- Only a change to an engine file triggers a replay; any other changeset
  passes without one.
- An engine change on a runner without the Train data fails, never passes.
- A decision change passes only when the PR body cites a comment the Code
  Owner wrote on that PR.
- The CI step runs on pull requests only, after the unit tier, with the data
  path ADR-025 mounts; the CI pool mounts that path read-only.

The shell tests run the script in a throwaway git repository with no data
and no token, so nothing is replayed and no API is called; the replay itself
is covered by ``tests/unit/scripts_backtest/test_decision_replay.py`` and by
the first engine PR on server-01.
"""

import importlib.util
import io
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT = _ROOT / "scripts" / "dev" / "train-decision-replay.sh"
_WORKFLOW = _ROOT / ".github" / "workflows" / "ci.yml"
_COMPOSE = _ROOT / "scripts" / "dev" / "gh-runner" / "compose.yml"
_REPO = "ricardoafo/hermes"
_PR = 798


def _import_approval() -> ModuleType:
    """Import the approval checker by path."""
    path = _ROOT / "scripts" / "dev" / "decision_change_approval.py"
    spec = importlib.util.spec_from_file_location("decision_change_approval", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["decision_change_approval"] = module
    spec.loader.exec_module(module)
    return module


APPROVAL = _import_approval()


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(  # noqa: S603 — git from PATH, fixed arguments
        ["git", "-C", str(repo), *args],  # noqa: S607
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _repo_with_change(tmp_path: Path, changed: str) -> tuple[Path, str]:
    """Return a repository whose HEAD changes ``changed`` against the returned base SHA."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.invalid")
    _git(repo, "config", "user.name", "t")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "base")
    base = _git(repo, "rev-parse", "HEAD")
    target = repo / changed
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("change\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "head")
    return repo, base


def _run_script(repo: Path, base: str, data_dir: Path) -> subprocess.CompletedProcess[str]:
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "BASE_SHA": base,
        "HERMES_DATA_DIR": str(data_dir),
        "REPLAY_WORKDIR": str(repo.parent / "work"),
    }
    return subprocess.run(  # noqa: S603 — fixed interpreter, the repo's own script
        ["bash", str(_SCRIPT)],  # noqa: S607
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize(
    "changed", ["docs/guides/x.md", "src/hermes/entrypoints/tui.py", "scripts/dev/x.sh"]
)
def test_a_changeset_without_engine_files_passes_without_a_replay(
    tmp_path: Path, changed: str
) -> None:
    """Docs, console and tooling changes cannot change a decision: exit 0, no replay."""
    repo, base = _repo_with_change(tmp_path, changed)
    result = _run_script(repo, base, tmp_path / "no-data")
    assert result.returncode == 0, result.stderr
    assert "No engine file changed" in result.stdout


@pytest.mark.parametrize(
    "changed",
    [
        "src/hermes/domain/strategy/x.py",
        "src/hermes/application/execution/x.py",
        "src/hermes/adapters/fills/x.py",
        "config/strategies.yaml",
        "docs/experiments/swing-mvp1/fee-history.csv",
        "uv.lock",
    ],
)
def test_an_engine_change_without_train_data_fails(tmp_path: Path, changed: str) -> None:
    """An engine change on a runner with no Train data fails with an error, never passes."""
    repo, base = _repo_with_change(tmp_path, changed)
    result = _run_script(repo, base, tmp_path / "no-data")
    assert result.returncode == 1
    assert "::error::Train data not found" in result.stdout


class TestApprovalCitation:
    """What the PR body must say for a decision change to pass."""

    def test_a_link_to_a_comment_on_this_pr_is_cited(self) -> None:
        """The cited comment id is read from the approval line."""
        body = f"Summary\n\nDecision changes approved: https://github.com/{_REPO}/pull/{_PR}#issuecomment-42\n"
        assert APPROVAL.cited_comment_id(body, _REPO, _PR) == 42

    @pytest.mark.parametrize(
        "body",
        [
            "",
            f"Decision changes approved: https://github.com/{_REPO}/pull/1#issuecomment-42",
            f"Decision changes approved: https://github.com/other/repo/pull/{_PR}#issuecomment-42",
            f"Decision changes approved: https://github.com/{_REPO}/pull/{_PR}",
            f"  > Decision changes approved: https://github.com/{_REPO}/pull/{_PR}#issuecomment-42",
        ],
    )
    def test_anything_else_cites_nothing(
        self, body: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Another PR, another repo, no comment anchor or a quoted line is no citation."""
        monkeypatch.setattr(urllib.request, "urlopen", _answer(body, "ricardoafo"))
        assert APPROVAL.main([_REPO, str(_PR), "ricardoafo"]) == 1
        assert "No 'Decision changes approved:" in capsys.readouterr().out

    def test_the_owners_comment_on_this_pr_approves(self) -> None:
        """A comment by the Code Owner on this PR is an approval."""
        comment = {
            "user": {"login": "ricardoafo"},
            "issue_url": f"https://api.github.com/repos/{_REPO}/issues/{_PR}",
        }
        assert APPROVAL.is_owner_comment(comment, repo=_REPO, pr=_PR, owner="ricardoafo")

    @pytest.mark.parametrize(("login", "issue"), [("rafo-claude-bot", _PR), ("ricardoafo", 1)])
    def test_another_author_or_another_pr_does_not_approve(self, login: str, issue: int) -> None:
        """The bot's comment, or the owner's comment on another PR, is no approval."""
        comment = {
            "user": {"login": login},
            "issue_url": f"https://api.github.com/repos/{_REPO}/issues/{issue}",
        }
        assert not APPROVAL.is_owner_comment(comment, repo=_REPO, pr=_PR, owner="ricardoafo")


def _answer(pr_body: str, commenter: str) -> Any:
    def urlopen(request: urllib.request.Request, timeout: float) -> io.BytesIO:
        _ = timeout
        if "/pulls/" in request.full_url:
            return io.BytesIO(json.dumps({"body": pr_body}).encode())
        comment = {
            "user": {"login": commenter},
            "issue_url": f"https://api.github.com/repos/{_REPO}/issues/{_PR}",
        }
        return io.BytesIO(json.dumps(comment).encode())

    return urlopen


@pytest.mark.parametrize(("commenter", "code"), [("ricardoafo", 0), ("rafo-claude-bot", 1)])
def test_main_passes_only_the_owners_cited_comment(
    monkeypatch: pytest.MonkeyPatch, commenter: str, code: int
) -> None:
    """End to end over a faked API: the owner's cited comment exits 0, anyone else's 1."""
    body = f"Decision changes approved: https://github.com/{_REPO}/pull/{_PR}#issuecomment-7"
    monkeypatch.setattr(urllib.request, "urlopen", _answer(body, commenter))
    assert APPROVAL.main([_REPO, str(_PR), "ricardoafo"]) == code


def test_main_without_a_citation_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """A body with no approval line exits 1."""
    monkeypatch.setattr(urllib.request, "urlopen", _answer("no approval here", "ricardoafo"))
    assert APPROVAL.main([_REPO, str(_PR), "ricardoafo"]) == 1


def _ci_step() -> dict[str, Any]:
    workflow = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["ci-success"]["steps"]
    return dict(next(s for s in steps if s.get("name") == "Train decision replay"))


def test_ci_step_runs_on_pull_requests_after_the_unit_tier() -> None:
    """The replay runs inside CI success, on PRs only, once the unit tier passed."""
    condition = str(_ci_step()["if"])
    assert "github.event_name == 'pull_request'" in condition
    assert "steps.unit.outcome == 'success'" in condition


def test_ci_step_reads_the_mounted_data() -> None:
    """The step reads the Train data from the path the CI pool mounts."""
    step = _ci_step()
    assert step["env"]["HERMES_DATA_DIR"] == "/srv/hermes-data"
    assert step["run"] == "bash scripts/dev/train-decision-replay.sh"


def test_ci_pool_mounts_the_data_read_only() -> None:
    """The CI runners see /srv/hermes-data read-only, so a PR job cannot change a kline."""
    compose = yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))
    mounts = [v for v in compose["services"]["runner"]["volumes"] if isinstance(v, dict)]
    assert {
        "type": "bind",
        "source": "/srv/hermes-data",
        "target": "/srv/hermes-data",
        "read_only": True,
    } in mounts
