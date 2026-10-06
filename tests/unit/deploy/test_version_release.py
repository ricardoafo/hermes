"""Tests for scripts/deploy/version_release.py (ADR-022 §3).

Protects what CD tags after a deploy:
- A cut version yields its v<release> tag and that version's changelog section.
- The tag targets the commit that first shipped the version, so one version
  names one build, even when the version deployed before CD could tag it.
- A commit is a version's first deploy only until a commit of that version ships,
  which is what DEPLOY_ON=version deploys (ADR-022 §6).
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


def _git(root: Path, *args: str) -> str:
    return subprocess.run(  # noqa: S603 (fixed git argv, no shell)
        ["git", "-C", str(root), *args],  # noqa: S607 (git from PATH)
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _commit(root: Path, version: str, message: str) -> str:
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "hermes"\nversion = "{version}"\n', encoding="utf-8"
    )
    (root / "notes.txt").write_text(message, encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", message)
    return _git(root, "rev-parse", "HEAD")


def _repo(tmp_path: Path, changelog: str = _CHANGELOG) -> Path:
    (tmp_path / "src" / "hermes").mkdir(parents=True)
    shutil.copy(_MAPPING, tmp_path / "src" / "hermes" / "release_version.py")
    (tmp_path / "CHANGELOG.md").write_text(changelog, encoding="utf-8")
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "test@example.invalid")
    _git(tmp_path, "config", "user.name", "test")
    _git(tmp_path, "config", "commit.gpgsign", "false")
    _git(tmp_path, "config", "tag.gpgsign", "false")
    return tmp_path


def _deployed(root: Path, sha: str) -> None:
    _git(root, "tag", f"deploy-{sha[:12]}", sha)


def test_a_cut_shadow_version_is_described_for_tagging(tmp_path: Path) -> None:
    """Package 1.0.0a1 is tagged v1.0.0-shadow.1 with its changelog section as the body."""
    root = _repo(tmp_path)
    _commit(root, "0.0.0", "before")
    cut = _commit(root, "1.0.0a1", "cut")

    assert version_release.describe(root, cut) == {
        "tag": "v1.0.0-shadow.1",
        "name": "hermes v1.0.0-shadow.1",
        "body": "### Added\n\n- First feature.\n",
        "target": cut,
        "first_deploy": True,
    }


def test_the_tag_goes_on_the_first_deployed_commit_of_the_version(tmp_path: Path) -> None:
    """A version that shipped untagged is tagged where it first shipped, not on today's deploy."""
    root = _repo(tmp_path)
    _deployed(root, _commit(root, "0.0.0", "before"))
    cut = _commit(root, "1.0.0a1", "cut")
    _deployed(root, cut)
    _deployed(root, _commit(root, "1.0.0a1", "later fix"))
    now = _commit(root, "1.0.0a1", "deploying now")

    assert version_release.describe(root, now)["target"] == cut


def test_an_undeployed_cut_is_tagged_at_its_first_deploy(tmp_path: Path) -> None:
    """If the cut commit never deployed, the oldest deployed commit after it is the target."""
    root = _repo(tmp_path)
    _commit(root, "0.0.0", "before")
    _commit(root, "1.0.0a1", "cut, never deployed")
    shipped = _commit(root, "1.0.0a1", "first deploy of the version")
    _deployed(root, shipped)
    now = _commit(root, "1.0.0a1", "deploying now")

    assert version_release.describe(root, now)["target"] == shipped


def test_a_deploy_of_an_older_version_is_not_a_target(tmp_path: Path) -> None:
    """Deploy tags from before the cut never decide where the new version's tag goes."""
    root = _repo(tmp_path)
    _deployed(root, _commit(root, "0.0.0", "before"))
    now = _commit(root, "1.0.0a1", "cut, deploying now")

    assert version_release.describe(root, now)["target"] == now


def test_a_version_that_shipped_is_no_longer_a_first_deploy(tmp_path: Path) -> None:
    """Once a commit of the version deployed, later commits of it are not first deploys."""
    root = _repo(tmp_path)
    _deployed(root, _commit(root, "1.0.0a1", "cut"))
    now = _commit(root, "1.0.0a1", "fix merged after the cut")

    assert version_release.describe(root, now)["first_deploy"] is False


