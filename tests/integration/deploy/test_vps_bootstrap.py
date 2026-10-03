"""Tier 2 invariants for the re-run safety of ``scripts/deploy/vps_bootstrap.sh``.

The script uses absolute paths (``/etc``, ``/opt``, ``/mnt``) and arrives on
the VPS over stdin, so it cannot run whole in a scratch root. Two of its steps
broke or could break a re-dispatch, and both live in functions that these
tests extract from the script text and run under bash with a stub ``chown``,
``mountpoint`` and ``install``:

- ``prepare_mount_point``: run 37070991389 failed because ``install -d -o``
  ran on an already-mounted SSHFS point, where chown fails even as root.
- ``ensure_env_file``: the env file is written once; an existing file keeps
  its content and mtime and only has its owner and mode repaired.

Everything else in the script is covered by the operator acceptance run
(openspec/changes/179-cd-first-real-deploy/change.md, R-5).
"""

from __future__ import annotations

import os
import re
import stat
import subprocess
from pathlib import Path

import pytest

from tests.integration.deploy._bash import (
    NEEDS_POSIX_MODES,
    RUN_ON_THIS_PLATFORM,
    SCRIPT_DIR,
    SKIP_REASON,
    clean_env,
    output,
    posix,
    run_bash,
    write_stubs,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not RUN_ON_THIS_PLATFORM, reason=SKIP_REASON),
]

_SCRIPT = SCRIPT_DIR / "vps_bootstrap.sh"
_MOUNT_DIR = "/mnt/storage-box"

# chown to root needs root, which CI is not; the stub records the call instead.
_STUB_CHOWN = """\
#!/usr/bin/env bash
echo "chown $*" >> "$STUB_LOG"
"""

_STUB_MOUNTPOINT = """\
#!/usr/bin/env bash
echo "mountpoint $*" >> "$STUB_LOG"
[ -n "${STUB_MOUNTED:-}" ]
"""

# A live SSHFS mount refuses chown even for root; the stub reproduces that.
_STUB_INSTALL = """\
#!/usr/bin/env bash
echo "install $*" >> "$STUB_LOG"
if [ -n "${STUB_MOUNTED:-}" ]; then
    echo "install: cannot change owner and permissions of '${*: -1}': Operation not permitted" >&2
    exit 1
fi
"""


def _function(name: str) -> str:
    """Return the source of the top-level bash function ``name`` from the script."""
    text = _SCRIPT.read_text(encoding="utf-8")
    match = re.search(rf"^{name}\(\) \{{\n.*?^\}}\n", text, re.DOTALL | re.MULTILINE)
    assert match is not None, f"function {name} not found in {_SCRIPT.name}"
    return match.group(0)


class Harness:
    """A scratch directory, stub binaries and a call log for one function call."""

    def __init__(self, base: Path) -> None:
        """Create the stub directory under ``base``."""
        self.base = base
        self.log = base / "calls.log"
        write_stubs(
            base / "stubs",
            {"chown": _STUB_CHOWN, "mountpoint": _STUB_MOUNTPOINT, "install": _STUB_INSTALL},
        )

    def calls(self) -> list[str]:
        """Return every logged stub call, oldest first."""
        return self.log.read_text(encoding="utf-8").splitlines() if self.log.exists() else []

    def call(self, function: str, argument: str, **extra: str) -> subprocess.CompletedProcess[str]:
        """Run ``function argument`` after defining it, with ``extra`` in the environment."""
        script = f'set -euo pipefail\nenv_tmp=""\n{_function(function)}\n{function} "$1"\n'
        env = clean_env(
            PATH=f"{posix(self.base / 'stubs')}{os.pathsep}{os.environ['PATH']}",
            STUB_LOG=posix(self.log),
            **extra,
        )
        # A file, not `bash -c`: Windows argv quoting would mangle the backslashes in the sed call.
        script_file = self.base / f"{function}.sh"
        script_file.write_text(script, encoding="utf-8", newline="\n")
        return run_bash([posix(script_file), argument], env)


@pytest.fixture
def harness(tmp_path: Path) -> Harness:
    """Return a harness with the stubs installed and an empty call log."""
    return Harness(tmp_path)


def test_mounted_mount_point_is_left_untouched(harness: Harness) -> None:
    """Invariant (A-5.3): an active mount is never chowned, so a re-dispatch exits 0."""
    result = harness.call("prepare_mount_point", _MOUNT_DIR, STUB_MOUNTED="1")

    assert result.returncode == 0, output(result)
    assert [call for call in harness.calls() if call.startswith("install")] == []


def test_unmounted_mount_point_is_prepared_for_hermes(harness: Harness) -> None:
    """Invariant: with nothing mounted, the mount point is created owned by hermes, mode 0755."""
    result = harness.call("prepare_mount_point", _MOUNT_DIR)

    assert result.returncode == 0, output(result)
    assert [call for call in harness.calls() if call.startswith("install")] == [
        f"install -d -m 0755 -o hermes -g hermes {_MOUNT_DIR}"
    ]


_HAND_EDITED = (
    'HERMES_ENV="prod"\nHERMES_ALLOW_MAINNET="no"\nHERMES_TELEGRAM__CHAT_ID="hand-fixed"\n'
)


