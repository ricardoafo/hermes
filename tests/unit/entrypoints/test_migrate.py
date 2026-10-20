"""Unit tests for ``hermes migrate`` (#137).

Invariant under test: the command is the operator's path for the rollback
protocol: ``--status`` names destructive migrations, a plain run snapshots
then applies, ``--restore`` puts the snapshot back, and each outcome maps to
its exit code.
"""

import io
import sqlite3
from pathlib import Path

import pytest

from hermes.adapters.ledger import migration_runner
from hermes.adapters.ledger.migration_runner import DESTRUCTIVE_MARKER, apply_migrations
from hermes.entrypoints import cli
from hermes.entrypoints import migrate as module


@pytest.fixture
def mig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the runner at a temporary migrations directory."""
    directory = tmp_path / "migrations"
    directory.mkdir()
    (directory / "0001_items.sql").write_text("CREATE TABLE items (id INTEGER);")
    monkeypatch.setattr(migration_runner, "MIGRATIONS_DIR", directory)
    return directory


def _ledger(tmp_path: Path, mig: Path) -> Path:
    db = tmp_path / "ledger.sqlite3"
    conn = sqlite3.connect(db)
    apply_migrations(conn, mig)
    conn.close()
    return db


def _run(argv: list[str]) -> tuple[int, str]:
    out = io.StringIO()
    return module.main(argv, stream=out), out.getvalue()


def _run_split(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = module.main(argv, stream=out, err=err)
    return code, out.getvalue(), err.getvalue()


def test_status_marks_destructive_migrations(tmp_path: Path, mig: Path) -> None:
    """--status lists what is pending and flags what needs the snapshot path."""
    db = _ledger(tmp_path, mig)
    (mig / "0002_drop.sql").write_text(f"{DESTRUCTIVE_MARKER}\nDROP TABLE items;")
    assert _run(["--db", str(db), "--status"]) == (0, "pending: 0002_drop.sql (destructive)\n")


def test_migrate_then_restore_round_trips(tmp_path: Path, mig: Path) -> None:
    """A plain run snapshots and applies; --restore brings the dropped table back."""
    db = _ledger(tmp_path, mig)
    (mig / "0002_drop.sql").write_text(f"{DESTRUCTIVE_MARKER}\nDROP TABLE items;")

    code, out = _run(["--db", str(db), "--snapshot-dir", str(tmp_path / "snaps")])
    assert code == 0
    assert "applied: 0002_drop.sql" in out
    snapshot = Path(out.splitlines()[0].removeprefix("Snapshot: "))

    assert _run(["--db", str(db), "--restore", str(snapshot)])[0] == 0
    conn = sqlite3.connect(db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM items").fetchone() == (0,)
    finally:
        conn.close()


def test_failed_migration_exits_1(tmp_path: Path, mig: Path) -> None:
    """A failing migration is rolled back and reported with exit 1."""
    db = _ledger(tmp_path, mig)
    (mig / "0002_bad.sql").write_text("NOT SQL;")
    code, out, err = _run_split(["--db", str(db), "--snapshot-dir", str(tmp_path / "snaps")])
    assert code == 1
    assert err.startswith("ERROR: migration failed and was rolled back")
    assert not out.startswith("ERROR")


def test_missing_ledger_exits_3(tmp_path: Path, mig: Path) -> None:
    """Migrating or checking a ledger that does not exist is a ledger problem."""
    assert _run(["--db", str(tmp_path / "absent.sqlite3")])[0] == 3
    assert _run(["--db", str(tmp_path / "absent.sqlite3"), "--status"])[0] == 3


def test_status_and_restore_together_is_a_usage_error() -> None:
    """The two actions are exclusive."""
    assert _run(["--status", "--restore", "x"])[0] == 2


def test_cli_routes_migrate(monkeypatch: pytest.MonkeyPatch) -> None:
    """`hermes migrate` reaches this command's main."""
    seen: list[list[str]] = []

    def fake_main(rest: list[str], *, stream: object, err: object) -> int:
        seen.append(rest)
        return 0

    monkeypatch.setattr(module, "main", fake_main)
    assert cli.main(["migrate", "--status"], stream=io.StringIO()) == 0
    assert seen == [["--status"]]


def test_dry_run_reports_and_writes_nothing(tmp_path: Path, mig: Path) -> None:
    """--dry-run prints what would run and the row counts, and leaves the ledger as it was."""
    db = _ledger(tmp_path, mig)
    (mig / "0002_drop.sql").write_text(f"{DESTRUCTIVE_MARKER}\nDROP TABLE items;")
    before = db.read_bytes()

    code, out = _run(["--db", str(db), "--dry-run"])

    assert code == 0
    assert "dry run: 0002_drop.sql (destructive): applies\n" in out
    assert "dry run: rows items: 0 -> -\n" in out
    assert db.read_bytes() == before


def test_dry_run_of_a_failing_migration_exits_1(tmp_path: Path, mig: Path) -> None:
    """A migration that fails on the copy is reported with exit 1."""
    db = _ledger(tmp_path, mig)
    (mig / "0002_bad.sql").write_text("NOT SQL;")

    code, out = _run(["--db", str(db), "--dry-run"])

    assert code == 1
    assert "dry run: FAILED 0002_bad.sql: " in out
