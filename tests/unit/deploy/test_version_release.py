"""Tests for scripts/deploy/version_release.py (ADR-022 §3).

Protects what CD tags after a deploy:
- A cut version yields its v<release> tag and that version's changelog section.
- An uncut version yields nothing to tag.
- Any problem yields nothing to tag and exit 0, so a deploy never fails on it.
- The script runs with the standard library alone, before ``uv sync``.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

import hermes

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT = _REPO_ROOT / "scripts" / "deploy" / "version_release.py"
_MAPPING = _REPO_ROOT / "src" / "hermes" / "release_version.py"

_CHANGELOG = """# Changelog

## [Unreleased]

## [1.0.0-shadow.1] - 2026-10-06

### Added

- First feature.

[Unreleased]: https://github.com/ricardoafo/hermes/commits/main
"""


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("version_release", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


version_release = _load()


def _checkout(tmp_path: Path, version: str, changelog: str = _CHANGELOG) -> Path:
    (tmp_path / "src" / "hermes").mkdir(parents=True)
    shutil.copy(_MAPPING, tmp_path / "src" / "hermes" / "release_version.py")
    (tmp_path / "pyproject.toml").write_text(
        f'[project]\nname = "hermes"\nversion = "{version}"\n', encoding="utf-8"
    )
    (tmp_path / "CHANGELOG.md").write_text(changelog, encoding="utf-8")
    return tmp_path


def test_a_cut_shadow_version_is_described_for_tagging(tmp_path: Path) -> None:
    """Package 1.0.0a1 is tagged v1.0.0-shadow.1 with its changelog section as the body."""
    root = _checkout(tmp_path, "1.0.0a1")

    assert version_release.describe(root) == {
        "tag": "v1.0.0-shadow.1",
        "name": "hermes v1.0.0-shadow.1",
        "body": "### Added\n\n- First feature.\n",
    }


def test_an_uncut_version_has_nothing_to_tag(tmp_path: Path) -> None:
    """A version without a dated changelog section, such as 0.0.0, is never tagged."""
    assert version_release.describe(_checkout(tmp_path, "0.0.0")) == {}


@pytest.mark.parametrize("version", ["1.0.0rc1", "not-a-version"])
def test_a_foreign_version_prints_nothing_to_tag_and_exits_zero(
    tmp_path: Path, version: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """A version the cut never writes is a warning, not a failed deploy."""
    root = _checkout(tmp_path, version)

    code = version_release.main(["--root", str(root)])

    captured = capsys.readouterr()
    assert code == 0
    assert json.loads(captured.out) == {}
    assert "not a hermes package version" in captured.err


def test_a_missing_changelog_prints_nothing_to_tag(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A checkout without CHANGELOG.md still exits 0 with nothing to tag."""
    root = _checkout(tmp_path, "1.0.0a1")
    (root / "CHANGELOG.md").unlink()

    assert version_release.main(["--root", str(root)]) == 0
    assert json.loads(capsys.readouterr().out) == {}


def test_runs_with_the_standard_library_alone(tmp_path: Path) -> None:
    """The notes job runs it with `uv run --no-project`, so it must not need hermes installed."""
    root = _checkout(tmp_path, "1.0.0a1")

    result = subprocess.run(  # noqa: S603 — fixed argv; the test's own interpreter
        [sys.executable, "-I", "-S", str(_SCRIPT), "--root", str(root)],
        capture_output=True,
        text=True,
        check=True,
    )

    assert json.loads(result.stdout)["tag"] == "v1.0.0-shadow.1"


def test_the_repository_head_is_a_cut_version_with_a_tag() -> None:
    """Main declares a cut version, so its next deploy has a tag and notes to publish."""
    release_version = version_release._load_release_version(_REPO_ROOT)
    described = version_release.describe(_REPO_ROOT)

    assert described["tag"] == f"v{release_version.to_semver(hermes.__version__)}"
    assert described["body"].startswith("### ")
