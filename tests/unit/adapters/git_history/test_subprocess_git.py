"""Tests for the git subprocess adapter, against a real throwaway repository.

The integration seam for the in-memory ``FakeGit`` the batch tests use.
"""

import subprocess
from pathlib import Path

import pytest

from hermes.adapters.git_history.subprocess_git import SubprocessGit


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(  # noqa: S603 - fixed git argv built by the test
        ["git", "-c", "core.hooksPath=/dev/null", "-C", str(repo), *args],  # noqa: S607
        capture_output=True,
        check=True,
        text=True,
    )
    return done.stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """Build a repository with two commits: ``a.txt`` written, then changed and ``b.txt`` added."""
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "test@example.invalid")
    _git(tmp_path, "config", "user.name", "test")
    _git(tmp_path, "config", "commit.gpgsign", "false")
    (tmp_path / "a.txt").write_bytes(b"one\n")
    _git(tmp_path, "add", "a.txt")
    _git(tmp_path, "commit", "-q", "-m", "first")
    (tmp_path / "a.txt").write_bytes(b"two\n")
    (tmp_path / "b.txt").write_bytes(b"bee\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "second")
    return tmp_path


def test_file_at_reads_the_bytes_at_each_commit(repo: Path) -> None:
    """The precursor's bytes, not the working tree's, are what a signing hash covers."""
    git = SubprocessGit(repo)

    assert (git.file_at("HEAD~1", "a.txt"), git.file_at("HEAD", "a.txt")) == (b"one\n", b"two\n")


def test_a_path_absent_at_a_commit_has_no_bytes_and_no_id(repo: Path) -> None:
    """b.txt did not exist at the first commit, and exists at HEAD."""
    git = SubprocessGit(repo)

    assert (
        git.file_at("HEAD~1", "b.txt"),
        git.object_id("HEAD~1", "b.txt"),
        git.file_at("HEAD", "b.txt"),
    ) == (
        None,
        None,
        b"bee\n",
    )


def test_object_id_changes_with_content_and_matches_git(repo: Path) -> None:
    """Code binding compares object ids; a changed file has another id."""
    git = SubprocessGit(repo)

    first, second = git.object_id("HEAD~1", "a.txt"), git.object_id("HEAD", "a.txt")

    assert (first != second, second) == (True, _git(repo, "rev-parse", "HEAD:a.txt"))


def test_an_unknown_commit_has_no_objects(repo: Path) -> None:
    """A commit the clone does not hold yields neither an id nor bytes."""
    git = SubprocessGit(repo)

    assert (git.object_id("f" * 40, "a.txt"), git.file_at("f" * 40, "a.txt")) == (None, None)


def test_is_ancestor_follows_history(repo: Path) -> None:
    """The first commit is an ancestor of HEAD, not the reverse, and an unknown one is neither."""
    git = SubprocessGit(repo)
    first = _git(repo, "rev-parse", "HEAD~1")

    assert (
        git.is_ancestor(first, "HEAD"),
        git.is_ancestor("HEAD", first),
        git.is_ancestor("f" * 40, "HEAD"),
    ) == (True, False, False)


@pytest.mark.parametrize("commit", ["--output=/tmp/x", "", "HEAD 1"])
def test_a_commit_that_could_be_read_as_an_option_is_refused(repo: Path, commit: str) -> None:
    """A batch's commit field never reaches git as an option."""
    with pytest.raises(ValueError, match="not a commit"):
        SubprocessGit(repo).is_ancestor(commit, "HEAD")
