"""Unit tests for scripts/dev/regenerate-adr-index.py (issue #1197).

Invariants under test:

- every ``adr-NNN-*.md`` file and ``adr-NNN-*/README.md`` folder gives one
  row, by number, with its title, the first word of its status and every
  "Superseded by" line joined; other files are ignored;
- an ADR without a title or status line, or whose title number differs from
  its file name, is refused rather than listed wrongly;
- the table replaces only the text between the markers, and a pipe in a
  title cannot break the row;
- ``--check`` fails on a stale table and passes on a fresh one.
"""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest


def _load(root: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "regenerate_adr_index", root / "scripts" / "dev" / "regenerate-adr-index.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["regenerate_adr_index"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def index(pytestconfig: pytest.Config) -> ModuleType:
    """Import the script as a module."""
    return _load(pytestconfig.rootpath)


def _write(folder: Path, name: str, text: str) -> None:
    target = folder / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def test_one_row_per_adr_file_or_folder_in_number_order(index: ModuleType, tmp_path: Path) -> None:
    """A file ADR and a folder ADR each give a row; README and other files do not."""
    _write(tmp_path, "adr-002-two.md", "# ADR-002: Two\n\n**Status**: Accepted (2026-10-01)\n")
    _write(tmp_path, "adr-001-one/README.md", "# ADR-001: One\n\n**Status**: Proposed\n")
    _write(tmp_path, "README.md", "# ADRs\n")
    _write(tmp_path, "notes.md", "# ADR-009: Not an ADR file\n")

    assert index.read_adrs(tmp_path) == [
        index.Adr("001", "One", "adr-001-one/", "Proposed", "—"),
        index.Adr("002", "Two", "adr-002-two.md", "Accepted", "—"),
    ]


def test_every_superseded_line_is_joined_with_its_continuation(
    index: ModuleType, tmp_path: Path
) -> None:
    """Both supersede lines appear, the wrapped one on one line, in file order."""
    _write(
        tmp_path,
        "adr-003-old.md",
        "# ADR-003: Old\n\n**Status**: Superseded\n"
        "**Superseded by**: ADR-010\n"
        "**Superseded in part by**: ADR-011 (section 2,\n  fees)\n\n---\n",
    )

    assert [a.superseded_by for a in index.read_adrs(tmp_path)] == [
        "ADR-010; ADR-011 (section 2, fees)"
    ]


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("**Status**: Accepted\n", "needs a '# ADR-NNN: title'"),
        ("# ADR-004: Four\n", "needs a '# ADR-NNN: title'"),
        ("# ADR-005: Five\n\n**Status**: Accepted\n", "title says ADR-005"),
    ],
    ids=["no-title", "no-status", "wrong-number"],
)
def test_a_malformed_adr_is_refused(
    index: ModuleType, tmp_path: Path, text: str, message: str
) -> None:
    """Missing title or status, or a title number unlike the file name, raises."""
    _write(tmp_path, "adr-004-four.md", text)

    with pytest.raises(ValueError, match=message):
        index.read_adrs(tmp_path)


def test_render_replaces_only_the_marked_table_and_escapes_pipes(index: ModuleType) -> None:
    """Text outside the markers is kept; a pipe in a title is escaped."""
    readme = f"intro\n{index.START}\nold\n{index.END}\noutro\n"
    adrs = [index.Adr("007", "A | B", "adr-007-a.md", "Accepted", "—")]

    assert index.render(readme, adrs) == (
        f"intro\n{index.START}\n"
        "| ADR | Title | Status | Superseded by |\n"
        "| --- | ----- | ------ | ------------- |\n"
        "| [007](adr-007-a.md) | A \\| B | Accepted | — |\n"
        f"{index.END}\noutro\n"
    )


def test_render_refuses_a_readme_without_markers(index: ModuleType) -> None:
    """Without both markers in order there is nowhere to write; it raises."""
    with pytest.raises(ValueError, match="README needs"):
        index.render(f"{index.END}\n{index.START}\n", [])


def test_check_fails_on_a_stale_table_and_passes_once_written(
    index: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--check exits 1 before a rewrite and 0 after it."""
    _write(tmp_path, "adr-001-one.md", "# ADR-001: One\n\n**Status**: Accepted\n")
    _write(tmp_path, "README.md", f"{index.START}\n{index.END}\n")
    monkeypatch.setattr(index, "ARCHITECTURE", tmp_path)

    assert (index.main(["--check"]), index.main([]), index.main(["--check"])) == (1, 0, 0)
