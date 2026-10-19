"""Invariants for the review input that ``.github/workflows/ai-review.yml`` builds (#642).

The "Gather review context" step writes ``.ai-review/pr.md``, which the
reviewer model reads. A failed linked-issue fetch once wrote the curl
argv, job token included, into that file. These tests run the step's
own Python block with ``urllib.request.urlopen`` replaced, so no network
is touched; the real API call is exercised by every AI review run.
"""

from __future__ import annotations

import io
import json
import re
import sys
import textwrap
import urllib.error
import urllib.request
from email.message import Message
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.support.paths import REPO_ROOT

_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ai-review.yml"
_FAKE_BEARER = "ghs_FAKEtokenFAKEtokenFAKEtokenFAKE0000"  # gitleaks:allow (fake test token)
_STEP = "Gather review context"


def _step_run() -> str:
    """Return the shell of the step that builds the review input."""
    workflow = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["review"]["steps"]
    return str(next(step["run"] for step in steps if step.get("name") == _STEP))


def _python_block() -> str:
    """Return the step's inline Python program (the ``<<'PY'`` heredoc)."""
    match = re.search(r"<<'PY'\n(.*?)\n\s*PY\n", _step_run(), re.DOTALL)
    assert match is not None
    return textwrap.dedent(match.group(1))


def _build_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str, issue_reply: Any
) -> str:
    """Run the step's Python block for a PR with ``body``; return the written pr.md.

    The PR read is answered with a fixed PR; the issue read calls ``issue_reply``.
    """

    def urlopen(request: urllib.request.Request, timeout: float) -> Any:
        if "/pulls/" in request.full_url:
            pr = {"number": 7, "title": "t", "body": body}
            return io.BytesIO(json.dumps(pr).encode())
        return issue_reply(request, timeout)

    out = tmp_path / "pr.md"
    monkeypatch.setenv("GH_TOKEN", _FAKE_BEARER)
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.setenv("PR_NUMBER", "7")
    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(sys, "argv", ["-", str(out)])
    exec(compile(_python_block(), "ai-review-step", "exec"), {"__name__": "__main__"})  # noqa: S102
    return out.read_text(encoding="utf-8")


def test_failed_issue_fetch_never_writes_the_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Invariant: the text the model reads names the error type and status, never the token."""
    raised: list[urllib.error.HTTPError] = []

    def refuse(request: urllib.request.Request, timeout: float) -> Any:
        error = urllib.error.HTTPError(request.full_url, 403, "Forbidden", Message(), None)
        raised.append(error)
        raise error

    text = _build_input(tmp_path, monkeypatch, "Closes #44", refuse)
    for error in raised:
        error.close()

    assert _FAKE_BEARER not in text
    assert "(Could not read issue #44: HTTPError (HTTP 403))" in text


def test_network_error_names_only_the_exception_type(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Invariant: a non-HTTP failure adds no message text that could carry request details."""

    def drop(request: urllib.request.Request, timeout: float) -> Any:
        raise urllib.error.URLError(f"tunnel failed with {_FAKE_BEARER}")

    text = _build_input(tmp_path, monkeypatch, "Closes #44", drop)

    assert _FAKE_BEARER not in text
    assert "(Could not read issue #44: URLError)" in text


def test_token_goes_only_in_the_authorization_header(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Invariant: the token is sent only as a header, and the issue title reaches the input."""
    seen: list[urllib.request.Request] = []

    def answer(request: urllib.request.Request, timeout: float) -> Any:
        seen.append(request)
        return io.BytesIO(json.dumps({"number": 44, "title": "Linked", "body": "b"}).encode())

    text = _build_input(tmp_path, monkeypatch, "Fixes #44", answer)

    assert seen[-1].full_url == "https://api.github.com/repos/owner/repo/issues/44"
    assert seen[-1].get_header("Authorization") == f"Bearer {_FAKE_BEARER}"
    assert "# Closed issue #44: Linked" in text


def test_issue_fetch_starts_no_subprocess() -> None:
    """Invariant: no child process gets the token on its command line."""
    assert "subprocess" not in _python_block()


def test_review_job_can_read_issues() -> None:
    """Invariant: the Issues API needs issues: read; without it every linked-issue fetch fails."""
    workflow = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))

    assert workflow["jobs"]["review"]["permissions"]["issues"] == "read"


def _review_steps() -> list[dict[str, Any]]:
    """Return the review job's steps."""
    workflow = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    return list(workflow["jobs"]["review"]["steps"])


def test_reviewer_gets_no_shell() -> None:
    """Invariant (#778): the model reads only; no Bash prefix rule it could extend with --output."""
    review = next(step for step in _review_steps() if step.get("name") == "Review")
    args = str(review["with"]["claude_args"])
    allowed = re.search(r'--allowedTools "([^"]*)"', args)
    denied = re.search(r'--disallowedTools "([^"]*)"', args)

    assert allowed is not None and allowed.group(1) == "Read,Grep,Glob"
    assert denied is not None and "Bash" in denied.group(1).split(",")


def test_pr_agent_settings_are_removed_before_the_review() -> None:
    """Invariant (#778): the PR's .claude, .mcp.json and CLAUDE.md never reach the reviewer."""
    names = [str(step.get("name", "")) for step in _review_steps()]
    remove = names.index("Remove the PR's agent settings from the checkout")
    run = str(_review_steps()[remove]["run"])

    assert remove < names.index("Review")
    for target in (".claude", ".mcp.json", "CLAUDE.md", "CLAUDE.local.md"):
        assert f"-name {target}" in run


def test_review_model_is_pinned_unless_the_variable_overrides_it() -> None:
    """Invariant (#685): only a PR or the variable can change the reviewer's model."""
    review = next(step for step in _review_steps() if step.get("name") == "Review")
    args = str(review["with"]["claude_args"])

    assert "--model ${{ vars.AI_REVIEW_MODEL || 'claude-sonnet-5-5' }}" in args


def test_prompt_has_the_security_checklist() -> None:
    """Invariant (#773): the reviewer's prompt names each risk class the security lens covers."""
    prompt = (REPO_ROOT / ".github" / "ai-review" / "prompt.md").read_text(encoding="utf-8")
    section = prompt.split("## Security lens", 1)[1].split("\n## ", 1)[0].lower()

    for topic in (
        "hermes_allow_mainnet",
        "typed confirmation",
        "workflow permissions",
        "subprocess",
        "deserialis",
        "sentry",
    ):
        assert topic in section
