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
from decimal import Decimal, InvalidOperation
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
        unknown_migrations: Applied migrations this release does not ship.
        lot_problems: Lots whose remaining quantity does not equal the
            original minus their consumptions, is negative or is unreadable (#559).
        orphans: Consumptions naming a missing lot, and lots naming a missing
            trade (#559).
    """

    integrity: tuple[str, ...]
    schema_level: str
    pending: tuple[str, ...]
    event_count: int
    gaps: tuple[IdGap, ...]
    events_table_present: bool
    unknown_migrations: tuple[str, ...] = ()
    lot_problems: tuple[str, ...] = ()
    orphans: tuple[str, ...] = ()

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
        if self.unknown_migrations:
            found.append(f"applied but not shipped: {', '.join(self.unknown_migrations)}")
        found.extend(f"lot: {problem}" for problem in self.lot_problems)
        found.extend(f"orphan: {orphan}" for orphan in self.orphans)
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


def _unknown_migrations(conn: sqlite3.Connection) -> tuple[str, ...]:
    if not _table_exists(conn, "schema_migrations"):
        return ()
    shipped = {path.name for path in MIGRATIONS_DIR.glob("*.sql")}
    applied = {str(row[0]) for row in conn.execute("SELECT filename FROM schema_migrations")}
    return tuple(sorted(applied - shipped))


def _decimal(raw: object) -> Decimal | None:
    try:
        return Decimal(str(raw))
    except InvalidOperation:
        return None


def _lot_problems(conn: sqlite3.Connection) -> tuple[str, ...]:
    if not (_table_exists(conn, "lots") and _table_exists(conn, "lot_consumptions")):
        return ()
    consumed: dict[str, Decimal] = {}
    problems: list[str] = []
    # A lot with an unreadable consumption has no trustworthy remainder to compare (#612).
    unsummable: set[str] = set()
    for cid, lot_id, qty in conn.execute(
        "SELECT id, lot_id, consumed_qty FROM lot_consumptions ORDER BY id"
    ):
        value = _decimal(qty)
        if value is None:
            problems.append(f"consumption {cid} on {lot_id}: unreadable quantity")
            unsummable.add(lot_id)
        else:
            consumed[lot_id] = consumed.get(lot_id, Decimal(0)) + value
    for lot_id, original, remaining in conn.execute(
        "SELECT lot_id, original_qty, remaining_qty FROM lots ORDER BY lot_id"
    ):
        orig, rem = _decimal(original), _decimal(remaining)
        used = consumed.get(lot_id, Decimal(0))
        if lot_id in unsummable:
            continue
        if orig is None or rem is None:
            problems.append(f"{lot_id}: unreadable quantity")
        elif rem < 0:
            problems.append(f"{lot_id}: remaining {rem} is negative")
        elif orig - used != rem:
            problems.append(f"{lot_id}: original {orig} - consumed {used} != remaining {rem}")
    return tuple(problems)


def _orphans(conn: sqlite3.Connection) -> tuple[str, ...]:
    if not (_table_exists(conn, "lots") and _table_exists(conn, "lot_consumptions")):
        return ()
    found = [
        f"consumption {cid} names missing lot {lot_id}"
        for cid, lot_id in conn.execute(
            "SELECT c.id, c.lot_id FROM lot_consumptions c "
            "LEFT JOIN lots l ON l.lot_id = c.lot_id WHERE l.lot_id IS NULL ORDER BY c.id"
        )
    ]
    lot_columns = {row[1] for row in conn.execute("PRAGMA table_info(lots)")}
    if "trade_id" in lot_columns and _table_exists(conn, "trades"):
        found += [
            f"lot {lot_id} names missing trade {trade_id}"
            for lot_id, trade_id in conn.execute(
                "SELECT l.lot_id, l.trade_id FROM lots l LEFT JOIN trades t ON t.id = l.trade_id "
                "WHERE l.trade_id IS NOT NULL AND t.id IS NULL ORDER BY l.lot_id"
            )
        ]
    return tuple(found)


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
    unknown, lots, orphans = _unknown_migrations(conn), _lot_problems(conn), _orphans(conn)
    if not _table_exists(conn, "events"):
        return LedgerReport(integrity, level, pending, 0, (), False, unknown, lots, orphans)
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
        unknown_migrations=unknown,
        lot_problems=lots,
        orphans=orphans,
    )
