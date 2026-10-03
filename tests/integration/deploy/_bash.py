"""Shared helpers for the Tier 2 tests that run deploy scripts under bash.

CI runs these tests on Linux and skips them on Windows. A Windows contributor
with Developer Mode on can run them by setting ``HERMES_TEST_BASH`` to the
path of Git Bash's ``bash.exe`` (the ``bash`` on PATH may be the WSL launcher).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

BASH = os.environ.get("HERMES_TEST_BASH") or "bash"
RUN_ON_THIS_PLATFORM = sys.platform != "win32" or bool(os.environ.get("HERMES_TEST_BASH"))
SKIP_REASON = "needs bash, GNU coreutils and real symlinks (set HERMES_TEST_BASH on Windows)"

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT_DIR = REPO_ROOT / "scripts" / "deploy"

# Windows has no POSIX file modes, so tests that assert on them run on Linux only.
NEEDS_POSIX_MODES = pytest.mark.skipif(
    sys.platform == "win32", reason="NTFS does not store POSIX file modes"
)


def posix(path: Path) -> str:
    """Return ``path`` with forward slashes, which bash accepts on every platform."""
    return path.as_posix()


def clean_env(**extra: str) -> dict[str, str]:
    """Return the ambient environment minus hermes, stub and uv variables, plus ``extra``."""
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("HERMES_", "STUB_", "UV_", "GATE_"))
    }
    # Git Bash only creates real symlinks with this; Linux ignores it.
    env["MSYS"] = "winsymlinks:nativestrict"
    env.update(extra)
    return env


def write_stubs(directory: Path, stubs: dict[str, str]) -> None:
    """Write each stub as an executable bash script named after the command it replaces."""
    directory.mkdir(parents=True, exist_ok=True)
    for name, body in stubs.items():
        stub = directory / name
        stub.write_text(body, encoding="utf-8", newline="\n")
        stub.chmod(0o755)


def run_bash(args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Run ``bash <args>`` with ``env`` and capture its output."""
    # S603: args are literals and paths a test built; no untrusted input.
    return subprocess.run(  # noqa: S603
        [BASH, *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def output(result: subprocess.CompletedProcess[str]) -> str:
    """Return stdout and stderr together, for assertion messages."""
    return f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