def test_a_cut_whose_deploy_failed_is_still_a_first_deploy(tmp_path: Path) -> None:
    """A failed deploy writes no deploy tag, so the next commit of the version still ships."""
    root = _repo(tmp_path)
    _deployed(root, _commit(root, "0.0.0", "before"))
    _commit(root, "1.0.0a1", "cut, deploy rolled back")
    now = _commit(root, "1.0.0a1", "the fix")

    assert version_release.describe(root, now)["first_deploy"] is True


def test_an_existing_version_tag_means_the_version_shipped(tmp_path: Path) -> None:
    """A v<release> tag already in the repository is never deployed over again."""
    root = _repo(tmp_path)
    cut = _commit(root, "1.0.0a1", "cut")
    _git(root, "tag", "v1.0.0-shadow.1", cut)
    now = _commit(root, "1.0.0a1", "later")

    assert version_release.describe(root, now)["first_deploy"] is False


def test_a_new_cut_after_a_shipped_version_is_a_first_deploy(tmp_path: Path) -> None:
    """Cutting the next version makes its commit deployable again."""
    changelog = _CHANGELOG.replace(
        "## [1.0.0-shadow.1]", "## [1.0.0-shadow.2] - 2026-10-20\n\n- Next.\n\n## [1.0.0-shadow.1]"
    )
    root = _repo(tmp_path, changelog)
    _deployed(root, _commit(root, "1.0.0a1", "first cut"))
    _git(root, "tag", "v1.0.0-shadow.1", "HEAD")
    now = _commit(root, "1.0.0a2", "second cut")

    described = version_release.describe(root, now)

    assert (described["tag"], described["first_deploy"]) == ("v1.0.0-shadow.2", True)


def test_an_uncut_version_has_nothing_to_tag(tmp_path: Path) -> None:
    """A version without a dated changelog section, such as 0.0.0, is never tagged."""
    root = _repo(tmp_path)
    head = _commit(root, "0.0.0", "before")

    assert version_release.describe(root, head) == {}


@pytest.mark.parametrize("version", ["1.0.0rc1", "not-a-version"])
def test_a_foreign_version_prints_nothing_to_tag_and_exits_zero(
    tmp_path: Path, version: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """A version the cut never writes is a warning, not a failed deploy."""
    root = _repo(tmp_path)
    head = _commit(root, version, "odd")

    code = version_release.main(["--root", str(root), "--release", head])

    captured = capsys.readouterr()
    assert code == 0
    assert json.loads(captured.out) == {}
    assert "not a hermes package version" in captured.err


def test_an_unknown_release_prints_nothing_to_tag(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A git failure (unknown commit) still exits 0 with nothing to tag."""
    root = _repo(tmp_path)
    _commit(root, "1.0.0a1", "cut")

    assert version_release.main(["--root", str(root), "--release", "f" * 40]) == 0
    assert json.loads(capsys.readouterr().out) == {}


def test_runs_with_the_standard_library_alone(tmp_path: Path) -> None:
    """The notes job runs it with `uv run --no-project`, so it must not need hermes installed."""
    root = _repo(tmp_path)
    cut = _commit(root, "1.0.0a1", "cut")

    result = subprocess.run(  # noqa: S603 — fixed argv; the test's own interpreter
        [sys.executable, "-I", "-S", str(_SCRIPT), "--root", str(root), "--release", cut],
        capture_output=True,
        text=True,
        check=True,
    )

    assert json.loads(result.stdout)["tag"] == "v1.0.0-shadow.1"


def test_the_repository_head_is_a_cut_version_with_a_tag() -> None:
    """Main declares a cut version, so its next deploy has a tag and notes to publish."""
    release_version = version_release._load_release_version(_REPO_ROOT)
    described = version_release.describe(_REPO_ROOT, "HEAD")

    assert described["tag"] == f"v{release_version.to_semver(hermes.__version__)}"
    assert described["body"].startswith("### ")
