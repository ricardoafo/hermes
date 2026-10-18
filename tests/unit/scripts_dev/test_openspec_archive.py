"""Unit tests for scripts/dev/openspec-archive.py (#1196).

Invariants under test:

- The repo as committed passes ``--check``.
- A live change without an ``issue: <N>`` line fails; an archived change
  whose status is not Shipped or Withdrawn fails.
- A tracked file naming a spec folder that does not exist fails, except the
  placeholder names and the release notes.
- ``--sweep`` moves a closed change to the archive, sets its status and
  repoints every reference, without touching a longer name that shares the
  prefix.
"""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

from hypothesis import given
from hypothesis import strategies as st

from tests.support.paths import REPO_ROOT as _ROOT


def _load() -> ModuleType:
    """Import the script module (not a package)."""
    spec = importlib.util.spec_from_file_location(
        "openspec_archive", _ROOT / "scripts" / "dev" / "openspec-archive.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["openspec_archive"] = module
    spec.loader.exec_module(module)
    return module


ARCHIVE = _load()

_NAMES = st.from_regex(r"[a-z0-9]{1,6}(-[a-z0-9]{1,6}){0,3}", fullmatch=True)


def _git(root: Path, *args: str) -> None:
    ARCHIVE._run("git", "-C", str(root), *args)


def _repo(tmp_path: Path) -> Path:
    """Build a git repo with one live change for issue 7 and a file pointing at it."""
    root = tmp_path / "repo"
    change = root / "openspec" / "changes" / "7-fee-mark"
    change.mkdir(parents=True)
    (change / "issue.md").write_text("# Tracking issue\n\nissue: 7\n", encoding="utf-8")
    (change / "change.md").write_text(
        "# Change: 7-fee-mark\n\n**Status**: Applied\n", encoding="utf-8"
    )
    longer = root / "openspec" / "changes" / "7-fee-mark-v2"
    longer.mkdir(parents=True)
    (longer / "issue.md").write_text("issue: 8\n", encoding="utf-8")
    (root / "fees.py").write_text(
        '"""See openspec/changes/7-fee-mark/change.md and openspec/changes/7-fee-mark-v2/."""\n',
        encoding="utf-8",
    )
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    return root


def test_repo_passes_the_check() -> None:
    """Every live spec parses, every archived one is final, every spec path resolves."""
    files = ARCHIVE.tracked_text_files(_ROOT)
    assert ARCHIVE.layout_problems(_ROOT, files) == []


def test_issue_number_reads_the_data_line() -> None:
    """The ``issue: <N>`` line gives the number, a placeholder gives none."""
    assert ARCHIVE.issue_number("# Tracking issue\n\nissue: 1196\nurl: <x>\n") == 1196
    assert ARCHIVE.issue_number("issue: <N>\n") is None


def test_live_change_without_issue_line_fails(tmp_path: Path) -> None:
    """A live change whose ``issue.md`` has no number is reported by path."""
    root = _repo(tmp_path)
    (root / "openspec" / "changes" / "7-fee-mark" / "issue.md").write_text(
        "**issue**: 7\n", encoding="utf-8"
    )
    problems = ARCHIVE.layout_problems(root, [])
    assert problems == ["openspec/changes/7-fee-mark: issue.md needs an 'issue: <N>' line"]


def test_archived_change_must_be_final(tmp_path: Path) -> None:
    """An archived change still marked Applied fails; Shipped and Withdrawn pass."""
    root = _repo(tmp_path)
    archived = root / "openspec" / "archive" / "3-old"
    archived.mkdir(parents=True)
    (archived / "change.md").write_text("# C\n\n**Status**: Applied\n", encoding="utf-8")
    assert ARCHIVE.layout_problems(root, []) == [
        "openspec/archive/3-old/change.md: an archived change is Shipped or Withdrawn, "
        "not 'Applied'"
    ]
    for final in ("Shipped", "Withdrawn"):
        (archived / "change.md").write_text(f"# C\n\n**Status**: {final}\n", encoding="utf-8")
        assert ARCHIVE.layout_problems(root, []) == []


def test_dangling_reference_fails(tmp_path: Path) -> None:
    """A path to a missing spec folder is reported with its file and line."""
    root = _repo(tmp_path)
    stale = root / "notes.md"
    stale.write_text("ok\nsee openspec/archive/7-fee-mark/change.md\n", encoding="utf-8")
    assert ARCHIVE.dangling_references(root, [stale]) == [
        "notes.md:2: openspec/archive/7-fee-mark does not exist"
    ]


def test_placeholders_and_release_notes_are_not_references(tmp_path: Path) -> None:
    """Example names pass, and the release notes are left out of the scan."""
    root = _repo(tmp_path)
    example = root / "guide.md"
    example.write_text(
        "create openspec/changes/<name>/ like openspec/changes/foo-bar/\n", encoding="utf-8"
    )
    (root / "changelog.d").mkdir()
    (root / "changelog.d" / "1-fixed.md").write_text(
        "- openspec/changes/gone/change.md\n", encoding="utf-8"
    )
    _git(root, "add", "-A")
    files = ARCHIVE.tracked_text_files(root)
    assert root / "changelog.d" / "1-fixed.md" not in files
    assert ARCHIVE.dangling_references(root, files) == []


def test_with_status_replaces_or_inserts() -> None:
    """The status line is rewritten in place, or added under the title."""
    assert ARCHIVE.with_status("# C\n\n**Status**: Proposed\nbody\n", "Shipped") == (
        "# C\n\n**Status**: Shipped\nbody\n"
    )
    assert ARCHIVE.with_status("# C\nbody\n", "Withdrawn") == "# C\n\n**Status**: Withdrawn\nbody\n"


@given(name=_NAMES, suffix=_NAMES)
def test_rewrite_leaves_a_longer_name_alone(name: str, suffix: str) -> None:
    """Archiving ``name`` never repoints ``name-<suffix>``, which is another change."""
    text = f"openspec/changes/{name}/change.md openspec/changes/{name}-{suffix}/"
    assert ARCHIVE.rewrite_references(text, [name]) == (
        f"openspec/archive/{name}/change.md openspec/changes/{name}-{suffix}/"
    )


def test_sweep_archives_and_repoints(tmp_path: Path) -> None:
    """A closed change moves, says Shipped, and the references follow it."""
    root = _repo(tmp_path)
    ARCHIVE.sweep(root, {root / "openspec" / "changes" / "7-fee-mark": "Shipped"})

    assert not (root / "openspec" / "changes" / "7-fee-mark").exists()
    moved = root / "openspec" / "archive" / "7-fee-mark" / "change.md"
    assert moved.read_text(encoding="utf-8") == "# Change: 7-fee-mark\n\n**Status**: Shipped\n"
    assert (root / "fees.py").read_text(encoding="utf-8") == (
        '"""See openspec/archive/7-fee-mark/change.md and openspec/changes/7-fee-mark-v2/."""\n'
    )
    assert ARCHIVE.layout_problems(root, ARCHIVE.tracked_text_files(root)) == []
