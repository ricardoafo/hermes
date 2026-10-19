"""Append-only archives and the restore drill in ``deploy/hermes-backup.sh`` (#775).

Invariants:

- the nightly run only adds an archive: it never prunes, compacts or
  deletes, since the VPS key is append-only and pruning belongs to a
  trusted machine;
- every archive holds the SQLite snapshots at ``sqlite/<name>``, whatever
  the run's temp dir was, beside the L2 Parquet;
- borg connects with the backup's own key and no other;
- ``--restore-drill`` restores the newest archive's snapshots, passes when
  each one is intact and every table still exists live, and prints each
  table's restored and live row counts;
- the drill fails when there is no archive, the archive holds no snapshot,
  or a database or table is gone from the live side.

borg needs a Storage Box, so a stand-in keeps archives as plain directories
and records each call; ``sqlite3``'s ``.backup`` is stood in by a copy. The
drill's own checks run for real on real SQLite files. The operator check is
``hermes-backup.sh --restore-drill`` on hermes-01
(deploy/hetzner-storagebox-setup.md section 7).
"""

import os
import shutil
import sqlite3
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from tests.support.paths import REPO_ROOT

_SCRIPT = REPO_ROOT / "deploy" / "hermes-backup.sh"
_BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(_BASH is None, reason="the backup script needs bash")

# Archives live under $FAKE_REPO/<name>/, named from a counter so --last 1 is the newest.
_FAKE_BORG = """#!/usr/bin/env bash
set -euo pipefail
{ printf 'borg'; printf ' %s' "$@"; printf ' | cwd=%s | rsh=%s\\n' "$PWD" "$BORG_RSH"; } \\
    >> "$FAKE_REPO/../borg.log"
case "$1" in
    create)
        n=$(( $(ls "$FAKE_REPO" | wc -l) + 1 ))
        archive="$FAKE_REPO/hermes-$(printf '%03d' "$n")"
        mkdir -p "$archive"
        for arg in "$@"; do
            case "$arg" in
                -*|::*|create|zstd,6) ;;
                /*) mkdir -p "$archive/${arg#/}"; cp -R "$arg/." "$archive/${arg#/}" ;;
                *) cp -R "$arg" "$archive/$arg" ;;
            esac
        done ;;
    list) ls "$FAKE_REPO" | tail -n 1 ;;
    extract) cp -R "$FAKE_REPO/${2#::}/$3" "$3" ;;
    *) exit 0 ;;
esac
"""

_FAKE_SQLITE3 = """#!/usr/bin/env bash
dest="${2#.backup \\'}"
cp "$1" "${dest%\\'}"
"""


@dataclass
class _Box:
    """A scratch VPS: data dir, fake repo and the tools on PATH."""

    root: Path

    @property
    def data(self) -> Path:
        return self.root / "data"

    @property
    def repo(self) -> Path:
        return self.root / "repo"

    def borg_calls(self) -> list[str]:
        """Return each borg call, one line per call."""
        log = self.root / "borg.log"
        return log.read_text(encoding="utf-8").splitlines() if log.exists() else []

    def run(self, *args: str, **extra: str) -> subprocess.CompletedProcess[str]:
        """Run the backup script with the fakes first on PATH."""
        env = {
            "PATH": f"{self.root / 'bin'}{os.pathsep}{os.environ['PATH']}",
            "HERMES_BACKUP_BORG_REPO": "ssh://u0@example.invalid:23/./hermes",
            "BORG_PASSPHRASE": "fake-passphrase",  # gitleaks:allow
            "HERMES_DATA_DIR": self.data.as_posix(),
            "FAKE_REPO": self.repo.as_posix(),
            **extra,
        }
        assert _BASH is not None
        return subprocess.run(  # noqa: S603 - fixed script, test-built env
            [_BASH, _SCRIPT.as_posix(), *args],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )


def _write_exe(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="\n")
    path.chmod(0o755)


def _make_db(path: Path, tables: dict[str, int]) -> None:
    """Create ``path`` with each table holding the given number of rows."""
    with sqlite3.connect(path) as db:
        for name, rows in tables.items():
            db.execute(f"CREATE TABLE {name} (id INTEGER PRIMARY KEY)")
            db.executemany(f"INSERT INTO {name} DEFAULT VALUES", [()] * rows)
    db.close()


