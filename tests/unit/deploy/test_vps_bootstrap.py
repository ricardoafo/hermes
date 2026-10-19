"""Re-run safety of the storage-box mount-point step in ``vps_bootstrap.sh``.

The bootstrap is re-run against a VPS whose sshfs mount is already live; FUSE
rejects ``chown``/``chmod`` on the mount root, so ``install -d -o hermes`` on
``/mnt/storage-box`` fails the whole run. These tests cut the mount-point guard
out of the real script and run it under bash with ``mountpoint`` and
``install`` replaced by recorders (both need a real FUSE mount and root).
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.support.paths import REPO_ROOT

_SCRIPT = REPO_ROOT / "scripts" / "deploy" / "vps_bootstrap.sh"
_BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(_BASH is None, reason="the bootstrap script needs bash")

_GUARD_START = "# Ensure mount point + sshfs binary."
_GUARD_END = "if ! command -v sshfs"

_STUB_MOUNTPOINT = """#!/usr/bin/env bash
exit "$(cat "$(dirname "$0")/mountpoint.rc")"
"""

_STUB_INSTALL = """#!/usr/bin/env bash
echo "$*" >> "$(dirname "$0")/install.log"
"""


def _mount_point_guard() -> str:
    text = _SCRIPT.read_text(encoding="utf-8")
    start = text.index(_GUARD_START)
    end = text.index(_GUARD_END, start)
    # The guard lives in `prepare_mount_point`; the step calls it, so define it first.
    func_start = text.index("prepare_mount_point() {")
    func_end = text.index("\n}\n", func_start) + 3
    return text[func_start:func_end] + text[start:end]


def _run_guard(tmp_path: Path, *, mounted: bool) -> tuple[subprocess.CompletedProcess[str], str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("mountpoint", _STUB_MOUNTPOINT), ("install", _STUB_INSTALL)):
        stub = bin_dir / name
        stub.write_text(body, encoding="utf-8", newline="\n")
        stub.chmod(0o755)
    (bin_dir / "mountpoint.rc").write_text("0" if mounted else "1", encoding="utf-8")
    snippet = tmp_path / "guard.sh"
    snippet.write_text("set -euo pipefail\n" + _mount_point_guard(), encoding="utf-8", newline="\n")

    env = {**os.environ, "PATH": f"{bin_dir.as_posix()}{os.pathsep}{os.environ.get('PATH', '')}"}
    assert _BASH is not None
    result = subprocess.run(  # noqa: S603 - fixed argv, test-owned script
        [_BASH, snippet.as_posix()], capture_output=True, text=True, env=env, check=False
    )
    log = bin_dir / "install.log"
    return result, log.read_text(encoding="utf-8") if log.exists() else ""


def test_live_mount_skips_mount_point_install(tmp_path: Path) -> None:
    """With the storage box already mounted, the step never chowns the mount root."""
    result, install_calls = _run_guard(tmp_path, mounted=True)

    assert result.returncode == 0, result.stderr
    assert install_calls == ""


def test_unmounted_creates_mount_point_owned_by_hermes(tmp_path: Path) -> None:
    """On a fresh box the mount point is still created 0755 hermes:hermes."""
    result, install_calls = _run_guard(tmp_path, mounted=False)

    assert result.returncode == 0, result.stderr
    assert install_calls == "-d -m 0755 -o hermes -g hermes /mnt/storage-box\n"
