"""Unit tests for LabNotebook writer.

Tests cover:
- First write creates entry with correct format and trailing SHA256 comment.
- Second write appends a second entry (both present in file).
- Attempting to write entry with same header as last entry raises LabNotebookAppendError.
- SHA256 in the comment matches hashlib.sha256(entry_text.encode()).hexdigest().
- SHA256 chain verifiable: verify_sha256() returns True for each appended entry.
- last_entry_header() returns the correct header after each append.
- FileNotFoundError raised when notebook file is missing.
- Entry.render() produces header in the correct format.
"""

from __future__ import annotations

import hashlib
import itertools
from pathlib import Path

import pytest

from hermes.application.strategy_protocol.lab_notebook import (
    LabNotebook,
    LabNotebookEntry,
)
from hermes.domain.errors import LabNotebookAppendError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_notebook(tmp_path: Path, content: str = "") -> LabNotebook:
    """Create a notebook file and return a LabNotebook instance."""
    notebook_file = tmp_path / "lab-notebook.md"
    notebook_file.write_text(content, encoding="utf-8")
    return LabNotebook(notebook_path=notebook_file)


def _make_entry(
    timestamp: str = "2026-08-25T13:00:00Z",
    phase: str = "meta",
    actor: str = "claude-experimentalist",
    action: str = "unit-test write",
    **kwargs: object,
) -> LabNotebookEntry:
    """Return a LabNotebookEntry with sensible test defaults."""
    return LabNotebookEntry(
        timestamp_utc=timestamp,
        phase=phase,
        actor=actor,
        action=action,
        **kwargs,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# LabNotebookEntry.render() and header() tests
# ---------------------------------------------------------------------------


class TestLabNotebookEntryRender:
    """LabNotebookEntry.render() and header() produce the correct Markdown format."""

    def test_header_format_correct(self) -> None:
        """header() must return '### YYYY-MM-DDTHH:MM:SSZ | phase | actor | action'."""
        entry = _make_entry(
            timestamp="2026-08-25T13:00:00Z",
            phase="train",
            actor="engine",
            action="backtest run",
        )
        header = entry.header()
        assert header == "### 2026-08-25T13:00:00Z | train | engine | backtest run", (
            f"Entry header format incorrect. Got: '{header}'"
        )

    def test_render_starts_with_header(self) -> None:
        """render() must start with the entry header line."""
        entry = _make_entry()
        rendered = entry.render()
        assert rendered.startswith("### 2026-08-25T13:00:00Z |"), (
            f"Rendered entry must start with the header line. Got: '{rendered[:60]}...'"
        )

    def test_render_includes_rationale_section(self) -> None:
        """render() must include '**Rationale**:' section with provided rationale."""
        entry = _make_entry(rationale=("No deviation per §11.",))
        rendered = entry.render()
        assert "**Rationale**:" in rendered, "Rendered entry must include '**Rationale**:' section."
        assert "No deviation per §11." in rendered, (
            "Rendered entry must include the rationale text."
        )

    def test_render_includes_observed_metrics_na_when_empty(self) -> None:
        """render() must include 'N/A' in the observed metrics section when no metrics provided."""
        entry = _make_entry(observed_metrics=())
        rendered = entry.render()
        assert "N/A" in rendered, (
            "Rendered entry with no metrics must include 'N/A' in observed metrics section."
        )

    def test_render_includes_metrics_when_provided(self) -> None:
        """render() must include all provided metric strings in the observed metrics section."""
        entry = _make_entry(observed_metrics=("dsr = 0.75", "sharpe = 0.9"))
        rendered = entry.render()
        assert "dsr = 0.75" in rendered, "Rendered entry must include provided metric values."

    def test_render_includes_files_touched(self) -> None:
        """render() must include files_touched entries in the files section."""
        entry = _make_entry(files_touched=("src/main.py (modified)",))
        rendered = entry.render()
        assert "src/main.py (modified)" in rendered, (
            "Rendered entry must include files_touched content."
        )


# ---------------------------------------------------------------------------
# LabNotebook basic operations
# ---------------------------------------------------------------------------


class TestLabNotebookFileNotFound:
    """LabNotebook raises FileNotFoundError when the notebook file is absent."""

    def test_raises_file_not_found_when_notebook_missing(self, tmp_path: Path) -> None:
        """FileNotFoundError must be raised when the notebook file does not exist."""
        missing_path = tmp_path / "does-not-exist.md"
        with pytest.raises(FileNotFoundError, match="not found"):
            LabNotebook(notebook_path=missing_path)


class TestLabNotebookFirstAppend:
    """First append creates the entry with SHA256 comment in the file."""

    def test_first_append_creates_entry_in_file(self, tmp_path: Path) -> None:
        """append() must write the entry header into the notebook file."""
        nb = _make_notebook(tmp_path, content="# Lab Notebook\n\n## Entries\n")
        entry = _make_entry(action="first-entry")
        nb.append(entry)
        content = (tmp_path / "lab-notebook.md").read_text(encoding="utf-8")
        assert "### 2026-08-25T13:00:00Z" in content, (
            "First appended entry header must be present in the notebook file."
        )

    def test_first_append_includes_sha256_comment(self, tmp_path: Path) -> None:
        """append() must write '<!-- entry-sha256:' comment after the entry."""
        nb = _make_notebook(tmp_path, content="# Lab Notebook\n\n## Entries\n")
        entry = _make_entry(action="sha256-test")
        nb.append(entry)
        content = (tmp_path / "lab-notebook.md").read_text(encoding="utf-8")
        assert "<!-- entry-sha256:" in content, (
            "Appended entry must include '<!-- entry-sha256:' comment."
        )

    def test_first_append_returns_correct_sha256(self, tmp_path: Path) -> None:
        """append() must return the SHA256 hex digest of the entry render() text."""
        nb = _make_notebook(tmp_path, content="# Lab Notebook\n\n## Entries\n")
        entry = _make_entry(action="sha256-return-check")
        sha256 = nb.append(entry)
        assert sha256 == hashlib.sha256(entry.render().encode("utf-8")).hexdigest(), (
            f"append() must return SHA256 of the entry render() text. Got '{sha256[:16]}...'."
        )

    def test_sha256_in_file_matches_computed(self, tmp_path: Path) -> None:
        """The SHA256 embedded in the file comment must match the computed hash of the entry."""
        nb = _make_notebook(tmp_path, content="# Lab Notebook\n\n## Entries\n")
        entry = _make_entry(action="sha256-file-verify")
        sha256 = nb.append(entry)
        content = (tmp_path / "lab-notebook.md").read_text(encoding="utf-8")
        assert f"<!-- entry-sha256: {sha256} -->" in content, (
            f"SHA256 comment in file does not match computed hash. "
            f"Expected to find '<!-- entry-sha256: {sha256} -->' in file."
        )


# ---------------------------------------------------------------------------
# Second append and multi-entry chain
# ---------------------------------------------------------------------------


class TestLabNotebookMultiEntry:
    """Multiple appends produce a chain of entries; SHA256 verifiable for each."""

    def test_second_append_adds_entry(self, tmp_path: Path) -> None:
        """Second append must add a second entry without removing the first."""
        nb = _make_notebook(tmp_path, content="# Lab Notebook\n\n## Entries\n")
        entry1 = _make_entry(timestamp="2026-08-25T13:00:00Z", action="first")
        entry2 = _make_entry(timestamp="2026-08-25T14:00:00Z", action="second")
        nb.append(entry1)
        nb.append(entry2)
        content = (tmp_path / "lab-notebook.md").read_text(encoding="utf-8")
        assert "2026-08-25T13:00:00Z" in content, (
            "First entry must still be present after second append."
        )
        assert "2026-08-25T14:00:00Z" in content, (
            "Second entry must be present after second append."
        )

    def test_sha256_chain_verifiable(self, tmp_path: Path) -> None:
        """verify_sha256() must return True for every entry in a multi-entry chain."""
        nb = _make_notebook(tmp_path, content="# Lab Notebook\n\n## Entries\n")
        entries = [
            _make_entry(timestamp=f"2026-08-25T{h:02d}:00:00Z", action=f"entry-{h}")
            for h in range(13, 16)
        ]
        for entry in entries:
            nb.append(entry)

        for entry in entries:
            assert nb.verify_sha256(entry) is True, (
                f"verify_sha256() must return True for entry with action={entry.action!r}. "
                f"SHA256 chain broken."
            )

    def test_last_entry_header_reflects_most_recent(self, tmp_path: Path) -> None:
        """last_entry_header() must return the most recently appended entry's header."""
        nb = _make_notebook(tmp_path, content="# Lab Notebook\n\n## Entries\n")
        entry1 = _make_entry(timestamp="2026-08-25T13:00:00Z", action="alpha")
        entry2 = _make_entry(timestamp="2026-08-25T14:00:00Z", action="beta")
        nb.append(entry1)
        nb.append(entry2)
        last = nb.last_entry_header()
        assert last is not None, "last_entry_header() must not return None after appending entries."
        assert "beta" in last, (
            f"last_entry_header() must return the most recently appended entry's header. "
            f"Expected 'beta' in header, got '{last}'."
        )


# ---------------------------------------------------------------------------
# Append-only invariant
# ---------------------------------------------------------------------------


class TestLabNotebookAppendOnlyInvariant:
    """The append-only invariant prevents overwriting or duplicating existing entries."""

    def test_duplicate_header_raises_append_error(self, tmp_path: Path) -> None:
        """LabNotebookAppendError raised when the same entry is appended twice."""
        nb = _make_notebook(tmp_path, content="# Lab Notebook\n\n## Entries\n")
        entry = _make_entry(action="duplicate-test")
        nb.append(entry)
        with pytest.raises(LabNotebookAppendError, match="append-only"):
            nb.append(entry)

    def test_same_timestamp_same_header_raises(self, tmp_path: Path) -> None:
        """LabNotebookAppendError raised when two entries have identical headers."""
        nb = _make_notebook(tmp_path, content="# Lab Notebook\n\n## Entries\n")
        entry1 = _make_entry(
            timestamp="2026-08-25T13:00:00Z",
            action="same-action",
        )
        entry2 = _make_entry(
            timestamp="2026-08-25T13:00:00Z",
            action="same-action",
        )
        nb.append(entry1)
        with pytest.raises(LabNotebookAppendError):
            nb.append(entry2)

    def test_different_timestamp_does_not_raise(self, tmp_path: Path) -> None:
        """Different timestamp → different header → second append must succeed."""
        nb = _make_notebook(tmp_path, content="# Lab Notebook\n\n## Entries\n")
        entry1 = _make_entry(timestamp="2026-08-25T13:00:00Z", action="run")
        entry2 = _make_entry(timestamp="2026-08-25T14:00:00Z", action="run")
        nb.append(entry1)
        nb.append(entry2)
        content = (tmp_path / "lab-notebook.md").read_text(encoding="utf-8")
        assert content.count("### 2026-08-25") == 2, (
            "Both entries must be present after appending entries with different timestamps."
        )

    def test_existing_content_not_modified(self, tmp_path: Path) -> None:
        """append() must never modify existing content — only append after end of file."""
        original = "# Lab Notebook\n\n## Entries\n\nExisting text here.\n"
        nb = _make_notebook(tmp_path, content=original)
        entry = _make_entry(action="new-entry")
        nb.append(entry)
        content = (tmp_path / "lab-notebook.md").read_text(encoding="utf-8")
        assert content.startswith(original), (
            "Existing notebook content must not be modified by append. "
            "Original content must appear at the start of the file."
        )


# ---------------------------------------------------------------------------
# verify_file(): hashes checkable from the file text (issue #786)
# ---------------------------------------------------------------------------

_REPO_NOTEBOOK = Path(__file__).resolve().parents[4] / "docs/experiments/swing-mvp1/lab-notebook.md"


class TestVerifyFile:
    """Every hashed entry verifies against the notebook's own text."""

    def test_an_appended_entry_verifies_as_written(self, tmp_path: Path) -> None:
        """A new entry with every section hashes exactly as it sits in the file."""
        nb = _make_notebook(tmp_path, content="# Lab Notebook\n")
        entry = _make_entry(
            observed_metrics=("m = 1",),
            decision=("d",),
            rationale=("r",),
            files_touched=("f (added)",),
        )
        nb.append(entry)
        assert nb.verify_file() == {entry.header(): True}

    def test_an_appended_entry_has_a_blank_line_between_each_label_and_its_list(
        self, tmp_path: Path
    ) -> None:
        """Per markdownlint MD032, no bold label line is directly followed by a list item."""
        nb = _make_notebook(tmp_path)
        nb.append(_make_entry(observed_metrics=("m = 1",), decision=("d",), files_touched=("f",)))
        lines = (tmp_path / "lab-notebook.md").read_text(encoding="utf-8").splitlines()
        assert [
            (label, following)
            for label, following in itertools.pairwise(lines)
            if label.startswith("**") and following.startswith("- ")
        ] == []

    def test_an_edited_entry_fails(self, tmp_path: Path) -> None:
        """Changing one word of a hashed entry makes it fail verification."""
        nb = _make_notebook(tmp_path)
        entry = _make_entry(decision=("ship it",))
        nb.append(entry)
        path = tmp_path / "lab-notebook.md"
        path.write_text(
            path.read_text(encoding="utf-8").replace("ship it", "hold it"), encoding="utf-8"
        )
        assert nb.verify_file() == {entry.header(): False}

    def test_the_repo_notebook_verifies_entry_by_entry(self) -> None:
        """The real notebook, including the 2026-10-03 entry with hand-added blank lines."""
        results = LabNotebook(notebook_path=_REPO_NOTEBOOK).verify_file()
        assert (
            any(header.startswith("### 2026-10-03T13:18:31Z") for header in results),
            [header for header, ok in results.items() if not ok],
        ) == (True, [])
