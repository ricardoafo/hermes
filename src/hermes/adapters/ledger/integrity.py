"""Read-only integrity check of the SQLite ledger (#538).

:func:`check_ledger` inspects an open connection and returns a
:class:`LedgerReport`; it never writes, so it is safe on a ledger that
``hermes.service`` is using. :func:`open_read_only` opens the file with the
SQLite ``mode=ro`` URI so even a bug here could not modify the ledger.

Reference: openspec/changes/538-ledger-verify/change.md.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from hermes.adapters.ledger.migration_runner import MIGRATIONS_DIR, pending_migrations

__all__ = ["IdGap", "LedgerReport", "check_ledger", "find_id_gaps", "open_read_only"]


@dataclass(frozen=True, slots=True)
class IdGap:
    """A run of consecutive event ids that are absent from the ledger.

    Attributes:
        first: Lowest missing id of the run.
        last: Highest missing id of the run.
    """

    first: int
    last: int

    def describe(self) -> str:
        """Return ``"7"`` for a single id and ``"7-9"`` for a run."""
        return str(self.first) if self.first == self.last else f"{self.first}-{self.last}"


@dataclass(frozen=True, slots=True)
class LedgerReport:
    """What the integrity check found.

    Attributes:
        integrity: Rows of ``PRAGMA integrity_check`` (``("ok",)`` when intact).
        schema_level: Version prefix of the latest applied migration, such as
            ``0005`` or ``20261004T0300`` (``0`` when none is applied).
        pending: Names of migrations not yet applied.
        event_count: Rows in the ``events`` table (0 when the table is absent).
        gaps: Runs of missing event ids.
        events_table_present: Whether the ``events`` table exists.
    """

    integrity: tuple[str, ...]
    schema_level: str
    pending: tuple[str, ...]
    event_count: int
    gaps: tuple[IdGap, ...]
    events_table_present: bool

    @property
    def findings(self) -> tuple[str, ...]:
        """One human-readable line per problem; empty when the ledger is OK."""
        found: list[str] = []
        if self.integrity != ("ok",):
            found.extend(f"integrity_check: {row}" for row in self.integrity)
        if not self.events_table_present:
            found.append("events table is missing")
        found.extend(f"event id gap: {gap.describe()}" for gap in self.gaps)
        if self.pending:
            found.append(f"pending migrations: {', '.join(self.pending)}")
        return tuple(found)

    @property
    def ok(self) -> bool:
        """True when there are no findings."""
        return not self.findings


def open_read_only(path: Path) -> sqlite3.Connection:
    """Open ``path`` with SQLite's ``mode=ro``; the connection cannot write.

    Raises:
        sqlite3.OperationalError: The file cannot be opened.
    """
    return sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)


def find_id_gaps(ids: Iterable[int], high_water: int) -> tuple[IdGap, ...]:
    """Return the runs of ids missing from ``1..high_water``.

    Args:
        ids: Ids present, ascending and unique.
        high_water: Highest id ever issued; ids above the largest present one
            up to here count as a trailing gap.

    Returns:
        The gaps in ascending order.
    """
    gaps: list[IdGap] = []
    expected = 1
    for present in ids:
        if present > expected:
            gaps.append(IdGap(expected, present - 1))
        expected = present + 1
    if high_water >= expected:
        gaps.append(IdGap(expected, high_water))
    return tuple(gaps)


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,))
    return row.fetchone() is not None


def _schema(conn: sqlite3.Connection) -> tuple[str, tuple[str, ...]]:
    if _table_exists(conn, "schema_migrations"):
        pending = tuple(path.name for path in pending_migrations(conn))
    else:
        pending = tuple(path.name for path in sorted(MIGRATIONS_DIR.glob("*.sql")))
    applied = [
        path.name for path in sorted(MIGRATIONS_DIR.glob("*.sql")) if path.name not in pending
    ]
    level = applied[-1].split("_", 1)[0] if applied else "0"
    return level, pending


def _high_water(conn: sqlite3.Connection, max_id: int) -> int:
    row = conn.execute("SELECT seq FROM sqlite_sequence WHERE name = 'events'").fetchone()
    return max(max_id, int(row[0])) if row is not None else max_id


def check_ledger(conn: sqlite3.Connection) -> LedgerReport:
    """Run the integrity, schema and event-sequence checks on ``conn``.

    Args:
        conn: A read-only connection to the ledger.

    Returns:
        The report; see :attr:`LedgerReport.findings`.

    Raises:
        sqlite3.DatabaseError: The file is not a usable SQLite database.
    """
    integrity = tuple(str(row[0]) for row in conn.execute("PRAGMA integrity_check"))
    level, pending = _schema(conn)
    if not _table_exists(conn, "events"):
        return LedgerReport(integrity, level, pending, 0, (), events_table_present=False)
    ids = (int(row[0]) for row in conn.execute("SELECT id FROM events ORDER BY id"))
    present = list(ids)
    high_water = _high_water(conn, present[-1] if present else 0)
    return LedgerReport(
        integrity=integrity,
        schema_level=level,
        pending=pending,
        event_count=len(present),
        gaps=find_id_gaps(present, high_water),
        events_table_present=True,
    )
