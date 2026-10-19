"""Unit tests for ``hermes shadow-start`` (ADR-021 §2 and §3, #797).

``run`` is driven against the repository's config and signed pre-registration,
with the ledger, certification store and archive in ``tmp_path``, a fixed
clock and an injected service check.

Invariants (797-shadow-day-zero change.md R-3..R-6):

- Without ``--confirm START`` nothing on disk changes.
- A failed Ready rule or a running service stops the command before any change.
- A directory the start cannot write (permissions, a read-only mount, a
  path that is a file) stops it before any change, so it never archives the
  ledger and then fails to remove it (#797).
- Confirmed: the archive is a read-only copy with every row; the ledger and
  its WAL files are gone; the day-0 record holds the hashes and the checks and
  is never overwritten.
"""

import io
import json
import os
import sqlite3
import stat
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

import pytest

from hermes.adapters.certification.json_store import JsonCertificationStore
from hermes.application.calibration.certification_gates import CertificationRecord
from hermes.application.calibration.lifecycle import CertificationState
from hermes.application.strategy_protocol.day_zero import sha256_file
from hermes.entrypoints import serve as serve_module
from hermes.entrypoints.shadow_start import main, run
from hermes.infrastructure.config.loader import load_config
from tests.support.paths import REPO_ROOT
from tests.support.serve_loop import FakeFeed

_SID = "swing-donchian-4h"
_CONFIG = REPO_ROOT / "config"
_AT = datetime(2026, 10, 15, 8, 0, tzinfo=UTC)
_RUN_ID = f"{_SID}-20261015T080000Z"


def _ledger(tmp_path: Path, rows: int = 3) -> Path:
    db = tmp_path / "hermes.sqlite3"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE trades (id INTEGER PRIMARY KEY, note TEXT)")
    conn.executemany("INSERT INTO trades (note) VALUES (?)", [(f"t{i}",) for i in range(rows)])
    conn.commit()
    conn.close()
    return db


def _certify(db: Path, state: CertificationState = CertificationState.BACKTEST_CERTIFIED) -> None:
    fingerprint = serve_module.parameter_fingerprint_for(load_config(_CONFIG))
    JsonCertificationStore(serve_module.certification_dir_for(db)).save(
        CertificationRecord(_SID, state=state, parameter_fingerprint=fingerprint)
    )


def _run(db: Path, *, confirm: bool, service_active: bool = False) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = run(
        _SID,
        db=db,
        config_dir=_CONFIG,
        archive_dir=db.parent / "archive",
        confirm=confirm,
        stream=out,
        err=err,
        release_link=db.parent / "no-release",
        is_service_active=lambda: service_active,
        clock=lambda: _AT,
    )
    return code, out.getvalue(), err.getvalue()


def _tree(directory: Path) -> list[str]:
    return sorted(p.relative_to(directory).as_posix() for p in directory.rglob("*"))


class TestRefusals:
    """Nothing changes unless Ready holds, serve is stopped and START is given."""

    def test_dry_run_changes_nothing(self, tmp_path: Path) -> None:
        """A Ready dry run exits 0 and leaves the directory exactly as it was."""
        db = _ledger(tmp_path)
        _certify(db)
        before = _tree(tmp_path)
        code, out, _ = _run(db, confirm=False)
        assert (code, _tree(tmp_path), "Dry run" in out) == (0, before, True)

    # SEV-1: the warm-up ledger must never be removed before the Ready rules hold (ADR-021 §2)
    def test_uncertified_strategy_is_refused(self, tmp_path: Path) -> None:
        """No backtest gate: exit 1, the ledger untouched, rule 2 printed as fail."""
        db = _ledger(tmp_path)
        _certify(db, CertificationState.UNTRUSTED)
        before = sha256_file(db)
        code, out, err = _run(db, confirm=True)
        assert (code, sha256_file(db), "not Ready" in err) == (1, before, True)
        assert "rule 2            fail" in out

    # SEV-1: archiving a ledger serve is writing to would lose rows (ADR-021 §3)
    def test_running_service_is_refused(self, tmp_path: Path) -> None:
        """hermes.service active: exit 1 and no file changes."""
        db = _ledger(tmp_path)
        _certify(db)
        before = _tree(tmp_path)
        code, _, err = _run(db, confirm=True, service_active=True)
        assert (code, _tree(tmp_path), "is running" in err) == (1, before, True)


