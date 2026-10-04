"""Invariants for ``scripts/deploy/release_notes.py`` (#626).

The notes are the record of what each deploy shipped, so they must start
at the previous deploy tag, list every commit after it and nothing before,
and carry the changelog lines those commits added. The tests build a
scratch git repository; nothing is mocked.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

_ROOT = Path(__file__).resolve().parents[3]
_REPO = "owner/hermes"


def _load() -> ModuleType:
    """Import the script module (not a package)."""
    spec = importlib.util.spec_from_file_location(
        "release_notes", _ROOT / "scripts" / "deploy" / "release_notes.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["release_notes"] = module
    spec.loader.exec_module(module)
    return module


NOTES = _load()


class Repo:
    """A scratch git repository whose commits look like squash merges on main."""

    def __init__(self, path: Path) -> None:
        """Initialise a repository on branch main at ``path``."""
        self.path = path
        self._git("init", "-q", "-b", "main")
        self._git("config", "user.name", "test")
        self._git("config", "user.email", "test@example.invalid")
        self._git("config", "commit.gpgsign", "false")

    def _git(self, *args: str) -> str:
        return subprocess.run(  # noqa: S603
            ["git", "-C", str(self.path), *args],  # noqa: S607
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def commit(self, subject: str, files: dict[str, str] | None = None) -> str:
        """Commit ``files`` (or a touch file) with ``subject``; return the sha."""
        for name, text in (files or {f"f-{subject[:8]}.txt": subject}).items():
            target = self.path / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        self._git("add", "-A")
        self._git("commit", "-q", "-m", subject)
        return self._git("rev-parse", "HEAD")

    def tag(self, name: str, sha: str) -> None:
        """Create a lightweight tag, as the GitHub release API does."""
        self._git("tag", name, sha)


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Repo:
    """Return an empty scratch repository; a hook's GIT_DIR must not redirect the script."""
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        monkeypatch.delenv(name, raising=False)
    return Repo(tmp_path)


def test_notes_list_only_commits_after_the_previous_deploy_tag(repo: Repo) -> None:
    """Invariant: a commit already shipped never appears again; every new one does."""
    shipped = repo.commit("feat: shipped before (#1)")
    repo.tag(f"deploy-{shipped[:12]}", shipped)
    repo.commit("fix: first new (#2)")
    release = repo.commit("feat: second new (#3)")

    notes = NOTES.collect(repo.path, release)

    assert notes.previous_tag == f"deploy-{shipped[:12]}"
    assert [change.subject for change in notes.changes] == [
        "feat: second new (#3)",
        "fix: first new (#2)",
    ]


def test_redeploy_of_a_tagged_commit_measures_from_the_tag_before_it(repo: Repo) -> None:
    """Invariant: redeploying a tagged commit lists what it brought, not an empty range."""
    first = repo.commit("feat: one (#1)")
    repo.tag(f"deploy-{first[:12]}", first)
    second = repo.commit("feat: two (#2)")
    repo.tag(f"deploy-{second[:12]}", second)

    notes = NOTES.collect(repo.path, second)

    assert notes.previous_tag == f"deploy-{first[:12]}"
    assert [change.subject for change in notes.changes] == ["feat: two (#2)"]


def test_first_deploy_without_a_tag_lists_the_latest_commits(repo: Repo) -> None:
    """Invariant: with no deploy tag yet the notes cap the list instead of failing."""
    for index in range(NOTES.FIRST_DEPLOY_COMMITS + 3):
        release = repo.commit(f"chore: commit {index}")

    notes = NOTES.collect(repo.path, release)

    assert notes.previous_tag is None
    assert len(notes.changes) == NOTES.FIRST_DEPLOY_COMMITS


def test_markdown_carries_the_changelog_lines_under_their_section(repo: Repo) -> None:
    """Invariant: each changelog.d line a commit added shows in its section with its PR."""
    base = repo.commit("chore: base (#1)")
    repo.tag(f"deploy-{base[:12]}", base)
    release = repo.commit(
        "feat(cli): console (#610)",
        {
            "changelog.d/600-added.md": "- Console connects to the VPS.\n",
            "changelog.d/+tidy.fixed.md": "- Errors go to stderr.\n",
            "changelog.d/README.md": "- not a fragment\n",
        },
    )

    body = NOTES.render_markdown(NOTES.collect(repo.path, release), _REPO)

    assert "### Added\n\n- Console connects to the VPS. (#610)\n" in body
    assert "### Fixed\n\n- Errors go to stderr. (#610)\n" in body
    assert "not a fragment" not in body


def test_markdown_says_redeploy_when_nothing_is_new(repo: Repo) -> None:
    """Invariant: an empty range reads as a redeploy, not as a release with no changes."""
    release = repo.commit("feat: one (#1)")
    notes = NOTES.Notes(release=release, previous_tag=f"deploy-{release[:12]}", changes=())

    body = NOTES.render_markdown(notes, _REPO)

    assert "this is a redeploy" in body


def test_telegram_message_caps_the_list_and_links_the_release(repo: Repo) -> None:
    """Invariant: the message stays short and points at the release the job publishes."""
    base = repo.commit("chore: base")
    repo.tag(f"deploy-{base[:12]}", base)
    count = NOTES.TELEGRAM_MAX_ITEMS + 2
    for index in range(count):
        release = repo.commit(f"fix: change {index} (#{100 + index})")

    text = NOTES.render_telegram(NOTES.collect(repo.path, release), _REPO)

    assert text.startswith(f"hermes {release[:12]} is live on hermes-01.\n")
    assert sum(line.startswith("- ") for line in text.splitlines()) == NOTES.TELEGRAM_MAX_ITEMS
    assert "... and 2 more" in text
    assert f"https://github.com/{_REPO}/releases/tag/deploy-{release[:12]}" in text


def test_unknown_release_exits_2(repo: Repo, capsys: pytest.CaptureFixture[str]) -> None:
    """Invariant: a bad sha is a clear failure the CD step can replace with a placeholder."""
    repo.commit("chore: base")

    code = NOTES.main(["--release", "f" * 40, "--repo", _REPO, "--root", str(repo.path)])

    assert code == 2
    assert "git failed" in capsys.readouterr().err
