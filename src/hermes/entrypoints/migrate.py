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
    migrate_with_snapshot,
    pending_migrations,
    restore_snapshot,
)
from hermes.domain.errors import LedgerError
from hermes.entrypoints.serve import DEFAULT_DB_PATH

__all__ = ["main"]

_USAGE = (
    "Usage: hermes migrate [--db PATH] [--snapshot-dir DIR] [--status | --restore SNAPSHOT]\n"
    "\n"
    "Stop hermes.service first. Without flags: snapshot the ledger, then apply every\n"
    "pending migration, destructive ones included. --restore copies a snapshot back.\n"
    "Exit codes: 0 done, 1 migration failed (rolled back), 2 usage, 3 ledger/snapshot.\n"
)


def _parse(argv: list[str], stream: TextIO) -> argparse.Namespace | None:
    parser = argparse.ArgumentParser(prog="hermes migrate", add_help=False)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--snapshot-dir", type=Path, default=None)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--status", action="store_true")
    group.add_argument("--restore", type=Path, default=None)
    parser.add_argument("-h", "--help", action="store_true")
    try:
        return parser.parse_args(argv)
    except SystemExit:
        stream.write("ERROR: could not parse `hermes migrate` arguments.\n" + _USAGE)
        return None


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
    if parsed.restore is not None:
        return _restore(parsed.restore, db, stream)
    return _migrate(db, parsed.snapshot_dir or db.parent / "migration-snapshots", stream)
