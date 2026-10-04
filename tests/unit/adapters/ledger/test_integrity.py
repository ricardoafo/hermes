"""Tests for the read-only ledger integrity check (#538).

Invariants protected here:

- an event id missing anywhere in ``1..high-water`` is a gap, including ids
  missing at the start and ids deleted from the end (``sqlite_sequence``);
- the schema level is the number of the latest applied migration, and an
  unapplied migration is a finding;
- the check opens the ledger with ``mode=ro`` and cannot write.

Every ledger is built through the real migrations.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hermes.adapters.ledger.integrity import (
    IdGap,
    LedgerReport,
    check_ledger,
    find_id_gaps,
    open_read_only,
)
from hermes.adapters.ledger.migration_runner import MIGRATIONS_DIR, apply_migrations

_MIGRATIONS = sorted(MIGRATIONS_DIR.glob("*.sql"))
_LATEST = _MIGRATIONS[-1].name.split("_", 1)[0]


def _insert_events(conn: sqlite3.Connection, count: int) -> None:
    for _ in range(count):
        conn.execute(
            "INSERT INTO events (event_type, event_schema_version, correlation_id,"
            " occurred_at, payload_json, recorded_at) VALUES ('t', 1, 'c', 'now', '{}', 'now')"
        )
    conn.commit()


@pytest.fixture
def ledger(tmp_path: Path) -> Path:
    """Build a fully migrated ledger with 5 events (ids 1-5)."""
    db = tmp_path / "ledger.sqlite3"
    conn = sqlite3.connect(db)
    apply_migrations(conn)
    _insert_events(conn, 5)
    conn.close()
    return db


def _check(db: Path) -> LedgerReport:
    conn = open_read_only(db)
    try:
        return check_ledger(conn)
    finally:
        conn.close()


def _delete(db: Path, ids: tuple[int, ...]) -> None:
    conn = sqlite3.connect(db)
    marks = ",".join("?" * len(ids))
    conn.execute("DELETE FROM events WHERE id IN (" + marks + ")", ids)  # noqa: S608
    conn.commit()
    conn.close()


def test_intact_ledger_has_no_findings(ledger: Path) -> None:
    """A migrated ledger with contiguous ids reports its count and level, no findings."""
    report = _check(ledger)
    assert (report.event_count, report.schema_level, report.findings) == (5, _LATEST, ())
    assert report.ok is True


def test_empty_events_table_is_ok(tmp_path: Path) -> None:
    """A fresh ledger with no events has count 0 and no gaps."""
    db = tmp_path / "fresh.sqlite3"
    conn = sqlite3.connect(db)
    apply_migrations(conn)
    conn.close()
    report = _check(db)
    assert (report.event_count, report.gaps, report.ok) == (0, (), True)


def test_deleted_middle_event_is_a_gap(ledger: Path) -> None:
    """Deleting ids 2 and 3 from 1-5 leaves the single gap 2-3."""
    _delete(ledger, (2, 3))
    report = _check(ledger)
    assert report.gaps == (IdGap(2, 3),)
    assert report.findings == ("event id gap: 2-3",)


def test_deleted_last_event_is_a_trailing_gap(ledger: Path) -> None:
    """Deleting the newest id is caught through sqlite_sequence."""
    _delete(ledger, (5,))
    assert _check(ledger).gaps == (IdGap(5, 5),)


def test_deleted_first_event_is_a_leading_gap(ledger: Path) -> None:
    """Deleting id 1 leaves a gap at the start."""
    _delete(ledger, (1,))
    report = _check(ledger)
    assert report.gaps == (IdGap(1, 1),)
    assert report.findings == ("event id gap: 1",)


def test_unapplied_migration_is_a_finding(ledger: Path) -> None:
    """A migration missing from schema_migrations lowers the level and is reported."""
    conn = sqlite3.connect(ledger)
    conn.execute("DELETE FROM schema_migrations WHERE filename = ?", (_pending_name(),))
    conn.commit()
    conn.close()
    report = _check(ledger)
    assert report.schema_level == _MIGRATIONS[-2].name.split("_", 1)[0]
    assert report.findings == (f"pending migrations: {_pending_name()}",)


def _pending_name() -> str:
    return _MIGRATIONS[-1].name


def test_unmigrated_database_reports_level_zero_without_writing(tmp_path: Path) -> None:
    """A database without bookkeeping has level 0, all migrations pending, no events table."""
    db = tmp_path / "bare.sqlite3"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE other (x INTEGER)")
    conn.commit()
    conn.close()
    before = db.read_bytes()
    report = _check(db)
    assert report.schema_level == "0"
    assert len(report.pending) == len(_MIGRATIONS)
    assert "events table is missing" in report.findings
    assert db.read_bytes() == before


def test_integrity_rows_are_findings() -> None:
    """Any integrity_check row other than a lone 'ok' is a finding."""
    report = LedgerReport(("row 3 missing from index x",), 5, (), 0, (), events_table_present=True)
    assert report.findings == ("integrity_check: row 3 missing from index x",)


def test_connection_is_read_only(ledger: Path) -> None:
    """open_read_only refuses writes."""
    conn = open_read_only(ledger)
    try:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("DELETE FROM events")
    finally:
        conn.close()


def test_gap_describe_single_and_run() -> None:
    """A single id prints bare; a run prints first-last."""
    assert (IdGap(7, 7).describe(), IdGap(7, 9).describe()) == ("7", "7-9")


@given(st.sets(st.integers(min_value=1, max_value=60)), st.integers(min_value=0, max_value=60))
def test_gaps_are_exactly_the_missing_ids(present: set[int], extra: int) -> None:
    """Expanding the gap runs yields precisely 1..high-water minus the present ids."""
    high_water = max([*present, extra])
    gaps = find_id_gaps(sorted(present), high_water)
    missing = {n for gap in gaps for n in range(gap.first, gap.last + 1)}
    assert missing == set(range(1, high_water + 1)) - present


def _execute(db: Path, sql: str) -> None:
    conn = sqlite3.connect(db)
    conn.execute(sql)
    conn.commit()
    conn.close()


@pytest.fixture
def ledger_with_lot(ledger: Path) -> Path:
    """Add one lot of 1 BTC with 0.4 consumed, so 0.6 remains (#559)."""
    _execute(
        ledger,
        "INSERT INTO lots (lot_id, asset, original_qty, remaining_qty, cost_basis_per_unit, "
        "opened_at, strategy_id) VALUES ('L1', 'BTC', '1', '0.6', '60000', "
        "'2026-10-03T12:00:00+00:00', 'swing')",
    )
    _execute(
        ledger,
        "INSERT INTO lot_consumptions (lot_id, consumed_qty, consumed_asset, realized_gain_eur, "
        "consumed_at) VALUES ('L1', '0.4', 'BTC', '10', '2026-10-03T16:00:00+00:00')",
    )
    return ledger


def test_consistent_lot_has_no_findings(ledger_with_lot: Path) -> None:
    """Original 1 - consumed 0.4 == remaining 0.6: nothing to report."""
    assert _check(ledger_with_lot).findings == ()


def test_wrong_remainder_is_a_finding(ledger_with_lot: Path) -> None:
    """A remaining quantity that disagrees with the consumptions names the lot (#559)."""
    _execute(ledger_with_lot, "UPDATE lots SET remaining_qty = '0.5' WHERE lot_id = 'L1'")
    assert _check(ledger_with_lot).findings == (
        "lot: L1: original 1 - consumed 0.4 != remaining 0.5",
    )


def test_negative_remainder_is_a_finding(ledger_with_lot: Path) -> None:
    """A lot can never hold less than nothing (#559)."""
    _execute(ledger_with_lot, "UPDATE lots SET remaining_qty = '-0.1' WHERE lot_id = 'L1'")
    assert _check(ledger_with_lot).findings == ("lot: L1: remaining -0.1 is negative",)


def test_unreadable_quantity_is_a_finding(ledger_with_lot: Path) -> None:
    """A quantity that is not a number is reported, not crashed on (#559)."""
    _execute(ledger_with_lot, "UPDATE lots SET original_qty = 'abc' WHERE lot_id = 'L1'")
    assert _check(ledger_with_lot).findings == ("lot: L1: unreadable quantity",)


def test_consumption_of_a_missing_lot_is_an_orphan(ledger_with_lot: Path) -> None:
    """A consumption pointing at no lot is reported with its row id (#559)."""
    _execute(
        ledger_with_lot,
        "INSERT INTO lot_consumptions (lot_id, consumed_qty, consumed_asset, "
        "realized_gain_eur, consumed_at) VALUES ('GHOST', '1', 'BTC', '0', 't')",
    )
    assert _check(ledger_with_lot).orphans == ("consumption 2 names missing lot GHOST",)


def test_lot_naming_a_missing_trade_is_an_orphan(ledger_with_lot: Path) -> None:
    """A lot whose trade_id names no trade is reported (#559)."""
    _execute(ledger_with_lot, "UPDATE lots SET trade_id = 999 WHERE lot_id = 'L1'")
    assert _check(ledger_with_lot).findings == ("orphan: lot L1 names missing trade 999",)


def test_applied_migration_not_shipped_is_a_finding(ledger: Path) -> None:
    """A ledger written by a newer or forked release is flagged (#559)."""
    _execute(ledger, "INSERT INTO schema_migrations (filename) VALUES ('9999_ghost.sql')")
    assert _check(ledger).findings == ("applied but not shipped: 9999_ghost.sql",)


def test_unreadable_consumption_names_its_row(ledger_with_lot: Path) -> None:
    """A corrupt consumed_qty names that consumption, not a remainder mismatch (#612)."""
    _execute(ledger_with_lot, "UPDATE lot_consumptions SET consumed_qty = 'abc' WHERE id = 1")
    assert _check(ledger_with_lot).findings == ("lot: consumption 1 on L1: unreadable quantity",)
