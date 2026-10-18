"""Unit tests for scripts/dev/change-type.py (issue #1077, ADR-024 §3).

Invariants under test:
- the engine rule is today's ENGINE_PATHS of the Train replay, so no engine file
  is ever classed milder;
- a file no rule names is engine, so a gap in the list fails safe;
- a diff takes the strictest type of its files.
"""

import importlib.util
import re
import subprocess
from pathlib import Path
from types import ModuleType

import pytest

_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT = _ROOT / "scripts" / "dev" / "change-type.py"


def _script() -> ModuleType:
    """Import the script module, which is not part of a package."""
    spec = importlib.util.spec_from_file_location("change_type_script", _SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("docs/guides/dev-workflow.md", "docs"),
        ("README.md", "docs"),
        ("changelog.d/.gitkeep", "docs"),
        ("src/hermes/release_version.py", "runtime"),
        ("src/hermes/notes.md", "docs"),
        ("src/hermes/adapters/console/ledger_read_model.py", "surface"),
        ("src/hermes/entrypoints/tui_home.py", "surface"),
        ("src/hermes/entrypoints/status_view.py", "surface"),
        ("tests/unit/domain/value_objects/test_money.py", "surface"),
        ("scripts/dev/change-type.py", "surface"),
        (".github/workflows/ci.yml", "surface"),
        ("src/hermes/entrypoints/serve.py", "runtime"),
        ("src/hermes/adapters/telegram_bot/notifier.py", "runtime"),
        ("src/hermes/adapters/ledger/migrations/0001.sql", "runtime"),
        ("src/hermes/infrastructure/env_gate.py", "runtime"),
        ("config/healthchecks.yaml", "runtime"),
        ("deploy/systemd/hermes.service", "runtime"),
        ("scripts/deploy/install.sh", "runtime"),
        (".github/workflows/cd.yml", "runtime"),
        ("tests/e2e/test_serve_cycle.py", "runtime"),
        ("pyproject.toml", "runtime"),
        ("src/hermes/domain/value_objects/money.py", "engine"),
        ("src/hermes/application/order_router/router.py", "engine"),
        ("src/hermes/adapters/fills/orderbook_walk.py", "engine"),
        ("src/hermes/adapters/fees/schedule.py", "engine"),
        ("config/strategies.yaml", "engine"),
        ("docs/experiments/exp-1/fee-history.csv", "engine"),
        ("docs/experiments/exp-1/data-manifest.json", "engine"),
        ("uv.lock", "engine"),
    ],
)
def test_each_path_gets_its_type(path: str, expected: str) -> None:
    """One path per rule lands in the type ADR-024 §3 gives it."""
    assert _script().classify_file(path) == expected


@pytest.mark.parametrize(
    "path",
    ["src/hermes/ports/strategy.py", "unheard-of.bin", "src/hermes/new_package/x.py"],
)
def test_unnamed_file_counts_as_engine(path: str) -> None:
    """A file the list does not name is engine, so a gap fails safe."""
    assert _script().classify_file(path) == "engine"


def test_mixed_diff_takes_the_strictest_type() -> None:
    """Docs, surface and runtime files together make a runtime diff."""
    paths = ["README.md", "src/hermes/entrypoints/tui_home.py", "config/klines.yaml"]
    assert _script().classify(paths) == "runtime"


def test_one_engine_file_makes_the_diff_engine() -> None:
    """A single engine file outranks any number of milder files."""
    paths = ["README.md", "config/klines.yaml", "src/hermes/domain/errors.py"]
    assert _script().classify(paths) == "engine"


def test_empty_diff_is_docs() -> None:
    """A diff with no files needs no more than the docs checks."""
    assert _script().classify([]) == "docs"


def test_engine_rule_matches_the_train_replay_list() -> None:
    """The engine rule is the ENGINE_PATHS the Train replay uses today, character for character."""
    replay = (_ROOT / "scripts" / "dev" / "train-decision-replay.sh").read_text(encoding="utf-8")
    match = re.search(r"^ENGINE_PATHS='([^']+)'", replay, re.MULTILINE)
    assert match is not None
    assert match.group(1) == _script().ENGINE_PATHS


def _run(*args: str, stdin: str = "") -> subprocess.CompletedProcess[str]:
    """Run the script as CI would."""
    return subprocess.run(  # noqa: S603 — fixed argv
        ["python3", str(_SCRIPT), *args],  # noqa: S607
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
    )


def test_cli_prints_the_type_of_stdin_names() -> None:
    """--files reads names from stdin and prints the type alone."""
    result = _run("--files", stdin="README.md\nconfig/klines.yaml\n")
    assert (result.returncode, result.stdout) == (0, "runtime\n")


def test_cli_at_least_exit_codes() -> None:
    """--at-least exits 0 when the type is that strict or stricter, 1 when milder."""
    names = "config/klines.yaml\n"
    assert _run("--files", "--at-least", "runtime", stdin=names).returncode == 0
    assert _run("--files", "--at-least", "engine", stdin=names).returncode == 1


def test_cli_list_prints_only_files_of_that_type() -> None:
    """--list engine prints the engine files, which is what the replay needs."""
    names = "README.md\nsrc/hermes/domain/errors.py\nconfig/klines.yaml\n"
    result = _run("--files", "--list", "engine", stdin=names)
    assert result.stdout == "src/hermes/domain/errors.py\n"


def test_cli_rejects_unknown_type() -> None:
    """A misspelt type is an argument error, never a silent pass."""
    assert _run("--files", "--at-least", "enginee", stdin="a\n").returncode == 2


def test_cli_diffs_two_commits(tmp_path: Path) -> None:
    """With two commits the script classifies `git diff --name-only` of them."""

    def git(*args: str) -> str:
        done = subprocess.run(  # noqa: S603 — fixed argv
            ["git", "-C", str(tmp_path), *args],  # noqa: S607
            capture_output=True,
            text=True,
            check=True,
        )
        return done.stdout.strip()

    git("init", "-q")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    (tmp_path / "a.md").write_text("a", encoding="utf-8")
    git("add", "-A")
    git("commit", "-q", "-m", "one")
    base = git("rev-parse", "HEAD")
    (tmp_path / "uv.lock").write_text("x", encoding="utf-8")
    git("add", "-A")
    git("commit", "-q", "-m", "two")
    done = subprocess.run(  # noqa: S603 — fixed argv
        ["python3", str(_SCRIPT), base, "HEAD"],  # noqa: S607
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert (done.returncode, done.stdout) == (0, "engine\n")


def test_ci_guide_lists_the_four_types() -> None:
    """The guide that explains CI names every change type."""
    guide = (_ROOT / "docs" / "guides" / "ci-runners-and-merge-queue.md").read_text(
        encoding="utf-8"
    )
    section = guide.split("## Change types", 1)[1].split("\n## ", 1)[0]
    named = re.findall(r"^- \*\*(\w+)\*\*:", section, re.MULTILINE)
    assert named == ["Docs", "Surface", "Runtime", "Engine"]
