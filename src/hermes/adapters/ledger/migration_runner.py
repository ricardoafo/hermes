"""Minimal SQLite migration runner — reads ``NNNN_*.sql`` files in order.

Not a general-purpose migration library. Alembic is overkill for a
single-writer SQLite ledger (ADR-003 §03 explicitly rules it out). This
runner does one thing: pick up new ``*.sql`` files, apply them in lexical
order, and remember which have run in a ``schema_migrations`` table so
re-runs are idempotent.

The runner is exposed as a plain function — no class, no state — so
composition can call it from ``entrypoints/`` at startup and tests can
call it against ``sqlite3.connect(":memory:")`` connections.

Rollback protocol: not implemented in this PR. Migrations are additive
in Phase 1 (schema grows only) — a rollback runner lands in a follow-up
issue when the first destructive migration is proposed.

Reference: ADR-003 §03 (migration approach — plain SQL, numbered).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

__all__ = ["MIGRATIONS_DIR", "apply_migrations"]

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def apply_migrations(
    conn: sqlite3.Connection, migrations_dir: Path | None = None
) -> tuple[str, ...]:
    """Apply every un-applied ``*.sql`` migration in lexical order.

    The runner:

    1. Creates the ``schema_migrations`` bookkeeping table if missing.
    2. Reads the set of already-applied filenames from that table.
    3. Iterates ``migrations_dir/*.sql`` sorted lexically.
    4. For each un-applied file, executes its SQL inside a single
       transaction and records the filename on success.

    Idempotent: re-running on a fully-migrated database is a no-op and
    returns an empty tuple.

    Args:
        conn: An open SQLite connection. The caller owns opening/closing.
        migrations_dir: Directory to scan for ``*.sql`` files. Defaults to
            :data:`MIGRATIONS_DIR` (this package's ``migrations/``).

    Returns:
        Tuple of migration filenames applied during THIS call (in order).
        Empty when nothing new was applied.

    Raises:
        sqlite3.Error: On any SQL failure. The failing migration is
            rolled back and its filename is NOT recorded.
    """
    dir_ = migrations_dir if migrations_dir is not None else MIGRATIONS_DIR

    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "  filename TEXT PRIMARY KEY,"
        "  applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))"
        ")"
    )
    conn.commit()

    already_applied = {
        row[0] for row in conn.execute("SELECT filename FROM schema_migrations").fetchall()
    }

    applied: list[str] = []
    for sql_file in sorted(dir_.glob("*.sql")):
        if sql_file.name in already_applied:
            continue
        script = sql_file.read_text(encoding="utf-8")
        try:
            conn.executescript(script)
            conn.execute("INSERT INTO schema_migrations (filename) VALUES (?)", (sql_file.name,))
            conn.commit()
        except sqlite3.Error:
            conn.rollback()
            raise
        applied.append(sql_file.name)

    return tuple(applied)
