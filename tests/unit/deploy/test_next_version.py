"""Tests for scripts/deploy/next_version.py (ADR-030 §2-§4, ADR-027 §1).

Protects what CD ships and what version it ships as:
- A push that changes nothing the release bundle carries ships nothing and
  keeps the last version (ADR-030 §2).
- A shipping push is MINOR when a fragment adds, changes, deprecates or
  removes something, or an engine file changed; PATCH otherwise (ADR-030 §3).
- The base is the highest release tag merged into the release, never an
  unmerged tag or a retired ``-shadow.N`` one.
- A computed bump never leaves the major version (ADR-027 §3).
- ``--stamp`` writes the version into both version files the wheel reads.
- Under the deploy lock, a number an earlier deploy took is never reused
  (ADR-030 §4).
- The engine list is the one the Train replay uses (ADR-025).
"""

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path
from types import ModuleType

from hypothesis import given
from hypothesis import strategies as st

from tests.support.paths import REPO_ROOT

_SCRIPT = REPO_ROOT / "scripts" / "deploy" / "next_version.py"
_REPLAY = REPO_ROOT / "scripts" / "dev" / "train-decision-replay.sh"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("next_version", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


next_version = _load()


def _git(root: Path, *args: str) -> str:
    return subprocess.run(  # noqa: S603 (fixed git argv, no shell)
        ["git", "-C", str(root), *args],  # noqa: S607 (git from PATH)
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _commit(root: Path, files: dict[str, str], message: str) -> str:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", message)
    return _git(root, "rev-parse", "HEAD")


def _repo(tmp_path: Path, tag: str | None = "v0.2.0") -> Path:
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "ci@example.invalid")
    _git(tmp_path, "config", "user.name", "ci")
    _commit(
        tmp_path,
        {
            "pyproject.toml": '[project]\nname = "hermes"\nversion = "0.2.0"\n',
            "src/hermes/__init__.py": '__version__ = "0.2.0"\n',
            "README.md": "hermes\n",
        },
        "chore(release): cut 0.2.0",
    )
    if tag:
        _git(tmp_path, "tag", tag)
    return tmp_path


def test_a_docs_and_tests_only_push_ships_nothing_and_keeps_the_version(tmp_path: Path) -> None:
    """ADR-030 §2: no bundle file changed, so no deploy and no new number."""
    root = _repo(tmp_path)
    head = _commit(
        root,
        {"docs/guide.md": "x\n", "tests/unit/test_x.py": "x\n", "changelog.d/9-added.md": "- x\n"},
        "docs: guide",
    )

    result = next_version.compute(root, head)

    assert (result["ships"], result["version"], result["bump"]) == (False, "0.2.0", "none")


def test_a_research_batch_file_ships_nothing(tmp_path: Path) -> None:
    """ADR-030 §2: batch and results PRs add files under docs/experiments the bundle omits."""
    root = _repo(tmp_path)
    head = _commit(root, {"docs/experiments/swing-b1/batch.md": "x\n"}, "docs(experiment): batch")

    assert next_version.compute(root, head)["ships"] is False


def test_a_bundle_change_with_no_fragment_is_a_patch(tmp_path: Path) -> None:
    """ADR-030 §3: a chore that touches the bundle and adds no fragment bumps PATCH."""
    root = _repo(tmp_path)
    head = _commit(root, {"deploy/hermes-backup.sh": "#!/bin/sh\n"}, "chore(deploy): backup")

    result = next_version.compute(root, head)

    assert (result["ships"], result["version"], result["bump"]) == (True, "0.2.1", "patch")


def test_fixed_and_security_fragments_outside_the_engine_are_a_patch(tmp_path: Path) -> None:
    """ADR-027 §1: fixes that change no engine decision bump PATCH."""
    root = _repo(tmp_path)
    head = _commit(
        root,
        {
            "src/hermes/entrypoints/status.py": "x = 1\n",
            "changelog.d/10-fixed.md": "- a fix\n",
            "changelog.d/11-security.md": "- a hardening\n",
        },
        "fix(status): a fix",
    )

    assert next_version.compute(root, head)["version"] == "0.2.1"


def test_an_added_fragment_is_a_minor_that_resets_the_patch(tmp_path: Path) -> None:
    """ADR-027 §1: a feature bumps MINOR; 0.2.3 becomes 0.3.0, not 0.3.3."""
    root = _repo(tmp_path, tag="v0.2.3")
    head = _commit(
        root,
        {"src/hermes/entrypoints/status.py": "x = 1\n", "changelog.d/12-added.md": "- new\n"},
        "feat(status): new",
    )

    result = next_version.compute(root, head)

    assert (result["version"], result["bump"], result["minor_fragments"]) == (
        "0.3.0",
        "minor",
        ["changelog.d/12-added.md"],
    )


def test_an_engine_file_makes_a_fix_a_minor(tmp_path: Path) -> None:
    """ADR-030 §3: any engine file changed means MINOR, even with only a fixed fragment."""
    root = _repo(tmp_path)
    head = _commit(
        root,
        {"src/hermes/domain/sizing.py": "x = 1\n", "changelog.d/+sizing.fixed.md": "- fix\n"},
        "fix(domain): sizing",
    )

    result = next_version.compute(root, head)

    assert (result["version"], result["engine_files"]) == (
        "0.3.0",
        ["src/hermes/domain/sizing.py"],
    )


def test_the_base_is_the_highest_merged_release_tag(tmp_path: Path) -> None:
    """A retired -shadow tag and a tag on an unmerged branch never set the base."""
    root = _repo(tmp_path, tag="v0.2.0")
    _git(root, "tag", "v1.0.0-shadow.1")
    _git(root, "checkout", "-q", "-b", "side")
    _commit(root, {"side.txt": "x\n"}, "side")
    _git(root, "tag", "v0.9.0")
    _git(root, "checkout", "-q", "main")
    head = _commit(root, {"config/app.yaml": "a: 1\n"}, "chore(config): a")

    result = next_version.compute(root, head)

    assert (result["previous"], result["version"]) == ("0.2.0", "0.2.1")


def test_with_no_release_tag_the_pyproject_version_ships_as_is(tmp_path: Path) -> None:
    """Bootstrap: before any v tag, the committed version is the release."""
    root = _repo(tmp_path, tag=None)
    head = _git(root, "rev-parse", "HEAD")

    result = next_version.compute(root, head)

    assert (result["ships"], result["version"], result["tag"]) == (True, "0.2.0", "v0.2.0")


def test_stamp_writes_the_version_into_both_version_files(tmp_path: Path) -> None:
    """The wheel's metadata and hermes.__version__ must name the same computed version."""
    root = _repo(tmp_path)

    next_version.stamp(root, "0.4.1")

    assert re.findall(r'"([^"]+)"', (root / "pyproject.toml").read_text(encoding="utf-8")) == [
        "hermes",
        "0.4.1",
    ]
    assert (root / "src/hermes/__init__.py").read_text(
        encoding="utf-8"
    ) == '__version__ = "0.4.1"\n'


def test_cli_prints_json_and_stamps(tmp_path: Path) -> None:
    """CD reads the JSON line and builds from the stamped tree."""
    root = _repo(tmp_path)
    head = _commit(root, {"uv.lock": "lock\n"}, "build(deps): lock")

    out = subprocess.run(  # noqa: S603 (fixed argv)
        [sys.executable, str(_SCRIPT), "--release", head, "--root", str(root), "--stamp"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout

    assert json.loads(out)["version"] == "0.3.0"
    assert (root / "src/hermes/__init__.py").read_text(
        encoding="utf-8"
    ) == '__version__ = "0.3.0"\n'


def test_cli_exits_2_on_an_unknown_commit(tmp_path: Path) -> None:
    """A git failure must fail the CD step loudly, never ship a guessed version."""
    root = _repo(tmp_path)

    done = subprocess.run(  # noqa: S603 (fixed argv)
        [sys.executable, str(_SCRIPT), "--release", "0" * 40, "--root", str(root)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert (done.returncode, done.stdout) == (2, "")


def test_engine_paths_match_the_train_replay_list() -> None:
    """ADR-025 has one engine file list; the version bump must not drift from it."""
    shell = re.search(
        r"^ENGINE_PATHS='(?P<re>[^']+)'$", _REPLAY.read_text(encoding="utf-8"), re.MULTILINE
    )

    assert shell is not None
    assert next_version.ENGINE_PATHS.pattern == shell["re"]


@given(
    st.tuples(st.integers(0, 50), st.integers(0, 50), st.integers(0, 50)),
    st.sampled_from(["minor", "patch"]),
)
def test_a_bump_never_changes_the_major_and_always_moves_up(
    previous: tuple[int, int, int], kind: str
) -> None:
    """ADR-027 §3: only a manual cut leaves 0.y.z; every computed bump is strictly higher."""
    raised = next_version.bump(previous, kind)

    assert raised[0] == previous[0]
    assert raised > previous
    assert raised == (
        (previous[0], previous[1] + 1, 0) if kind == "minor" else (*previous[:2], previous[2] + 1)
    )


def test_set_stamps_the_given_version_without_git(tmp_path: Path) -> None:
    """The wheel build gets the version from the notes job and only stamps it."""
    (tmp_path / "src/hermes").mkdir(parents=True)
    (tmp_path / "pyproject.toml").write_text('version = "0.2.0"\n', encoding="utf-8")
    (tmp_path / "src/hermes/__init__.py").write_text('__version__ = "0.2.0"\n', encoding="utf-8")

    code = next_version.main(["--set", "0.5.2", "--root", str(tmp_path)])

    assert code == 0
    assert (tmp_path / "pyproject.toml").read_text(encoding="utf-8") == 'version = "0.5.2"\n'


def _notes(root: Path, version: str) -> Path:
    built = root / "notes-version.json"
    built.write_text(json.dumps({"version": version}) + "\n", encoding="utf-8")
    return built


def test_settle_keeps_a_version_no_deploy_took(tmp_path: Path) -> None:
    """ADR-030 §4: with no newer tag, the built version holds and nothing is rewritten."""
    root = _repo(tmp_path)
    head = _commit(root, {"uv.lock": "lock\n"}, "build(deps): lock")
    built = _notes(root, "0.3.0")

    result = next_version.settle(root, head, built)

    assert (result["moved"], result["version"]) == (False, "0.3.0")
    assert built.read_text(encoding="utf-8") == '{"version": "0.3.0"}\n'
    assert (root / "pyproject.toml").read_text(encoding="utf-8").count('version = "0.2.0"') == 1


def test_settle_adopts_the_next_number_when_an_earlier_deploy_took_it(tmp_path: Path) -> None:
    """ADR-030 §4: two batches never ship as one version.

    Batch A (an engine lock change) and batch B (a fix) were both computed
    against v0.2.0 as 0.3.0. A deployed first and tagged v0.3.0 on its commit,
    so B, a fix with no engine file since that tag, ships as 0.3.1.
    """
    root = _repo(tmp_path)
    _commit(root, {"uv.lock": "lock\n"}, "build(deps): lock")
    _git(root, "tag", "v0.3.0")
    head = _commit(root, {"src/hermes/cli.py": "fix\n"}, "fix(cli): typo")
    built = _notes(root, "0.3.0")

    result = next_version.settle(root, head, built)

    assert (result["moved"], result["version"], result["bump"]) == (True, "0.3.1", "patch")
    assert json.loads(built.read_text(encoding="utf-8"))["version"] == "0.3.1"
    assert (root / "src/hermes/__init__.py").read_text(
        encoding="utf-8"
    ) == '__version__ = "0.3.1"\n'


def test_cli_settle_prints_moved_for_the_workflow(tmp_path: Path) -> None:
    """The deploy job rebuilds only on ``"moved": true`` from this line."""
    root = _repo(tmp_path)
    _commit(root, {"uv.lock": "lock\n"}, "build(deps): lock")
    _git(root, "tag", "v0.3.0")
    head = _commit(root, {"src/hermes/cli.py": "fix\n"}, "fix(cli): typo")
    built = _notes(root, "0.3.0")

    out = subprocess.run(  # noqa: S603 (fixed argv)
        [
            sys.executable,
            str(_SCRIPT),
            "--release",
            head,
            "--root",
            str(root),
            "--settle",
            str(built),
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout

    assert (json.loads(out)["moved"], json.loads(out)["version"]) == (True, "0.3.1")
