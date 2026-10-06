"""Minimal SQLite migration runner — reads ``NNNN_*.sql`` files in order.

Not a general-purpose migration library. Alembic is overkill for a
single-writer SQLite ledger (ADR-003 §03 explicitly rules it out). This
runner does one thing: pick up new ``*.sql`` files, apply them in lexical
order, and remember which have run in a ``schema_migrations`` table so
re-runs are idempotent.

Rollback protocol (#137). ADR-006 keeps migrations forward-only: there are
no down-migrations, and recovery from a bad migration is restoring a backup.
This module makes that recovery mechanical:

- Each migration runs inside one explicit transaction, so a failure leaves
  the schema exactly as it was.
- A migration whose first line is :data:`DESTRUCTIVE_MARKER` (it drops or
  rebuilds something) is never applied by :func:`apply_migrations`, which
  runs on every ledger start. It runs only through :func:`migrate_with_snapshot`,
  which first writes and verifies a snapshot of the whole database. Restoring
  that snapshot (:func:`restore_snapshot`) is the rollback.

The operator procedure is in ``docs/guides/ledger-migrations.md``.

Reference: ADR-003 §03 (migration approach — plain SQL, numbered),
ADR-006 §2 (forward-only, restore from backup), GitHub issue #137.
"""

from __future__ import annotations

import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from hermes.domain.errors import LedgerError

__all__ = [
    "DESTRUCTIVE_MARKER",
    "MIGRATIONS_DIR",
    "DryRunReport",
    "apply_migrations",
    "dry_run",
    "migrate_with_snapshot",
    "pending_migrations",
    "restore_snapshot",
    "unknown_applied_migrations",
]

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
DESTRUCTIVE_MARKER = "-- hermes: destructive"


def _ensure_bookkeeping(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "  filename TEXT PRIMARY KEY,"
        "  applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))"
        ")"
    )
    conn.commit()


def _is_destructive(sql_file: Path) -> bool:
    lines = sql_file.read_text(encoding="utf-8").lstrip().splitlines()
    return bool(lines) and lines[0].strip() == DESTRUCTIVE_MARKER


def pending_migrations(
    conn: sqlite3.Connection, migrations_dir: Path | None = None
) -> tuple[Path, ...]:
    """Return the migration files not yet applied, in lexical order.

    Args:
        conn: An open SQLite connection.
        migrations_dir: Directory to scan. Defaults to :data:`MIGRATIONS_DIR`.

    Returns:
        The pending ``*.sql`` paths.
    """
    dir_ = migrations_dir if migrations_dir is not None else MIGRATIONS_DIR
    _ensure_bookkeeping(conn)
    applied = {row[0] for row in conn.execute("SELECT filename FROM schema_migrations")}
    return tuple(p for p in sorted(dir_.glob("*.sql")) if p.name not in applied)


def unknown_applied_migrations(
    conn: sqlite3.Connection, migrations_dir: Path | None = None
) -> tuple[str, ...]:
    """Return the applied migrations this code does not ship, in lexical order.

    A non-empty result means a newer release migrated the ledger: this code
    does not know that schema.

    Args:
        conn: An open SQLite connection.
        migrations_dir: Directory to scan. Defaults to :data:`MIGRATIONS_DIR`.

    Returns:
        The filenames recorded in ``schema_migrations`` with no ``*.sql`` file.
    """
    dir_ = migrations_dir if migrations_dir is not None else MIGRATIONS_DIR
    _ensure_bookkeeping(conn)
    known = {p.name for p in dir_.glob("*.sql")}
    applied = {row[0] for row in conn.execute("SELECT filename FROM schema_migrations")}
    return tuple(sorted(applied - known))


