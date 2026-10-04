"""``hermes migrate`` — the only way a destructive ledger migration runs (#137).

Forms:

- ``hermes migrate --status``: list pending migrations, marking destructive ones.
- ``hermes migrate``: snapshot the ledger, verify the snapshot, then apply every
  pending migration in its own transaction.
- ``hermes migrate --restore SNAPSHOT``: copy a snapshot back over the ledger
  (the rollback; ADR-006 has no down-migrations).

Stop ``hermes.service`` before migrating or restoring. Procedure:
``docs/guides/ledger-migrations.md``.

Exit codes: 0 done, 1 migration failed (rolled back, snapshot kept), 2 usage,
3 ledger or snapshot problem.
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path
from typing import TextIO

from hermes.adapters.ledger.migration_runner import (
    DESTRUCTIVE_MARKER,
    DryRunReport,
    dry_run,
    migrate_with_snapshot,
    pending_migrations,
    restore_snapshot,
)
from hermes.domain.errors import LedgerError
from hermes.entrypoints.serve import DEFAULT_DB_PATH

__all__ = ["main"]

_USAGE = (
    "Usage: hermes migrate [--db PATH] [--snapshot-dir DIR]\n"
    "                      [--status | --dry-run | --restore SNAPSHOT]\n"
    "\n"
    "Stop hermes.service first. Without flags: snapshot the ledger, then apply every\n"
    "pending migration, destructive ones included. --restore copies a snapshot back.\n"
    "--dry-run applies them to a temporary copy and reports; it never writes the ledger.\n"
    "Exit codes: 0 done, 1 migration failed (rolled back), 2 usage, 3 ledger/snapshot.\n"
)


def _parse(argv: list[str], stream: TextIO) -> argparse.Namespace | None:
    parser = argparse.ArgumentParser(prog="hermes migrate", add_help=False)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--snapshot-dir", type=Path, default=None)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--status", action="store_true")
    group.add_argument("--dry-run", action="store_true")
    group.add_argument("--restore", type=Path, default=None)
    parser.add_argument("-h", "--help", action="store_true")
    try:
        return parser.parse_args(argv)
    except SystemExit:
        stream.write("ERROR: could not parse `hermes migrate` arguments.\n" + _USAGE)
        return None


def _write_dry_run(report: DryRunReport, stream: TextIO) -> None:
    if not report.pending:
        stream.write("dry run: no pending migrations.\n")
    for name, destructive in report.pending:
        mark = " (destructive)" if destructive else ""
        state = "applies" if name in report.applied else "not reached"
        stream.write(f"dry run: {name}{mark}: {state}\n")
    if report.error is not None:
        stream.write(f"dry run: FAILED {report.error}\n")
    stream.write(f"dry run: integrity_check {'ok' if report.intact else 'FAILED'}\n")
    for table in sorted(report.rows_before.keys() | report.rows_after.keys()):
        before = report.rows_before.get(table, "-")
        after = report.rows_after.get(table, "-")
        stream.write(f"dry run: rows {table}: {before} -> {after}\n")


def _dry_run(db: Path, stream: TextIO) -> int:
    try:
        report = dry_run(db)
    except LedgerError as exc:
        stream.write(f"ERROR: {exc}\n")
        return 3
    _write_dry_run(report, stream)
    return 0 if report.ok else 1


def _status(db: Path, stream: TextIO) -> int:
    if not db.exists():
        stream.write(f"ERROR: ledger {db} does not exist\n")
        return 3
    conn = sqlite3.connect(db)
    try:
        pending = pending_migrations(conn)
    finally:
        conn.close()
    if not pending:
        stream.write("No pending migrations.\n")
    for path in pending:
        first = path.read_text(encoding="utf-8").lstrip().splitlines()[:1]
        tag = " (destructive)" if first and first[0].strip() == DESTRUCTIVE_MARKER else ""
        stream.write(f"pending: {path.name}{tag}\n")
    return 0


def _restore(snapshot: Path, db: Path, stream: TextIO) -> int:
    try:
        restore_snapshot(snapshot, db)
    except LedgerError as exc:
        stream.write(f"ERROR: {exc}\n")
        return 3
    stream.write(f"Restored {db} from {snapshot}.\n")
    return 0


def _migrate(db: Path, snapshot_dir: Path, stream: TextIO) -> int:
    try:
        applied, snapshot = migrate_with_snapshot(db, snapshot_dir)
    except LedgerError as exc:
        stream.write(f"ERROR: {exc}\n")
        return 3
    except sqlite3.Error as exc:
        stream.write(
            f"ERROR: migration failed and was rolled back: {exc}\n"
            "The snapshot taken before it is in the snapshot directory.\n"
        )
        return 1
    if snapshot is None:
        stream.write("No pending migrations.\n")
        return 0
    stream.write(f"Snapshot: {snapshot}\n")
    for name in applied:
        stream.write(f"applied: {name}\n")
    return 0


def main(argv: list[str], *, stream: TextIO) -> int:
    """Parse ``argv`` and run the requested migration action.

    Args:
        argv: Arguments after the ``migrate`` token.
        stream: Output stream.

    Returns:
        Process exit code.
    """
    parsed = _parse(argv, stream)
    if parsed is None:
        return 2
    if parsed.help:
        stream.write(_USAGE)
        return 0
    db: Path = parsed.db
    if parsed.status:
        return _status(db, stream)
    if parsed.dry_run:
        return _dry_run(db, stream)
    if parsed.restore is not None:
        return _restore(parsed.restore, db, stream)
    return _migrate(db, parsed.snapshot_dir or db.parent / "migration-snapshots", stream)
