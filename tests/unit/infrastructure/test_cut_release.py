"""Unit and property tests for scripts/dev/cut-release.py (ADR-022 §2, §7; ADR-027).

Protects the release-cut invariants:
- A cut refuses malformed, equal or older versions and mismatched version files.
- A cut refuses the retired ``-shadow.N`` line; the last shadow counts as 0.1.0.
- An engine change needs a MINOR bump; leaving 0.y.z needs --real-capital.
- Compiling keeps every hand-written ``[Unreleased]`` entry and every fragment.
- The cut leaves an empty ``[Unreleased]`` and deletes only compiled fragments.
- The PR body carries the Engine change line the PR body lint enforces.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hermes.release_version import to_package_version, to_semver

_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT_PATH = _ROOT / "scripts" / "dev" / "cut-release.py"
_spec = importlib.util.spec_from_file_location("cut_release", _SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None
cut_release = importlib.util.module_from_spec(_spec)
sys.modules["cut_release"] = cut_release
_spec.loader.exec_module(cut_release)

_CHANGELOG = """# Changelog

Intro text.

## [Unreleased]

### Added

- Hand-written feature.

### Notes

- A hand-written note.

[Unreleased]: https://github.com/ricardoafo/hermes/commits/main
"""


def _repo(tmp_path: Path, version: str = "0.0.0") -> Path:
    (tmp_path / "src" / "hermes").mkdir(parents=True)
    (tmp_path / "pyproject.toml").write_text(
        f'[project]\nname = "hermes"\nversion = "{version}"\n', encoding="utf-8"
    )
    (tmp_path / "src" / "hermes" / "__init__.py").write_text(
        f'__version__ = "{version}"\n', encoding="utf-8"
    )
    (tmp_path / "CHANGELOG.md").write_text(_CHANGELOG, encoding="utf-8")
    frags = tmp_path / "changelog.d"
    frags.mkdir()
    (frags / "20-added.md").write_text("- Fragment feature twenty.\n", encoding="utf-8")
    (frags / "10-fixed.md").write_text("- Fragment fix ten.\n", encoding="utf-8")
    (frags / "README.md").write_text("# Fragments\n", encoding="utf-8")
    (frags / "wave-old.md").write_text("Legacy note.\n", encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize(
    ("current", "new"),
    [("0.2.0", "0.2.0"), ("0.3.0", "0.2.9"), ("1.0.0a1", "0.1.0"), ("1.0.0", "0.9.0")],
)
def test_a_version_that_does_not_move_forward_is_refused(current: str, new: str) -> None:
    """A cut never repeats or rewinds the version."""
    with pytest.raises(ValueError, match="not greater"):
        cut_release.check_newer(current, new)


@given(st.integers(1, 50), st.integers(1, 30), st.integers(0, 30))
def test_any_zero_version_above_0_1_0_follows_the_retired_shadow(
    shadow: int, minor: int, patch: int
) -> None:
    """From any retired ``1.0.0aN``, every 0.y.z above 0.1.0 counts as newer (ADR-027 §2)."""
    new = f"0.{minor}.{patch}"
    if new == "0.1.0":
        return

    cut_release.check_newer(f"1.0.0a{shadow}", new)


def test_a_retired_shadow_name_is_refused() -> None:
    """No new -shadow.N release is cut (ADR-027 §1)."""
    with pytest.raises(ValueError, match=r"retired -shadow\.N"):
        cut_release.check_release_name("1.0.0-shadow.2")


@pytest.mark.parametrize(("current", "new"), [("0.2.0", "0.2.1"), ("1.0.0a1", "0.1.5")])
def test_an_engine_change_on_a_patch_bump_is_refused(current: str, new: str) -> None:
    """A decision-changing engine change needs a MINOR cut (ADR-027 §1)."""
    with pytest.raises(ValueError, match="needs a MINOR cut"):
        cut_release.check_bump(current, new, engine_change="yes", real_capital=False)


@pytest.mark.parametrize(("current", "new"), [("0.2.0", "0.3.0"), ("1.0.0a1", "0.2.0")])
def test_an_engine_change_on_a_minor_bump_is_allowed(current: str, new: str) -> None:
    """A MINOR bump may carry an engine change."""
    cut_release.check_bump(current, new, engine_change="yes", real_capital=False)


def test_a_patch_bump_without_engine_change_is_allowed() -> None:
    """A fix-only PATCH cut passes."""
    cut_release.check_bump("0.2.0", "0.2.1", engine_change="no", real_capital=False)


@pytest.mark.parametrize("current", ["0.9.3", "1.0.0a1"])
def test_leaving_zero_major_without_real_capital_is_refused(current: str) -> None:
    """1.0.0 is cut only for the first real-capital release (ADR-027 §3)."""
    with pytest.raises(ValueError, match="--real-capital"):
        cut_release.check_bump(current, "1.0.0", engine_change="no", real_capital=False)


def test_leaving_zero_major_with_real_capital_is_allowed() -> None:
    """--real-capital lets the first 1.x cut through."""
    cut_release.check_bump("0.9.3", "1.0.0", engine_change="no", real_capital=True)


def test_bumps_after_one_need_no_real_capital_flag() -> None:
    """Only the step out of 0.y.z needs the flag."""
    cut_release.check_bump("1.0.0", "1.1.0", engine_change="yes", real_capital=False)


def test_mismatched_version_files_are_refused() -> None:
    """pyproject.toml and __version__ must agree before a cut."""
    with pytest.raises(ValueError, match=r"__version__ says '0\.1\.0'"):
        cut_release.read_current_version('version = "0.0.0"\n', '__version__ = "0.1.0"\n')


def test_the_repository_version_files_agree() -> None:
    """The checked-in pyproject.toml and src/hermes/__init__.py declare one version."""
    version = cut_release.read_current_version(
        (_ROOT / "pyproject.toml").read_text(encoding="utf-8"),
        (_ROOT / "src" / "hermes" / "__init__.py").read_text(encoding="utf-8"),
    )

    assert version == to_package_version(to_semver(version))


def test_compiling_keeps_hand_written_entries_and_fragments() -> None:
    """The new section holds the legacy entries first, then fragments, in Keep-a-Changelog order."""
    fragments = {
        "Added": [(20, "- Fragment feature twenty.\n")],
        "Fixed": [(10, "- Fragment fix ten.\n")],
    }

    changelog, section = cut_release.cut_changelog(
        _CHANGELOG, "1.0.0-shadow.1", "2026-10-06", fragments
    )

    assert section == (
        "## [1.0.0-shadow.1] - 2026-10-06\n\n"
        "### Added\n\n- Hand-written feature.\n\n- Fragment feature twenty.\n\n"
        "### Fixed\n\n- Fragment fix ten.\n\n"
        "### Notes\n\n- A hand-written note.\n"
    )
    assert changelog == (
        "# Changelog\n\nIntro text.\n\n## [Unreleased]\n\n" + section + "\n"
        "[Unreleased]: https://github.com/ricardoafo/hermes/commits/main\n"
        "[1.0.0-shadow.1]: https://github.com/ricardoafo/hermes/releases/tag/v1.0.0-shadow.1\n"
    )


def test_a_second_cut_keeps_the_earlier_release_section() -> None:
    """Cutting again leaves earlier dated sections and link references untouched."""
    first, _ = cut_release.cut_changelog(_CHANGELOG, "1.0.0-shadow.1", "2026-10-06", {})
    with_new = first.replace(
        "## [Unreleased]\n", "## [Unreleased]\n\n### Fixed\n\n- Later fix.\n", 1
    )

    second, section = cut_release.cut_changelog(with_new, "1.0.0-shadow.2", "2026-10-20", {})

    assert section == "## [1.0.0-shadow.2] - 2026-10-20\n\n### Fixed\n\n- Later fix.\n"
    assert second.index("## [1.0.0-shadow.2]") < second.index("## [1.0.0-shadow.1]")
    assert second.count("[1.0.0-shadow.1]: ") == 1
    assert second.count("[1.0.0-shadow.2]: ") == 1


def test_compiling_the_real_changelog_loses_no_entry() -> None:
    """A dry cut of this repository keeps every bullet of [Unreleased] plus one per fragment."""
    changelog = (_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    fragments = cut_release.aggregate_changelog.collect_fragments(
        _ROOT / "changelog.d", resolve_pr=lambda _: None
    )
    _, legacy, _ = cut_release._split_unreleased(changelog)

    _, section = cut_release.cut_changelog(changelog, "9.9.9", "2026-10-06", fragments)

    def bullets(text: str) -> int:
        return len(re.findall(r"^- ", text, re.MULTILINE))

    fragment_bullets = sum(bullets(body) for entries in fragments.values() for _, body in entries)
    assert bullets(section) == bullets(legacy) + fragment_bullets


def test_cut_updates_versions_changelog_and_fragments(tmp_path: Path) -> None:
    """A cut sets both versions, compiles the changelog and deletes only compiled fragments."""
    root = _repo(tmp_path)

    code = cut_release.main(
        [
            "0.2.0",
            "--root",
            str(root),
            "--date",
            "2026-10-06",
            "--no-lock",
            "--body-out",
            str(tmp_path / "body.md"),
        ]
    )

    assert code == 0
    assert 'version = "0.2.0"' in (root / "pyproject.toml").read_text(encoding="utf-8")
    assert (root / "src" / "hermes" / "__init__.py").read_text(
        encoding="utf-8"
    ) == '__version__ = "0.2.0"\n'
    assert "## [Unreleased]\n\n## [0.2.0] - 2026-10-06\n" in (root / "CHANGELOG.md").read_text(
        encoding="utf-8"
    )
    assert sorted(p.name for p in (root / "changelog.d").iterdir()) == ["README.md", "wave-old.md"]


def test_cut_writes_a_pr_body_whose_engine_line_must_be_filled(tmp_path: Path) -> None:
    """Without --engine-change the body's Engine change line is a placeholder, not yes or no."""
    root = _repo(tmp_path)

    cut_release.main(
        [
            "0.2.0",
            "--root",
            str(root),
            "--date",
            "2026-10-06",
            "--no-lock",
            "--body-out",
            str(tmp_path / "body.md"),
        ]
    )
    body = (tmp_path / "body.md").read_text(encoding="utf-8")

    assert "## Release\n\nEngine change: <yes or no" in body
    assert "#### Added\n\n- Hand-written feature.\n\n- Fragment feature twenty." in body