class TestConfirmed:
    """With START the warm-up ledger is archived and day 0 recorded."""

    def test_archive_is_a_read_only_full_copy(self, tmp_path: Path) -> None:
        """The archived copy holds every row and cannot be written."""
        db = _ledger(tmp_path, rows=5)
        _certify(db)
        assert _run(db, confirm=True)[0] == 0
        archived = tmp_path / "archive" / _RUN_ID / "hermes.sqlite3"
        conn = sqlite3.connect(f"file:{archived}?mode=ro", uri=True)
        rows = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
        conn.close()
        mode = stat.S_IMODE(archived.stat().st_mode)
        assert (rows, mode & 0o222) == (5, 0)

    def test_the_ledger_is_replaced_by_an_empty_one(self, tmp_path: Path) -> None:
        """Day 0 starts on an empty ledger serve accepts (#876): no warm-up row is left."""
        db = _ledger(tmp_path)
        (tmp_path / "hermes.sqlite3-wal").write_bytes(b"")
        (tmp_path / "hermes.sqlite3-shm").write_bytes(b"")
        _certify(db)
        assert _run(db, confirm=True)[0] == 0
        with closing(sqlite3.connect(db)) as fresh:
            counts = [
                fresh.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608
                for table in ("trades", "events", "capital_seeds")
            ]
        assert counts == [0, 0, 0]

    def test_serve_boots_on_the_day_zero_ledger_without_new_ledger(self, tmp_path: Path) -> None:
        """#876: hermes.service has no --new-ledger, so day 0 must leave a ledger serve opens."""
        db = _ledger(tmp_path)
        _certify(db)
        assert _run(db, confirm=True)[0] == 0
        composition = serve_module.build_composition(
            load_config(_CONFIG, env={}), db_path=db, env={}, feed=FakeFeed()
        )
        composition.sqlite_connection.close()
        if composition.serve_lock is not None:
            composition.serve_lock.release()
        assert composition.tracker.open_position_count() == 0

    def test_record_holds_the_hashes_and_checks(self, tmp_path: Path) -> None:
        """The day-0 record names the run, the archived copy's hash and every rule."""
        db = _ledger(tmp_path)
        _certify(db)
        assert _run(db, confirm=True)[0] == 0
        record = json.loads((tmp_path / "runs" / f"{_RUN_ID}.json").read_text())
        archived = tmp_path / "archive" / _RUN_ID / "hermes.sqlite3"
        assert (
            record["run_id"],
            record["archived_ledger_sha256"],
            record["data_manifest_sha256"],
            [c["rule"] for c in record["checks"]],
        ) == (
            _RUN_ID,
            sha256_file(archived),
            sha256_file(REPO_ROOT / "docs" / "experiments" / "swing-mvp1" / "data-manifest.json"),
            [1, 2, 3, 4, 6, 7],
        )

    def test_notebook_entry_is_printed(self, tmp_path: Path) -> None:
        """The output ends with a lab notebook entry naming the run."""
        db = _ledger(tmp_path)
        _certify(db)
        _, out, _ = _run(db, confirm=True)
        assert f"| shadow | operator | shadow day 0: counted run {_RUN_ID} starts" in out

    def test_second_start_in_the_same_second_is_refused(self, tmp_path: Path) -> None:
        """A record is never overwritten: the same run id twice exits 3."""
        db = _ledger(tmp_path)
        _certify(db)
        assert _run(db, confirm=True)[0] == 0
        assert _run(db, confirm=True)[0] == 3


_ROOT_SKIP = pytest.mark.skipif(
    os.geteuid() == 0, reason="root ignores directory permissions, so nothing is read-only"
)


class TestUnwritableDirectories:
    """The service account's restrictive permissions stop the start before any change."""

    @_ROOT_SKIP
    def test_a_read_only_ledger_directory_changes_nothing(self, tmp_path: Path) -> None:
        """The ledger cannot be removed: exit 3, no archive, no record, ledger intact."""
        db = _ledger(tmp_path)
        _certify(db)
        before = _tree(tmp_path)
        tmp_path.chmod(0o555)
        try:
            code, _, err = _run(db, confirm=True)
        finally:
            tmp_path.chmod(0o755)
        assert (code, _tree(tmp_path), f"cannot write in {tmp_path}" in err) == (3, before, True)

    @_ROOT_SKIP
    def test_a_read_only_archive_directory_changes_nothing(self, tmp_path: Path) -> None:
        """The archive cannot be written: exit 3 and the ledger stays."""
        db = _ledger(tmp_path)
        _certify(db)
        archive = tmp_path / "archive"
        archive.mkdir(mode=0o555)
        before = _tree(tmp_path)
        try:
            code, _, err = _run(db, confirm=True)
        finally:
            archive.chmod(0o755)
        assert (code, _tree(tmp_path), f"cannot write in {archive}" in err) == (3, before, True)

    # SEV-1: S5 an archive taken with the ledger left in place resumes the warm-up as day 0 (#797)
    def test_a_blocked_run_records_path_changes_nothing(self, tmp_path: Path) -> None:
        """Even as root: with ``runs`` taken by a file, no archive is left behind (#797)."""
        db = _ledger(tmp_path)
        _certify(db)
        (tmp_path / "runs").write_text("not a directory", encoding="utf-8")
        before = _tree(tmp_path)
        code, _, err = _run(db, confirm=True)
        assert (code, _tree(tmp_path), "cannot write in" in err) == (3, before, True)


class TestMain:
    """Argument parsing."""

    @pytest.mark.parametrize("argv", [[], [_SID, "--confirm", "yes"], [_SID, "--bogus"]])
    def test_usage_errors_exit_2(self, argv: list[str]) -> None:
        """No strategy, a wrong confirm word or an unknown flag is a usage error."""
        assert main(argv, stream=io.StringIO(), err=io.StringIO()) == 2
