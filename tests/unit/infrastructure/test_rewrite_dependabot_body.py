"""Unit tests for scripts/dev/rewrite-dependabot-body.py.

Protects the #162 invariants:
- The rendered body clears ``scripts/dev/pr-body-lint.sh`` (all four
  mandatory sections carry real content).
- A major bump (including a minor bump below 1.0) gets a MAJOR warning
  in Risk; a patch or minor bump does not.
- Grouped PRs list every package; single PRs fall back to the title.
- The original Dependabot body survives verbatim.
- A body that already carries the marker is not rewritten again.
"""

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from tests.support.paths import REPO_ROOT

_SCRIPT_PATH = REPO_ROOT / "scripts" / "dev" / "rewrite-dependabot-body.py"
_spec = importlib.util.spec_from_file_location("rewrite_dependabot_body", _SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None
rewrite = importlib.util.module_from_spec(_spec)
sys.modules["rewrite_dependabot_body"] = rewrite
_spec.loader.exec_module(rewrite)

_SINGLE_TITLE = (
    "chore(deps)(deps): update python-telegram-bot requirement from <22,>=21.5 to >=22.8,<23"
)
_SINGLE_BODY = (
    "Updates the requirements on [python-telegram-bot]"
    "(https://github.com/python-telegram-bot/python-telegram-bot) to permit the latest version.\n"
    "<details>\n<summary>Release notes</summary>\n"
    '<p><em>Sourced from <a href="https://github.com/python-telegram-bot/python-telegram-bot/'
    'releases">releases</a>.</em></p>\n</details>\n'
)
_GROUPED_TITLE = "ci(deps): bump the actions-minor-patch group with 2 updates"
_GROUPED_BODY = (
    "Bumps the actions-minor-patch group with 2 updates.\n\n"
    "Updates `actions/checkout` from 7.0.0 to 7.1.0\n"
    "- [Release notes](https://github.com/actions/checkout/releases)\n\n"
    "Updates `astral-sh/setup-uv` from 7.0.1 to 7.0.2\n"
)


def _lint(body: str) -> subprocess.CompletedProcess[str]:
    """Run the real PR-body lint on ``body``."""
    bash = shutil.which("bash")
    assert bash is not None
    return subprocess.run(  # noqa: S603 - fixed argv, repo-owned script
        [bash, str(REPO_ROOT / "scripts" / "dev" / "pr-body-lint.sh")],
        env={**os.environ, "PR_BODY": body},
        capture_output=True,
        text=True,
        check=False,
        cwd=REPO_ROOT,
    )


@pytest.mark.parametrize(
    ("title", "body"),
    [(_SINGLE_TITLE, _SINGLE_BODY), (_GROUPED_TITLE, _GROUPED_BODY), ("chore(deps): odd", "")],
)
def test_rendered_body_passes_pr_body_lint(title: str, body: str) -> None:
    """Every rendered body, even an unparseable one, clears the mandatory-section lint."""
    result = _lint(rewrite.render(title, body))
    assert result.returncode == 0, result.stdout + result.stderr


def test_major_requirement_bump_warns_in_risk() -> None:
    """``>=21.5`` → ``>=22.8`` crosses a major boundary and is flagged MAJOR."""
    rendered = rewrite.render(_SINGLE_TITLE, _SINGLE_BODY)
    risk = rendered.split("## Risk", 1)[1].split("## Dependabot notes", 1)[0]
    assert "**MAJOR bump**: `python-telegram-bot` 21.5 → 22.8" in risk


def test_minor_bumps_do_not_warn() -> None:
    """A grouped minor/patch update carries no MAJOR warning."""
    rendered = rewrite.render(_GROUPED_TITLE, _GROUPED_BODY)
    assert "MAJOR" not in rendered.split("## Dependabot notes", 1)[0]


def test_grouped_summary_lists_every_package() -> None:
    """Each ``Updates `pkg` from A to B`` line becomes a Summary bullet."""
    summary = rewrite.render(_GROUPED_TITLE, _GROUPED_BODY).split("## Related", 1)[0]
    assert "- `actions/checkout`: 7.0.0 → 7.1.0" in summary
    assert "- `astral-sh/setup-uv`: 7.0.1 → 7.0.2" in summary


def test_related_lists_upstream_links() -> None:
    """Upstream repo and releases links from the original body appear in Related."""
    related = rewrite.render(_SINGLE_TITLE, _SINGLE_BODY).split("## Related", 1)[1]
    related = related.split("## Testing", 1)[0]
    assert "https://github.com/python-telegram-bot/python-telegram-bot)" in related
    assert "<https://github.com/python-telegram-bot/python-telegram-bot/releases>" in related


def test_original_body_is_kept_verbatim() -> None:
    """The Dependabot body is preserved under the trailing notes section."""
    rendered = rewrite.render(_SINGLE_TITLE, _SINGLE_BODY)
    assert rendered.split("## Dependabot notes\n\n", 1)[1].strip() == _SINGLE_BODY.strip()


def test_zero_major_minor_bump_is_major() -> None:
    """Below 1.0 a minor bump is breaking under semver and is flagged."""
    assert rewrite.Update("x", "0.11.0", "0.12.0").is_major


@given(
    major=st.integers(min_value=0, max_value=50),
    minor_a=st.integers(min_value=0, max_value=50),
    minor_b=st.integers(min_value=0, max_value=50),
)
def test_same_major_above_zero_is_never_major(major: int, minor_a: int, minor_b: int) -> None:
    """Property: with an unchanged major >= 1, no minor change is flagged MAJOR."""
    major += 1
    update = rewrite.Update("x", f"{major}.{minor_a}", f"{major}.{minor_b}")
    assert not update.is_major


def test_marker_present_skips_rewrite(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A body already carrying the marker exits 3 and writes nothing (no edit loop)."""
    out = tmp_path / "body.md"
    monkeypatch.setenv("PR_TITLE", _SINGLE_TITLE)
    monkeypatch.setenv("PR_BODY", rewrite.render(_SINGLE_TITLE, _SINGLE_BODY))
    assert rewrite.main(["--out", str(out)]) == rewrite.EXIT_ALREADY_REWRITTEN
    assert not out.exists()


def test_crlf_body_is_normalised(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CRs in the incoming body never reach the rendered output."""
    out = tmp_path / "body.md"
    monkeypatch.setenv("PR_TITLE", _GROUPED_TITLE)
    monkeypatch.setenv("PR_BODY", _GROUPED_BODY.replace("\n", "\r\n"))
    assert rewrite.main(["--out", str(out)]) == 0
    assert "\r" not in out.read_bytes().decode("utf-8")
