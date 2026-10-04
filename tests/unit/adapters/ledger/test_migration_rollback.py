"""Unit tests for the ledger migration rollback protocol (#137).

ADR-006 has no down-migrations; the rollback is restoring a verified snapshot.
Invariants under test:

- A migration that fails half-way leaves no trace (all-or-nothing).
- A destructive migration never runs at ledger start.
- ``migrate_with_snapshot`` writes an intact snapshot before applying, and
  restoring it inverts the migration's schema and data changes.
- Re-running after a restore re-applies cleanly.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from hermes.adapters.ledger.migration_runner import (
    DESTRUCTIVE_MARKER,
    apply_migrations,
    migrate_with_snapshot,
    pending_migrations,
    restore_snapshot,
)
from hermes.domain.errors import LedgerError

_NOW = datetime(2026, 10, 3, 18, tzinfo=UTC)


def _tables(db: Path) -> list[str]:
    conn = sqlite3.connect(db)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
        ).fetchall()
    finally:
        conn.close()
    return [r[0] for r in rows]


def _migrations(tmp_path: Path) -> Path:
    mig = tmp_path / "migrations"
    mig.mkdir()
    (mig / "0001_items.sql").write_text(
        "CREATE TABLE items (id INTEGER PRIMARY KEY, label TEXT NOT NULL DEFAULT 'x');"
        "INSERT INTO items (label) VALUES ('kept');"
    )
    return mig


def _ledger(tmp_path: Path, mig: Path) -> Path:
    db = tmp_path / "ledger.sqlite3"
    conn = sqlite3.connect(db)
    apply_migrations(conn, mig)
    conn.close()
    return db


def _add_destructive(mig: Path) -> None:
    (mig / "0002_items_drop_default.sql").write_text(
        "\n".join(
            [
                DESTRUCTIVE_MARKER,
                "CREATE TABLE items_new (id INTEGER PRIMARY KEY, label TEXT NOT NULL);",
                "INSERT INTO items_new SELECT id, label FROM items;",
                "DROP TABLE items;",
                "ALTER TABLE items_new RENAME TO items;",
            ]
        )
    )


def test_half_failed_migration_leaves_no_trace(tmp_path: Path) -> None:
    """Two good statements then a bad one: none of them stays, and nothing is recorded."""
    mig = tmp_path / "m"
    mig.mkdir()
    (mig / "0001_bad.sql").write_text(
        "CREATE TABLE a (id INTEGER); CREATE TABLE b (id INTEGER); NOT SQL;"
    )
    conn = sqlite3.connect(":memory:")
    try:
        with pytest.raises(sqlite3.Error):
            apply_migrations(conn, mig)
        tables = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        assert tables == [("schema_migrations",)]
    finally:
        conn.close()


def test_destructive_migration_never_runs_at_start(tmp_path: Path) -> None:
    """Ledger start refuses a pending destructive migration and leaves the schema as it was."""
    mig = _migrations(tmp_path)
    db = _ledger(tmp_path, mig)
    _add_destructive(mig)
    conn = sqlite3.connect(db)
    try:
        with pytest.raises(LedgerError, match="hermes migrate"):
            apply_migrations(conn, mig)
        assert [p.name for p in pending_migrations(conn, mig)] == ["0002_items_drop_default.sql"]
    finally:
        conn.close()


def test_non_destructive_migrations_before_a_destructive_one_still_apply(tmp_path: Path) -> None:
    """On an existing ledger, start applies everything up to the destructive file, then stops."""
    mig = _migrations(tmp_path)
    conn = sqlite3.connect(":memory:")
    apply_migrations(conn, mig)
    (mig / "0002_extra.sql").write_text("CREATE TABLE extra (id INTEGER);")
    (mig / "0003_items_drop_default.sql").write_text(f"{DESTRUCTIVE_MARKER}\nDROP TABLE extra;")
    try:
        with pytest.raises(LedgerError):
            apply_migrations(conn, mig)
        assert [p.name for p in pending_migrations(conn, mig)] == ["0003_items_drop_default.sql"]
    finally:
        conn.close()


def test_migrate_snapshots_before_applying_and_restore_inverts_it(tmp_path: Path) -> None:
    """The snapshot holds the pre-migration schema; restoring it brings the default back."""
    mig = _migrations(tmp_path)
    db = _ledger(tmp_path, mig)
    _add_destructive(mig)

    applied, snapshot = migrate_with_snapshot(db, tmp_path / "snaps", migrations_dir=mig, now=_NOW)

    assert applied == ("0002_items_drop_default.sql",)
    assert (
        snapshot
        == tmp_path / "snaps" / "ledger-pre-0002_items_drop_default-20261003T180000Z.sqlite3"
    )
    conn = sqlite3.connect(db)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO items (id) VALUES (99)")
    conn.close()

    restore_snapshot(snapshot, db)

    conn = sqlite3.connect(db)
    try:
        conn.execute("INSERT INTO items (id) VALUES (99)")
        labels = [r[0] for r in conn.execute("SELECT label FROM items ORDER BY id")]
        assert labels == ["kept", "x"]
        assert [p.name for p in pending_migrations(conn, mig)] == ["0002_items_drop_default.sql"]
    finally:
        conn.close()


def test_rerun_after_restore_reapplies_cleanly(tmp_path: Path) -> None:
    """After a rollback, the same migration applies again without manual fixes."""
    mig = _migrations(tmp_path)
    db = _ledger(tmp_path, mig)
    _add_destructive(mig)
    _, snapshot = migrate_with_snapshot(db, tmp_path / "snaps", migrations_dir=mig, now=_NOW)
    assert snapshot is not None
    restore_snapshot(snapshot, db)

    applied, _ = migrate_with_snapshot(db, tmp_path / "snaps2", migrations_dir=mig, now=_NOW)

    assert applied == ("0002_items_drop_default.sql",)
    assert _tables(db) == ["items", "schema_migrations"]


def test_failed_migration_keeps_the_snapshot_and_the_ledger_unchanged(tmp_path: Path) -> None:
    """A destructive migration that fails is rolled back; the snapshot is still there."""
    mig = _migrations(tmp_path)
    db = _ledger(tmp_path, mig)
    (mig / "0002_broken.sql").write_text(f"{DESTRUCTIVE_MARKER}\nDROP TABLE items;\nNOT SQL;")
    with pytest.raises(sqlite3.Error):
        migrate_with_snapshot(db, tmp_path / "snaps", migrations_dir=mig, now=_NOW)
    assert _tables(db) == ["items", "schema_migrations"]
    assert len(list((tmp_path / "snaps").glob("*.sqlite3"))) == 1


def test_nothing_pending_takes_no_snapshot(tmp_path: Path) -> None:
    """With nothing to apply there is nothing to roll back to, so no snapshot is written."""
    mig = _migrations(tmp_path)
    db = _ledger(tmp_path, mig)
    assert migrate_with_snapshot(db, tmp_path / "snaps", migrations_dir=mig) == ((), None)
    assert not (tmp_path / "snaps").exists()


def test_missing_ledger_or_snapshot_is_refused(tmp_path: Path) -> None:
    """Migrating a ledger that is not there, or restoring a missing snapshot, fails loudly."""
    with pytest.raises(LedgerError, match="does not exist"):
        migrate_with_snapshot(tmp_path / "absent.sqlite3", tmp_path / "snaps")
    with pytest.raises(LedgerError, match="does not exist"):
        restore_snapshot(tmp_path / "absent.sqlite3", tmp_path / "ledger.sqlite3")


def test_corrupt_snapshot_is_not_restored(tmp_path: Path) -> None:
    """A snapshot that fails integrity_check never overwrites the ledger."""
    mig = _migrations(tmp_path)
    db = _ledger(tmp_path, mig)
    bad = tmp_path / "bad.sqlite3"
    bad.write_bytes(b"not a database at all" * 100)
    before = db.read_bytes()
    with pytest.raises((LedgerError, sqlite3.DatabaseError)):
        restore_snapshot(bad, db)
    assert db.read_bytes() == before


def test_fresh_ledger_gets_destructive_migrations_too(tmp_path: Path) -> None:
    """A new ledger has no rows to protect, so start applies every migration."""
    mig = _migrations(tmp_path)
    _add_destructive(mig)
    conn = sqlite3.connect(":memory:")
    try:
        applied = apply_migrations(conn, mig)
    finally:
        conn.close()
    assert applied == ("0001_items.sql", "0002_items_drop_default.sql")
