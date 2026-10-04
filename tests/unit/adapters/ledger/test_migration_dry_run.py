"""Unit tests for ``dry_run``: pending migrations applied to a copy of the ledger (#556).

Invariants under test:

- The real ledger file is byte-identical after a dry run, success or failure.
- The report lists what would run, what applied, any error, integrity, and the
  row count of every table before and after.
- A destructive rebuild that keeps its rows shows equal counts.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from hermes.adapters.ledger.migration_runner import DESTRUCTIVE_MARKER, apply_migrations, dry_run
from hermes.domain.errors import LedgerError


def _ledger(tmp_path: Path) -> tuple[Path, Path]:
    mig = tmp_path / "migrations"
    mig.mkdir()
    (mig / "0001_items.sql").write_text(
        "CREATE TABLE items (id INTEGER PRIMARY KEY, label TEXT NOT NULL DEFAULT 'x');",
        encoding="utf-8",
    )
    db = tmp_path / "ledger.sqlite3"
    conn = sqlite3.connect(db)
    apply_migrations(conn, mig)
    conn.executemany("INSERT INTO items (label) VALUES (?)", [("a",), ("b",), ("c",)])
    conn.commit()
    conn.close()
    return db, mig


def _add_rebuild(mig: Path) -> None:
    (mig / "0002_items_rebuild.sql").write_text(
        "\n".join(
            [
                DESTRUCTIVE_MARKER,
                "CREATE TABLE items_new (id INTEGER PRIMARY KEY, label TEXT NOT NULL);",
                "INSERT INTO items_new SELECT id, label FROM items;",
                "DROP TABLE items;",
                "ALTER TABLE items_new RENAME TO items;",
            ]
        ),
        encoding="utf-8",
    )


def test_destructive_rebuild_is_reported_with_equal_row_counts(tmp_path: Path) -> None:
    """A rebuild that keeps its rows applies on the copy, stays intact, and keeps 3 rows."""
    db, mig = _ledger(tmp_path)
    _add_rebuild(mig)

    report = dry_run(db, migrations_dir=mig)

    assert report.pending == (("0002_items_rebuild.sql", True),)
    assert (report.applied, report.error, report.intact, report.ok) == (
        ("0002_items_rebuild.sql",),
        None,
        True,
        True,
    )
    assert (report.rows_before["items"], report.rows_after["items"]) == (3, 3)


def test_real_ledger_is_untouched(tmp_path: Path) -> None:
    """The dry run reads the ledger only: its bytes and pending list are unchanged."""
    db, mig = _ledger(tmp_path)
    _add_rebuild(mig)
    before = db.read_bytes()

    dry_run(db, migrations_dir=mig)

    assert db.read_bytes() == before
    conn = sqlite3.connect(db)
    try:
        assert conn.execute("SELECT filename FROM schema_migrations").fetchall() == [
            ("0001_items.sql",)
        ]
    finally:
        conn.close()


def test_failing_migration_is_reported_and_stops_the_run(tmp_path: Path) -> None:
    """A broken migration is named with its error; later ones are not reached."""
    db, mig = _ledger(tmp_path)
    (mig / "0002_bad.sql").write_text("NOT SQL;", encoding="utf-8")
    (mig / "0003_later.sql").write_text("CREATE TABLE later (id INTEGER);", encoding="utf-8")
    before = db.read_bytes()

    report = dry_run(db, migrations_dir=mig)

    assert report.applied == ()
    assert report.error is not None
    assert report.error.startswith("0002_bad.sql: ")
    assert report.ok is False
    assert db.read_bytes() == before


def test_nothing_pending_is_an_ok_empty_report(tmp_path: Path) -> None:
    """With nothing to apply, the report is ok and the counts match."""
    db, mig = _ledger(tmp_path)

    report = dry_run(db, migrations_dir=mig)

    assert (report.pending, report.ok, report.rows_after) == ((), True, report.rows_before)


def test_missing_ledger_is_refused(tmp_path: Path) -> None:
    """A dry run never creates a ledger."""
    missing = tmp_path / "absent.sqlite3"
    with pytest.raises(LedgerError, match="does not exist"):
        dry_run(missing)
    assert not missing.exists()
