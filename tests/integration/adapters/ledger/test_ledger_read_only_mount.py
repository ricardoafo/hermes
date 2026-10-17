"""A read-only unit opens the WAL ledger on a read-only mount while serve is stopped (#879).

Production runs the tax pack, the bot and the archive check under
``ProtectSystem=strict``: ``/var/lib/hermes`` is mounted read-only for them, so
opening the ``-shm`` file for writing fails with EROFS for every user, its
owner and root included. This test builds that mount for real: the reader runs
in a private mount namespace (``unshare``) where the ledger directory is
bind-mounted read-only.

Making the mount needs user and mount namespaces. Where the host refuses them
(an unprivileged container), the test skips, and
``tests/unit/adapters/ledger/test_connection.py`` still proves the closest case
CI allows: a directory and files the reader cannot write.
"""

import shutil
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest

from hermes.adapters.ledger.connection import open_ledger

pytestmark = pytest.mark.integration

_READ_AS_A_UNIT = """
import sys
from contextlib import closing
from pathlib import Path
from hermes.adapters.ledger.connection import open_ledger
with closing(open_ledger(Path(sys.argv[1]), read_only=True)) as conn:
    print(conn.execute("SELECT x FROM t").fetchall())
"""


def _in_a_read_only_mount(directory: Path, *command: str) -> subprocess.CompletedProcess[str]:
    """Run ``command`` where ``directory`` is bind-mounted read-only, as on the VPS."""
    unshare = shutil.which("unshare")
    assert unshare is not None
    script = 'mount --bind -o ro "$1" "$1" && shift && exec "$@"'
    return subprocess.run(  # noqa: S603 - fixed tools, paths built by the test
        [
            unshare,
            "--user",
            "--map-root-user",
            "--mount",
            "sh",
            "-c",
            script,
            "sh",
            str(directory),
            *command,
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def _read_only_mounts_work(tmp_path: Path) -> bool:
    if shutil.which("unshare") is None:
        return False
    probe = tmp_path / "probe"
    probe.mkdir()
    touched = _in_a_read_only_mount(probe, "touch", str(probe / "x"))
    return touched.returncode != 0 and "Read-only file system" in touched.stderr


def test_a_unit_on_a_read_only_mount_reads_the_ledger_serve_left(tmp_path: Path) -> None:
    """With serve stopped and the directory mounted read-only, every committed row reads."""
    if not _read_only_mounts_work(tmp_path):
        pytest.skip("this host cannot make a read-only mount namespace (unshare refused)")
    state = tmp_path / "state"
    state.mkdir()
    db = state / "hermes.sqlite3"
    with closing(open_ledger(db)) as serve:
        serve.execute("CREATE TABLE t (x INTEGER)")
        serve.execute("INSERT INTO t VALUES (7)")
        serve.commit()
    read = _in_a_read_only_mount(state, sys.executable, "-c", _READ_AS_A_UNIT, str(db))
    assert (read.returncode, read.stdout, read.stderr) == (0, "[(7,)]\n", "")