def _env_file(tmp_path: Path, content: str = _HAND_EDITED) -> Path:
    """Write the env file with a fixed old mtime and return its path."""
    path = tmp_path / "env.production"
    path.write_text(content, encoding="utf-8", newline="\n")
    os.utime(path, (1_700_000_000, 1_700_000_000))
    return path


def test_existing_env_file_keeps_its_content(harness: Harness, tmp_path: Path) -> None:
    """Invariant (D-8): a re-dispatch never rewrites an existing env file, even with new secrets."""
    path = _env_file(tmp_path)

    result = harness.call("ensure_env_file", posix(path), HERMES_ENV="dev", HERMES_NEW__VALUE="x")

    assert result.returncode == 0, output(result)
    assert path.read_text(encoding="utf-8") == _HAND_EDITED


def test_existing_env_file_keeps_its_mtime(harness: Harness, tmp_path: Path) -> None:
    """Invariant (A-5.2): a second dispatch leaves the env file's modification time unchanged."""
    path = _env_file(tmp_path)

    harness.call("ensure_env_file", posix(path), HERMES_ENV="prod")

    assert path.stat().st_mtime_ns == 1_700_000_000 * 1_000_000_000


def test_existing_env_file_owner_is_repaired_to_root(harness: Harness, tmp_path: Path) -> None:
    """Invariant (R-5): a file left `hermes:hermes` by the old script is chowned to root:root."""
    path = _env_file(tmp_path)

    harness.call("ensure_env_file", posix(path))

    assert f"chown root:root {posix(path)}" in harness.calls()


@NEEDS_POSIX_MODES
def test_read_only_env_file_mode_is_repaired_to_0600(harness: Harness, tmp_path: Path) -> None:
    """Invariant (R-5): the 0400 file the old script wrote ends up 0600, content intact."""
    path = _env_file(tmp_path)
    path.chmod(0o400)

    result = harness.call("ensure_env_file", posix(path))

    assert result.returncode == 0, output(result)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert path.read_text(encoding="utf-8") == _HAND_EDITED


def test_missing_env_file_is_rendered_from_the_environment(
    harness: Harness, tmp_path: Path
) -> None:
    """Invariant: an absent file is rendered sorted and quoted, skipping empty values."""
    path = tmp_path / "env.production"

    result = harness.call(
        "ensure_env_file",
        posix(path),
        HERMES_ENV="prod",
        HERMES_ALLOW_MAINNET="no",
        HERMES_SLACK__BOT_TOKEN="",
        HERMES_X__Y='a"b\\c',
    )

    assert result.returncode == 0, output(result)
    assert path.read_text(encoding="utf-8") == (
        'HERMES_ALLOW_MAINNET="no"\nHERMES_ENV="prod"\nHERMES_X__Y="a\\"b\\\\c"\n'
    )


@NEEDS_POSIX_MODES
def test_rendered_env_file_is_mode_0600(harness: Harness, tmp_path: Path) -> None:
    """Invariant: secrets are never world-readable, not even for an instant (mktemp is 0600)."""
    path = tmp_path / "env.production"

    harness.call("ensure_env_file", posix(path), HERMES_ENV="prod")

    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_rendered_env_file_is_chowned_to_root(harness: Harness, tmp_path: Path) -> None:
    """Invariant (D-2): the rendered file is chowned to root:root, never left hermes-owned."""
    path = tmp_path / "env.production"

    harness.call("ensure_env_file", posix(path), HERMES_ENV="prod")

    chowns = [call for call in harness.calls() if call.startswith("chown")]
    assert len(chowns) == 1
    assert chowns[0].startswith(f"chown root:root {tmp_path.as_posix()}/.env.production.")


def test_render_leaves_no_temp_file_behind(harness: Harness, tmp_path: Path) -> None:
    """Invariant: after a render only the env file remains in the directory."""
    path = tmp_path / "env.production"

    harness.call("ensure_env_file", posix(path), HERMES_ENV="prod")

    leftovers = sorted(
        p.name
        for p in tmp_path.iterdir()
        if p.name not in {"stubs", "calls.log", "ensure_env_file.sh", "prepare_mount_point.sh"}
    )
    assert leftovers == ["env.production"]


def test_no_hermes_vars_creates_no_env_file(harness: Harness, tmp_path: Path) -> None:
    """Invariant (D-8): without any HERMES_* value no empty file is written."""
    path = tmp_path / "env.production"

    result = harness.call("ensure_env_file", posix(path), HERMES_SLACK__BOT_TOKEN="")

    assert result.returncode == 0, output(result)
    assert path.exists() is False


def test_no_hermes_vars_leaves_no_temp_file_behind(harness: Harness, tmp_path: Path) -> None:
    """Invariant: the aborted render removes its hidden temp file."""
    path = tmp_path / "env.production"

    harness.call("ensure_env_file", posix(path))

    leftovers = sorted(
        p.name
        for p in tmp_path.iterdir()
        if p.name not in {"stubs", "calls.log", "ensure_env_file.sh", "prepare_mount_point.sh"}
    )
    assert leftovers == []
