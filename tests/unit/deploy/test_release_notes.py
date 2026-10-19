"""Invariants for ``scripts/deploy/release_notes.py`` (#626).

The notes are the record of what each deploy shipped, so they must start
at the previous deploy tag, list every commit after it and nothing before,
and carry the changelog lines those commits added. The tests build a
scratch git repository; nothing is mocked.
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

from tests.support.paths import REPO_ROOT

_REPO = "owner/hermes"


def _load() -> ModuleType:
    """Import the script module (not a package)."""
    spec = importlib.util.spec_from_file_location(
        "release_notes", REPO_ROOT / "scripts" / "deploy" / "release_notes.py"
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
        # Subjects carry ':' and '#', which Windows refuses in filenames (#660).
        safe = re.sub(r"[^A-Za-z0-9]", "-", subject[:8])
        for name, text in (files or {f"f-{safe}.txt": subject}).items():
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


def test_a_wrapped_fragment_line_stays_one_changelog_item(repo: Repo) -> None:
    """Invariant: a bullet wrapped over several lines is one item, not one item per line."""
    base = repo.commit("chore: base (#1)")
    repo.tag(f"deploy-{base[:12]}", base)
    release = repo.commit(
        "fix: tidy (#7)",
        {
            "changelog.d/7-fixed.md": (
                "- **Bootstrap** skips the mount when\n"
                "  it is already mounted. Closes #7.\n"
                "- Second item.\n"
            ),
        },
    )

    body = NOTES.render_markdown(NOTES.collect(repo.path, release), _REPO)

    assert (
        "### Fixed\n\n"
        "- **Bootstrap** skips the mount when it is already mounted. Closes #7. (#7)\n"
        "- Second item. (#7)\n"
    ) in body


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


def _with_version(repo: Repo, version: str) -> str:
    """Commit a pyproject with ``version`` beside the real release-name mapping."""
    mapping = (REPO_ROOT / "src" / "hermes" / "release_version.py").read_text(encoding="utf-8")
    return repo.commit(
        f"chore(release): {version}",
        files={
            "pyproject.toml": f'[project]\nname = "hermes"\nversion = "{version}"\n',
            "src/hermes/release_version.py": mapping,
        },
    )


def test_telegram_message_leads_with_the_release_version(repo: Repo) -> None:
    """A deploy of package 1.0.0a1 announces v1.0.0-shadow.1 with its commit (ADR-022 §4)."""
    release = _with_version(repo, "1.0.0a1")

    text = NOTES.render_telegram(NOTES.collect(repo.path, release), _REPO)

    assert text.startswith(f"hermes v1.0.0-shadow.1 ({release[:12]}) is live on hermes-01.\n")


def test_the_version_is_read_at_the_release_not_the_checkout(repo: Repo) -> None:
    """Notes for an older commit name that commit's version, whatever is checked out."""
    release = _with_version(repo, "1.0.0a1")
    _with_version(repo, "1.0.0a2")

    assert NOTES.collect(repo.path, release).version == "v1.0.0-shadow.1"


def test_without_a_readable_version_the_message_names_the_commit(repo: Repo) -> None:
    """No pyproject at the release: the message falls back to the commit alone, never fails."""
    release = repo.commit("chore: no project file")

    text = NOTES.render_telegram(NOTES.collect(repo.path, release), _REPO)

    assert text.startswith(f"hermes {release[:12]} is live on hermes-01.\n")


def test_unknown_release_exits_2(repo: Repo, capsys: pytest.CaptureFixture[str]) -> None:
    """Invariant: a bad sha is a clear failure the CD step can replace with a placeholder."""
    repo.commit("chore: base")

    code = NOTES.main(["--release", "f" * 40, "--repo", _REPO, "--root", str(repo.path)])

    assert code == 2
    assert "git failed" in capsys.readouterr().err


def test_the_computed_version_overrides_the_committed_one(
    repo: Repo, capsys: pytest.CaptureFixture[str]
) -> None:
    """ADR-030 §4: the repo keeps the last release; the message names what CD stamped."""
    release = _with_version(repo, "0.2.0")

    code = NOTES.main(
        [
            "--release",
            release,
            "--repo",
            _REPO,
            "--root",
            str(repo.path),
            "--format",
            "telegram",
            "--version",
            "0.3.0",
        ]
    )

    assert code == 0
    assert capsys.readouterr().out.startswith(
        f"hermes v0.3.0 ({release[:12]}) is live on hermes-01.\n"
    )
