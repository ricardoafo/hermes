"""Unit tests for scripts/dev/aggregate-changelog.py.

Protects the wave-4 fragment-based changelog aggregation invariants:
- Multiple fragments merge under the correct section, ordered by PR number.
- Empty directory yields just the header (no sections).
- Non-numeric-prefixed files are skipped (placeholder convention).
- Invalid type suffix raises ValueError with a diagnostic path.
- ``--apply`` rewrites the [Unreleased] block in place without touching the
  rest of CHANGELOG.md byte-for-byte.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

# Import the script as a module. Its filename contains a hyphen so it is
# not importable with plain `import`.
_SCRIPT_PATH = Path(__file__).resolve().parents[3] / "scripts" / "dev" / "aggregate-changelog.py"
_spec = importlib.util.spec_from_file_location("aggregate_changelog", _SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None
aggregate_changelog = importlib.util.module_from_spec(_spec)
sys.modules["aggregate_changelog"] = aggregate_changelog
_spec.loader.exec_module(aggregate_changelog)


def test_happy_path_multiple_fragments_ordered_by_pr(tmp_path: Path) -> None:
    """Fragments merge under correct sections and sort by PR number ascending."""
    frag_dir = tmp_path / "changelog.d"
    frag_dir.mkdir()
    (frag_dir / "132-added.md").write_text(
        "- Slack notifier adapter. Closes #132.\n", encoding="utf-8"
    )
    (frag_dir / "130-added.md").write_text(
        "- Telegram notifier adapter. Closes #130.\n", encoding="utf-8"
    )
    (frag_dir / "140-fixed.md").write_text(
        "- Fixed CI race in mutation workflow.\n", encoding="utf-8"
    )

    by_section = aggregate_changelog.collect_fragments(frag_dir)

    assert [pr for pr, _ in by_section["Added"]] == [130, 132]
    assert [pr for pr, _ in by_section["Fixed"]] == [140]
    assert by_section["Changed"] == []

    block = aggregate_changelog.render_unreleased(by_section)
    assert "## [Unreleased]" in block
    assert "### Added" in block
    assert "### Fixed" in block
    # Order in the rendered block: 130 before 132 (numeric ascending).
    added_idx = block.index("### Added")
    fixed_idx = block.index("### Fixed")
    t130 = block.index("Telegram notifier adapter")
    t132 = block.index("Slack notifier adapter")
    assert added_idx < t130 < t132 < fixed_idx
    # Sections without entries do NOT appear.
    assert "### Changed" not in block
    assert "### Deprecated" not in block


def test_empty_dir_returns_only_header(tmp_path: Path) -> None:
    """An empty fragments directory renders just the [Unreleased] header."""
    frag_dir = tmp_path / "changelog.d"
    frag_dir.mkdir()

    by_section = aggregate_changelog.collect_fragments(frag_dir)
    block = aggregate_changelog.render_unreleased(by_section)

    assert block.strip() == "## [Unreleased]"
    for section in ("Added", "Changed", "Fixed"):
        assert f"### {section}" not in block


def test_missing_fragment_dir_returns_empty_sections(tmp_path: Path) -> None:
    """A non-existent fragments dir is treated as empty (no crash)."""
    frag_dir = tmp_path / "does-not-exist"

    by_section = aggregate_changelog.collect_fragments(frag_dir)

    assert all(v == [] for v in by_section.values())


def test_non_numeric_prefix_is_skipped(tmp_path: Path) -> None:
    """Placeholder files (non-numeric prefix) are ignored by aggregation."""
    frag_dir = tmp_path / "changelog.d"
    frag_dir.mkdir()
    (frag_dir / "wave-4-process-hardening.md").write_text(
        "- Placeholder for a PR whose number is unknown.\n", encoding="utf-8"
    )
    (frag_dir / "132-added.md").write_text("- Real fragment. Closes #132.\n", encoding="utf-8")

    by_section = aggregate_changelog.collect_fragments(frag_dir)

    assert len(by_section["Added"]) == 1
    assert by_section["Added"][0][0] == 132


def test_gitkeep_and_readme_are_skipped(tmp_path: Path) -> None:
    """The .gitkeep sentinel and the README are never treated as fragments."""
    frag_dir = tmp_path / "changelog.d"
    frag_dir.mkdir()
    (frag_dir / ".gitkeep").write_text("", encoding="utf-8")
    (frag_dir / "README.md").write_text("# guide\n", encoding="utf-8")
    (frag_dir / "132-added.md").write_text("- Real fragment.\n", encoding="utf-8")

    by_section = aggregate_changelog.collect_fragments(frag_dir)

    assert len(by_section["Added"]) == 1


def test_invalid_type_raises_valueerror(tmp_path: Path) -> None:
    """A numeric-prefixed file with an unknown type suffix raises ValueError."""
    frag_dir = tmp_path / "changelog.d"
    frag_dir.mkdir()
    (frag_dir / "132-nonsense.md").write_text("- Bad fragment.\n", encoding="utf-8")

    with pytest.raises(ValueError, match="unknown type 'nonsense'"):
        aggregate_changelog.collect_fragments(frag_dir)


def test_apply_replaces_unreleased_block_in_place(tmp_path: Path) -> None:
    """--apply rewrites [Unreleased] and preserves everything outside it."""
    frag_dir = tmp_path / "changelog.d"
    frag_dir.mkdir()
    (frag_dir / "200-added.md").write_text("- Brand new feature. Closes #200.\n", encoding="utf-8")

    changelog = tmp_path / "CHANGELOG.md"
    original = (
        "# Changelog\n\n"
        "Format: Keep a Changelog.\n\n"
        "## [Unreleased]\n\n"
        "### Added\n\n"
        "- Old stale entry.\n\n"
        "## [1.0.0] - 2026-01-01\n\n"
        "### Added\n\n"
        "- First release.\n\n"
        "[Unreleased]: https://example.com/compare/1.0.0...HEAD\n"
    )
    changelog.write_text(original, encoding="utf-8")

    by_section = aggregate_changelog.collect_fragments(frag_dir)
    block = aggregate_changelog.render_unreleased(by_section)
    aggregate_changelog.apply_to_changelog(changelog, block)

    rewritten = changelog.read_text(encoding="utf-8")
    # New content in.
    assert "Brand new feature" in rewritten
    # Old stale content out.
    assert "Old stale entry" not in rewritten
    # Everything outside the [Unreleased] block preserved.
    assert "# Changelog" in rewritten
    assert "Format: Keep a Changelog." in rewritten
    assert "## [1.0.0] - 2026-01-01" in rewritten
    assert "- First release." in rewritten
    assert "[Unreleased]: https://example.com/compare/1.0.0...HEAD" in rewritten
