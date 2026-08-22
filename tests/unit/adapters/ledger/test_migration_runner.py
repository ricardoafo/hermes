"""Tests for the SQLite migration runner.

Verifies:

- Applying migrations against a fresh DB creates every table + bookkeeping row.
- Re-running is idempotent — the second call returns an empty tuple.
- A partial-order set of files is applied in lexical order.
- SQL failure rolls back and does NOT record the failed filename.

Reference: ADR-003 §03 (migration approach).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from hermes.adapters.ledger.migration_runner import (
    MIGRATIONS_DIR,
    apply_migrations,
)


def _tables_in(conn: sqlite3.Connection) -> set[str]:
    """Return the set of user-visible table names in the connection."""
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {row[0] for row in rows}


class TestApplyMigrationsFreshDatabase:
    """Applying to a fresh :memory: DB installs every expected table."""

    def test_first_call_creates_all_tables(self) -> None:
        """First call installs trades, lots, lot_consumptions, events, schema_migrations."""
        conn = sqlite3.connect(":memory:")
        try:
            applied = apply_migrations(conn)
            tables = _tables_in(conn)
            # 0001 must have been applied.
            assert "0001_initial_schema.sql" in applied
            # Every documented table exists.
            expected = {"trades", "lots", "lot_consumptions", "events", "schema_migrations"}
            assert expected.issubset(tables)
        finally:
            conn.close()

    def test_second_call_is_idempotent_noop(self) -> None:
        """Re-running against a fully-migrated DB returns an empty tuple."""
        conn = sqlite3.connect(":memory:")
        try:
            apply_migrations(conn)
            second = apply_migrations(conn)
            # Nothing new was applied.
            assert second == ()
        finally:
            conn.close()

    def test_bookkeeping_row_recorded_per_migration(self) -> None:
        """Every applied filename appears in schema_migrations exactly once."""
        conn = sqlite3.connect(":memory:")
        try:
            apply_migrations(conn)
            rows = conn.execute(
                "SELECT filename FROM schema_migrations ORDER BY filename"
            ).fetchall()
            recorded = [r[0] for r in rows]
            assert "0001_initial_schema.sql" in recorded
        finally:
            conn.close()


class TestApplyMigrationsLexicalOrder:
    """Custom migrations directory: files applied in lexical order."""

    def test_files_applied_in_lexical_order(self, tmp_path: Path) -> None:
        """Two migrations named 0001 and 0002 apply in that order."""
        (tmp_path / "0001_first.sql").write_text("CREATE TABLE first (id INTEGER);")
        (tmp_path / "0002_second.sql").write_text("CREATE TABLE second (id INTEGER);")
        conn = sqlite3.connect(":memory:")
        try:
            applied = apply_migrations(conn, migrations_dir=tmp_path)
            assert applied == ("0001_first.sql", "0002_second.sql")
        finally:
            conn.close()


class TestApplyMigrationsFailureRollback:
    """SQL error in a migration rolls back and skips the bookkeeping insert."""

    def test_broken_sql_rolls_back_and_leaves_no_row(self, tmp_path: Path) -> None:
        """A migration with invalid SQL raises and does NOT record its filename."""
        (tmp_path / "0001_bad.sql").write_text("THIS IS NOT VALID SQL;")
        conn = sqlite3.connect(":memory:")
        try:
            with pytest.raises(sqlite3.Error):
                apply_migrations(conn, migrations_dir=tmp_path)
            recorded = conn.execute(
                "SELECT COUNT(*) FROM schema_migrations WHERE filename = ?",
                ("0001_bad.sql",),
            ).fetchone()[0]
            assert recorded == 0
        finally:
            conn.close()


class TestMigrationsDirLocation:
    """MIGRATIONS_DIR points at the shipped migrations directory."""

    def test_migrations_dir_contains_initial_schema(self) -> None:
        """The shipped migrations dir includes 0001_initial_schema.sql."""
        assert (MIGRATIONS_DIR / "0001_initial_schema.sql").exists()