@pytest.fixture
def box(tmp_path: Path) -> _Box:
    """Return a scratch VPS with a ledger of two fills and one L2 file."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_exe(bin_dir / "borg", _FAKE_BORG)
    _write_exe(bin_dir / "sqlite3", _FAKE_SQLITE3)
    found = _Box(tmp_path)
    found.repo.mkdir()
    (found.data / "l2").mkdir(parents=True)
    (found.data / "l2" / "btc.parquet").write_bytes(b"PAR1")
    _make_db(found.data / "hermes.sqlite3", {"fills": 2, "lots": 1})
    return found


def test_the_nightly_run_only_creates_an_archive(box: _Box) -> None:
    """No prune, compact or delete reaches the append-only key."""
    result = box.run()

    assert result.returncode == 0, result.stderr
    assert [call.split(" ")[1] for call in box.borg_calls()] == ["create"]


def test_the_archive_holds_the_snapshots_at_a_fixed_path(box: _Box) -> None:
    """Snapshots sit at sqlite/<name> in every archive, beside the L2 Parquet."""
    box.run()

    archive = box.repo / "hermes-001"
    l2 = archive / box.data.relative_to("/") / "l2" / "btc.parquet"
    assert sorted(p.name for p in (archive / "sqlite").iterdir()) == ["hermes.sqlite3"]
    assert l2.read_bytes() == b"PAR1"


def test_borg_connects_with_the_backup_key_only(box: _Box) -> None:
    """BORG_RSH names the backup's own key and refuses any other identity."""
    box.run(HERMES_BACKUP_SSH_KEY="/keys/backup")

    rsh = box.borg_calls()[0].split(" | rsh=")[1]
    assert rsh == (
        "ssh -p 23 -i /keys/backup -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"
    )


def test_the_drill_passes_and_prints_restored_beside_live_counts(box: _Box) -> None:
    """A fill written after the backup shows as a difference, not a failure."""
    box.run()
    with sqlite3.connect(box.data / "hermes.sqlite3") as db:
        db.execute("INSERT INTO fills DEFAULT VALUES")
    db.close()

    result = box.run("--restore-drill")

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines()[-4:-1] == [
        "hermes.sqlite3: integrity ok",
        "  fills: restored 2, live 3, differs (rows written since the backup?)",
        "  lots: restored 1, live 1, same",
    ]


def test_the_drill_restores_the_newest_archive(box: _Box) -> None:
    """With two archives, the drill extracts the later one."""
    box.run()
    box.run()

    box.run("--restore-drill")

    assert box.borg_calls()[-1].startswith("borg extract ::hermes-002 sqlite | ")


def test_the_drill_fails_without_an_archive(box: _Box) -> None:
    """An empty repo is a failed drill, not a silent pass."""
    result = box.run("--restore-drill")

    assert result.returncode == 1
    assert "FAIL: no hermes- archive" in result.stderr


def test_the_drill_fails_when_the_archive_holds_no_snapshot(box: _Box) -> None:
    """An archive taken with no database in the data dir fails the drill."""
    (box.data / "hermes.sqlite3").unlink()
    box.run()

    result = box.run("--restore-drill")

    assert result.returncode == 1
    assert "FAIL: the archive holds no SQLite snapshot" in result.stdout


def test_the_drill_fails_when_a_table_is_gone_live(box: _Box) -> None:
    """A table in the backup but not in the live ledger fails the drill."""
    box.run()
    with sqlite3.connect(box.data / "hermes.sqlite3") as db:
        db.execute("DROP TABLE lots")
    db.close()

    result = box.run("--restore-drill")

    assert result.returncode == 1
    assert "  FAIL: lots is in the backup but not in the live database" in result.stdout


def test_the_drill_fails_when_a_database_is_gone_live(box: _Box) -> None:
    """A snapshot whose live database no longer exists fails the drill."""
    box.run()
    (box.data / "hermes.sqlite3").unlink()

    result = box.run("--restore-drill")

    assert result.returncode == 1
    assert f"  FAIL: no live hermes.sqlite3 in {box.data}" in result.stdout