def _apply_one(conn: sqlite3.Connection, sql_file: Path) -> None:
    script = sql_file.read_text(encoding="utf-8")
    # executescript() commits any open transaction and then runs in autocommit,
    # so without an explicit BEGIN a failure half-way would keep the first
    # statements. An explicit BEGIN keeps the script and its bookkeeping row in
    # one transaction, committed together or rolled back together.
    try:
        conn.executescript(f"BEGIN;\n{script}\n;")
        conn.execute("INSERT INTO schema_migrations (filename) VALUES (?)", (sql_file.name,))
        conn.commit()
    except sqlite3.Error:
        if conn.in_transaction:
            conn.rollback()
        raise


def apply_migrations(
    conn: sqlite3.Connection, migrations_dir: Path | None = None
) -> tuple[str, ...]:
    """Apply every pending non-destructive migration in lexical order.

    Runs on every ledger start. On a ledger that already holds applied
    migrations it stops at the first pending destructive migration: that
    one, and everything after it, waits for :func:`migrate_with_snapshot`.
    A brand-new ledger (no migration applied yet) has no data to protect,
    so it gets every migration, destructive ones included.

    Idempotent: re-running on a fully-migrated database is a no-op and
    returns an empty tuple.

    Args:
        conn: An open SQLite connection. The caller owns opening/closing.
        migrations_dir: Directory to scan for ``*.sql`` files. Defaults to
            :data:`MIGRATIONS_DIR` (this package's ``migrations/``).

    Returns:
        Tuple of migration filenames applied during THIS call (in order).

    Raises:
        LedgerError: A destructive migration is pending; run
            ``hermes migrate`` first.
        sqlite3.Error: On any SQL failure. The failing migration is rolled
            back in full and its filename is NOT recorded.
    """
    pending = pending_migrations(conn, migrations_dir)
    fresh = conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 0
    applied: list[str] = []
    for sql_file in pending:
        if not fresh and _is_destructive(sql_file):
            raise LedgerError(
                msg=(
                    f"destructive migration {sql_file.name} is pending; it is never applied "
                    "at start. Run `hermes migrate` (snapshot first), see "
                    "docs/guides/ledger-migrations.md"
                )
            )
        _apply_one(conn, sql_file)
        applied.append(sql_file.name)
    return tuple(applied)


def _check_integrity(conn: sqlite3.Connection, path: Path) -> None:
    check = conn.execute("PRAGMA integrity_check").fetchone()
    if check is None or check[0] != "ok":
        raise LedgerError(msg=f"snapshot {path} failed integrity_check: {check!r}")


def _snapshot(conn: sqlite3.Connection, snapshot_path: Path) -> None:
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    target = sqlite3.connect(snapshot_path)
    try:
        conn.backup(target)
        _check_integrity(target, snapshot_path)
    finally:
        target.close()