def test_engine_change_answer_lands_in_the_pr_body(tmp_path: Path) -> None:
    """--engine-change no writes the answered line the lint accepts."""
    root = _repo(tmp_path)

    cut_release.main(
        [
            "0.2.0",
            "--root",
            str(root),
            "--date",
            "2026-10-06",
            "--no-lock",
            "--body-out",
            str(tmp_path / "body.md"),
            "--engine-change",
            "no",
        ]
    )

    assert "\nEngine change: no\n" in (tmp_path / "body.md").read_text(encoding="utf-8")


def test_a_refused_cut_changes_nothing(tmp_path: Path) -> None:
    """A version that does not move forward exits 2 and leaves every file as it was."""
    root = _repo(tmp_path, version="0.2.0")
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}

    code = cut_release.main(
        [
            "0.2.0",
            "--root",
            str(root),
            "--no-lock",
            "--body-out",
            str(tmp_path / "body.md"),
        ]
    )

    assert code == 2
    assert {p: p.read_bytes() for p in root.rglob("*") if p.is_file()} == before


def test_dry_run_changes_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """--dry-run prints the new section and writes no file."""
    root = _repo(tmp_path)
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}

    code = cut_release.main(["0.2.0", "--root", str(root), "--date", "2026-10-06", "--dry-run"])

    assert code == 0
    assert capsys.readouterr().out.startswith("## [0.2.0] - 2026-10-06\n")
    assert {p: p.read_bytes() for p in root.rglob("*") if p.is_file()} == before


