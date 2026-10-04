"""Read-only consistency checks on the SQLite ledger (#559).

Each check returns a :class:`CheckResult`. The ledger is opened with SQLite's
``mode=ro`` URI, so the checks can run while ``hermes.service`` is up and can
never write.

Reference: GitHub issue #559.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from hermes.adapters.ledger.migration_runner import MIGRATIONS_DIR
from hermes.domain.errors import LedgerError

__all__ = ["CheckResult", "run_checks"]

_MAX_DETAILS = 5


@dataclass(frozen=True, slots=True)
class CheckResult:
    """Outcome of one check.

    Args:
        name: Short check name.
        ok: Whether the check passed.
        detail: Why it failed (first few offending rows), or a short summary.
    """

    name: str
    ok: bool
    detail: str


def _integrity(conn: sqlite3.Connection, _migrations: Path) -> CheckResult:
    rows = [r[0] for r in conn.execute("PRAGMA integrity_check")]
    ok = rows == ["ok"]
    return CheckResult("integrity", ok, "ok" if ok else "; ".join(rows[:_MAX_DETAILS]))


def _migrations(conn: sqlite3.Connection, migrations: Path) -> CheckResult:
    shipped = {p.name for p in migrations.glob("*.sql")}
    try:
        applied = {r[0] for r in conn.execute("SELECT filename FROM schema_migrations")}
    except sqlite3.OperationalError:
        return CheckResult("migrations", False, "no schema_migrations table")
    missing = sorted(shipped - applied)
    unknown = sorted(applied - shipped)
    problems = [f"not applied: {name}" for name in missing] + [
        f"applied but not shipped: {name}" for name in unknown
    ]
    if problems:
        return CheckResult("migrations", False, "; ".join(problems[:_MAX_DETAILS]))
    return CheckResult("migrations", True, f"{len(applied)} applied")


def _dec(raw: object) -> Decimal | None:
    try:
        return Decimal(str(raw))
    except InvalidOperation:
        return None


def _lot_remainders(conn: sqlite3.Connection, _migrations: Path) -> CheckResult:
    consumed: dict[str, Decimal] = {}
    for lot_id, qty in conn.execute("SELECT lot_id, consumed_qty FROM lot_consumptions"):
        value = _dec(qty)
        consumed[lot_id] = consumed.get(lot_id, Decimal(0)) + (value or Decimal(0))
    problems: list[str] = []
    count = 0
    for lot_id, original, remaining in conn.execute(
        "SELECT lot_id, original_qty, remaining_qty FROM lots ORDER BY lot_id"
    ):
        count += 1
        orig, rem = _dec(original), _dec(remaining)
        if orig is None or rem is None:
            problems.append(f"{lot_id}: unreadable quantity")
        elif rem < 0:
            problems.append(f"{lot_id}: remaining {rem} is negative")
        elif orig - consumed.get(lot_id, Decimal(0)) != rem:
            problems.append(
                f"{lot_id}: original {orig} - consumed {consumed.get(lot_id, Decimal(0))} "
                f"!= remaining {rem}"
            )
    if problems:
        return CheckResult("lot remainders", False, "; ".join(problems[:_MAX_DETAILS]))
    return CheckResult("lot remainders", True, f"{count} lots")


def _orphans(conn: sqlite3.Connection, _migrations: Path) -> CheckResult:
    problems = [
        f"consumption {cid} names missing lot {lot_id}"
        for cid, lot_id in conn.execute(
            "SELECT c.id, c.lot_id FROM lot_consumptions c "
            "LEFT JOIN lots l ON l.lot_id = c.lot_id WHERE l.lot_id IS NULL ORDER BY c.id"
        )
    ]
    lot_columns = {r[1] for r in conn.execute("PRAGMA table_info(lots)")}
    if "trade_id" in lot_columns:
        problems += [
            f"lot {lot_id} names missing trade {trade_id}"
            for lot_id, trade_id in conn.execute(
                "SELECT l.lot_id, l.trade_id FROM lots l LEFT JOIN trades t ON t.id = l.trade_id "
                "WHERE l.trade_id IS NOT NULL AND t.id IS NULL ORDER BY l.lot_id"
            )
        ]
    if problems:
        return CheckResult("orphans", False, "; ".join(problems[:_MAX_DETAILS]))
    return CheckResult("orphans", True, "none")


_CHECKS: tuple[Callable[[sqlite3.Connection, Path], CheckResult], ...] = (
    _integrity,
    _migrations,
    _lot_remainders,
    _orphans,
)


def run_checks(db_path: Path, *, migrations_dir: Path | None = None) -> tuple[CheckResult, ...]:
    """Run every check against the ledger, read-only.

    Args:
        db_path: Ledger database file.
        migrations_dir: Shipped migrations to compare against. Defaults to
            :data:`~hermes.adapters.ledger.migration_runner.MIGRATIONS_DIR`.

    Returns:
        One result per check, in a fixed order.

    Raises:
        LedgerError: The ledger is missing or cannot be read.
    """
    if not db_path.exists():
        raise LedgerError(msg=f"ledger {db_path} does not exist")
    migrations = migrations_dir if migrations_dir is not None else MIGRATIONS_DIR
    try:
        conn = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        raise LedgerError(msg=f"cannot open {db_path}: {exc}") from exc
    try:
        return tuple(check(conn, migrations) for check in _CHECKS)
    except sqlite3.Error as exc:
        raise LedgerError(msg=f"ledger check failed on {db_path}: {exc}") from exc
    finally:
        conn.close()
