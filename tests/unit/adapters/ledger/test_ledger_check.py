"""Unit tests for the read-only ledger consistency checks (#559).

Invariants under test:

- A consistent ledger passes every check.
- Each check fails on its own broken fixture and names the offending row.
- The ledger file is byte-identical after the checks.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from hermes.adapters.ledger.ledger_check import run_checks
from hermes.adapters.ledger.migration_runner import MIGRATIONS_DIR, apply_migrations
from hermes.domain.errors import LedgerError


def _ledger(tmp_path: Path) -> Path:
    """Return a ledger with every shipped migration, one lot of 1 BTC with 0.4 consumed."""
    db = tmp_path / "ledger.sqlite3"
    conn = sqlite3.connect(db)
    apply_migrations(conn)
    conn.execute(
        "INSERT INTO lots (lot_id, asset, original_qty, remaining_qty, cost_basis_per_unit, "
        "opened_at, strategy_id) VALUES ('L1', 'BTC', '1', '0.6', '60000', "
        "'2026-10-03T12:00:00+00:00', 'swing-donchian-4h')"
    )
    conn.execute(
        "INSERT INTO lot_consumptions (lot_id, consumed_qty, consumed_asset, realized_gain_eur, "
        "consumed_at) VALUES ('L1', '0.4', 'BTC', '10', '2026-10-03T16:00:00+00:00')"
    )
    conn.commit()
    conn.close()
    return db


def _results(db: Path) -> dict[str, tuple[bool, str]]:
    return {r.name: (r.ok, r.detail) for r in run_checks(db)}


def test_consistent_ledger_passes_every_check(tmp_path: Path) -> None:
    """Integrity, migrations, remainders and orphans all pass on a clean ledger."""
    results = _results(_ledger(tmp_path))
    assert {name: ok for name, (ok, _) in results.items()} == {
        "integrity": True,
        "migrations": True,
        "lot remainders": True,
        "orphans": True,
    }
    assert results["lot remainders"][1] == "1 lots"


def test_checks_never_write(tmp_path: Path) -> None:
    """The ledger is opened read-only: its bytes are unchanged."""
    db = _ledger(tmp_path)
    before = db.read_bytes()
    run_checks(db)
    assert db.read_bytes() == before


def _execute(db: Path, sql: str) -> None:
    conn = sqlite3.connect(db)
    conn.execute(sql)
    conn.commit()
    conn.close()


def test_wrong_remainder_is_named(tmp_path: Path) -> None:
    """Original 1 - consumed 0.4 must equal remaining; 0.5 is reported for L1."""
    db = _ledger(tmp_path)
    _execute(db, "UPDATE lots SET remaining_qty = '0.5' WHERE lot_id = 'L1'")
    assert _results(db)["lot remainders"] == (
        False,
        "L1: original 1 - consumed 0.4 != remaining 0.5",
    )


def test_negative_remainder_is_named(tmp_path: Path) -> None:
    """A lot can never hold less than nothing."""
    db = _ledger(tmp_path)
    _execute(db, "UPDATE lots SET remaining_qty = '-0.1' WHERE lot_id = 'L1'")
    assert _results(db)["lot remainders"] == (False, "L1: remaining -0.1 is negative")


def test_consumption_of_a_missing_lot_is_an_orphan(tmp_path: Path) -> None:
    """A consumption pointing at no lot is reported with its id."""
    db = _ledger(tmp_path)
    _execute(
        db,
        "INSERT INTO lot_consumptions (lot_id, consumed_qty, consumed_asset, "
        "realized_gain_eur, consumed_at) VALUES ('GHOST', '1', 'BTC', '0', 't')",
    )
    ok, detail = _results(db)["orphans"]
    assert (ok, detail) == (False, "consumption 2 names missing lot GHOST")


def test_lot_linked_to_a_missing_trade_is_an_orphan(tmp_path: Path) -> None:
    """A lot whose trade_id names no trade is reported."""
    db = _ledger(tmp_path)
    _execute(db, "UPDATE lots SET trade_id = 999 WHERE lot_id = 'L1'")
    assert _results(db)["orphans"] == (False, "lot L1 names missing trade 999")


def test_unapplied_and_unknown_migrations_are_named(tmp_path: Path) -> None:
    """A shipped file not applied, and an applied name not shipped, both fail the check."""
    db = _ledger(tmp_path)
    shipped = sorted(p.name for p in MIGRATIONS_DIR.glob("*.sql"))
    _execute(db, f"DELETE FROM schema_migrations WHERE filename = '{shipped[-1]}'")  # noqa: S608
    _execute(db, "INSERT INTO schema_migrations (filename) VALUES ('9999_ghost.sql')")
    assert _results(db)["migrations"] == (
        False,
        f"not applied: {shipped[-1]}; applied but not shipped: 9999_ghost.sql",
    )


def test_missing_ledger_is_refused(tmp_path: Path) -> None:
    """The check never creates a ledger."""
    missing = tmp_path / "absent.sqlite3"
    with pytest.raises(LedgerError, match="does not exist"):
        run_checks(missing)
    assert not missing.exists()