def test_the_first_zero_cut_after_the_retired_shadow_succeeds(tmp_path: Path) -> None:
    """From package 1.0.0a1 the next cut, 0.2.0, sets both version files (ADR-027 §2)."""
    root = _repo(tmp_path, version="1.0.0a1")

    code = cut_release.main(
        ["0.2.0", "--root", str(root), "--no-lock", "--body-out", str(tmp_path / "body.md")]
    )

    assert code == 0
    assert (root / "src" / "hermes" / "__init__.py").read_text(
        encoding="utf-8"
    ) == '__version__ = "0.2.0"\n'


@pytest.mark.parametrize(
    "argv",
    [
        ["1.0.0-shadow.2"],
        ["0.2.1", "--engine-change", "yes"],
        ["1.0.0"],
    ],
)
def test_a_cut_adr_027_refuses_changes_nothing(tmp_path: Path, argv: list[str]) -> None:
    """A retired name, an engine change on a PATCH bump or 1.0.0 without the flag exits 2."""
    root = _repo(tmp_path, version="0.2.0")
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}

    code = cut_release.main(
        [*argv, "--root", str(root), "--no-lock", "--body-out", str(tmp_path / "body.md")]
    )

    assert code == 2
    assert {p: p.read_bytes() for p in root.rglob("*") if p.is_file()} == before