def migrate_with_snapshot(
    db_path: Path,
    snapshot_dir: Path,
    *,
    migrations_dir: Path | None = None,
    now: datetime | None = None,
) -> tuple[tuple[str, ...], Path | None]:
    """Snapshot the ledger, then apply every pending migration, destructive ones included.

    The snapshot is taken with SQLite's online backup API and checked with
    ``PRAGMA integrity_check`` before any migration runs. Stop
    ``hermes.service`` first; the ledger must have no other writer.

    Args:
        db_path: Ledger database file.
        snapshot_dir: Where the snapshot is written.
        migrations_dir: Directory to scan. Defaults to :data:`MIGRATIONS_DIR`.
        now: Timestamp for the snapshot name. Defaults to the current UTC time.

    Returns:
        The filenames applied, and the snapshot path (``None`` when nothing
        was pending, so no snapshot was needed).

    Raises:
        LedgerError: The ledger file is missing or the snapshot is not intact.
        sqlite3.Error: A migration failed; it was rolled back in full, and
            the snapshot holds the state before this call.
    """
    if not db_path.exists():
        raise LedgerError(msg=f"ledger {db_path} does not exist")
    conn = sqlite3.connect(db_path)
    try:
        pending = pending_migrations(conn, migrations_dir)
        if not pending:
            return (), None
        stamp = (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")
        snapshot_path = snapshot_dir / f"{db_path.stem}-pre-{pending[0].stem}-{stamp}.sqlite3"
        _snapshot(conn, snapshot_path)
        applied: list[str] = []
        for sql_file in pending:
            _apply_one(conn, sql_file)
            applied.append(sql_file.name)
        return tuple(applied), snapshot_path
    finally:
        conn.close()


def restore_snapshot(snapshot_path: Path, db_path: Path) -> None:
    """Replace the ledger's contents with a snapshot (the rollback).

    Stop ``hermes.service`` first. The ledger is overwritten page by page
    through the backup API, so its file permissions and owner stay.

    Args:
        snapshot_path: A snapshot written by :func:`migrate_with_snapshot`.
        db_path: Ledger database file to restore into.

    Raises:
        LedgerError: The snapshot is missing or not intact.
    """
    if not snapshot_path.exists():
        raise LedgerError(msg=f"snapshot {snapshot_path} does not exist")
    source = sqlite3.connect(f"{snapshot_path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        _check_integrity(source, snapshot_path)
        target = sqlite3.connect(db_path)
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()


@dataclass(frozen=True, slots=True)
class DryRunReport:
    """What a migration run would do, measured on a copy of the ledger.

    Args:
        pending: Migrations that would run, in order, with their destructive flag.
        applied: Migrations that applied on the copy (all of ``pending`` on success).
        error: The SQL error of the migration that failed on the copy, or ``None``.
        intact: Whether the copy passed ``PRAGMA integrity_check`` afterwards.
        rows_before: Row count per table before any migration.
        rows_after: Row count per table after the migrations that applied.
    """

    pending: tuple[tuple[str, bool], ...]
    applied: tuple[str, ...]
    error: str | None
    intact: bool
    rows_before: dict[str, int]
    rows_after: dict[str, int]

    @property
    def ok(self) -> bool:
        """True when every pending migration applied and the copy is intact."""
        return self.error is None and self.intact


def _row_counts(conn: sqlite3.Connection) -> dict[str, int]:
    tables = [
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]
    # Table names come from sqlite_master itself, quoted as identifiers.
    return {
        name: int(conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0])  # noqa: S608
        for name in tables
    }


def dry_run(db_path: Path, *, migrations_dir: Path | None = None) -> DryRunReport:
    """Apply every pending migration to a temporary copy of the ledger and report.

    The copy is taken with SQLite's online backup API, so ``hermes.service``
    may keep running; the real ledger is only read. The copy is deleted on
    return.

    Args:
        db_path: Ledger database file.
        migrations_dir: Directory to scan. Defaults to :data:`MIGRATIONS_DIR`.

    Returns:
        The report.

    Raises:
        LedgerError: The ledger file is missing or cannot be read.
    """
    if not db_path.exists():
        raise LedgerError(msg=f"ledger {db_path} does not exist")
    with tempfile.TemporaryDirectory(prefix="hermes-dry-run-") as tmp:
        copy_path = Path(tmp) / db_path.name
        try:
            source = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
            target = sqlite3.connect(copy_path)
            try:
                source.backup(target)
            finally:
                source.close()
        except sqlite3.Error as exc:
            raise LedgerError(msg=f"cannot copy {db_path} for a dry run: {exc}") from exc
        try:
            pending = pending_migrations(target, migrations_dir)
            rows_before = _row_counts(target)
            applied: list[str] = []
            error: str | None = None
            for sql_file in pending:
                try:
                    _apply_one(target, sql_file)
                except sqlite3.Error as exc:
                    error = f"{sql_file.name}: {exc}"
                    break
                applied.append(sql_file.name)
            check = target.execute("PRAGMA integrity_check").fetchone()
            return DryRunReport(
                pending=tuple((p.name, _is_destructive(p)) for p in pending),
                applied=tuple(applied),
                error=error,
                intact=check is not None and check[0] == "ok",
                rows_before=rows_before,
                rows_after=_row_counts(target),
            )
        finally:
            target.close()
