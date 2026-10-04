"""Tests for migration 0005_lots_drop_strategy_default (#325).

Invariants under test:

- After 0005, ``lots.strategy_id`` is NOT NULL with no default, so an insert
  without a strategy id fails instead of silently writing 'swing'.
- Every lot row and value written before 0005 survives it, legacy 'swing' rows
  included, along with the indexes.
- On an existing ledger 0005 is destructive: start refuses it, and
  ``migrate_with_snapshot`` applies it after a snapshot.
"""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest

from hermes.adapters.ledger.migration_runner import (
    MIGRATIONS_DIR,
    apply_migrations,
    migrate_with_snapshot,
)
from hermes.domain.errors import LedgerError

_THROUGH_0004 = (
    "0001_initial_schema.sql",
    "0002_lots_add_strategy_id.sql",
    "0003_live_bars.sql",
    "0004_trade_eur_values.sql",
)
_LOTS_SQL = (
    "SELECT lot_id, asset, original_qty, remaining_qty, cost_basis_per_unit, opened_at, "
    "strategy_id, cost_basis_native_per_unit, native_currency, trade_id FROM lots ORDER BY lot_id"
)


def _ledger_at_0004(tmp_path: Path) -> Path:
    """Return a ledger migrated through 0004 with one legacy-default lot and one explicit lot."""
    through = tmp_path / "through_0004"
    through.mkdir()
    for name in _THROUGH_0004:
        shutil.copy(MIGRATIONS_DIR / name, through / name)
    db = tmp_path / "ledger.sqlite3"
    conn = sqlite3.connect(db)
    try:
        apply_migrations(conn, through)
        conn.execute(
            "INSERT INTO lots (lot_id, asset, original_qty, remaining_qty, cost_basis_per_unit, "
            "opened_at) VALUES ('L1', 'BTC', '1', '0.5', '60000', '2026-01-02T10:00:00+00:00')"
        )
        conn.execute(
            "INSERT INTO lots (lot_id, asset, original_qty, remaining_qty, cost_basis_per_unit, "
            "opened_at, strategy_id, cost_basis_native_per_unit, native_currency) VALUES "
            "('L2', 'ETH', '10', '10', NULL, '2026-01-03T10:00:00+00:00', 'swing-donchian-4h', "
            "'2500.5', 'USDT')"
        )
        conn.commit()
    finally:
        conn.close()
    return db


def test_existing_ledger_refuses_0005_at_start(tmp_path: Path) -> None:
    """0005 rebuilds lots, so ledger start never runs it on a ledger with data."""
    db = _ledger_at_0004(tmp_path)
    conn = sqlite3.connect(db)
    try:
        with pytest.raises(LedgerError, match=r"0005_lots_drop_strategy_default.sql"):
            apply_migrations(conn)
    finally:
        conn.close()


def test_migrate_keeps_every_lot_and_drops_the_default(tmp_path: Path) -> None:
    """Through `hermes migrate`: rows are identical, and an insert without strategy_id fails."""
    db = _ledger_at_0004(tmp_path)
    conn = sqlite3.connect(db)
    before = conn.execute(_LOTS_SQL).fetchall()
    conn.close()

    applied, snapshot = migrate_with_snapshot(db, tmp_path / "snaps")

    assert applied == ("0005_lots_drop_strategy_default.sql",)
    assert snapshot is not None
    conn = sqlite3.connect(db)
    try:
        assert conn.execute(_LOTS_SQL).fetchall() == before
        column = {r[1]: r for r in conn.execute("PRAGMA table_info(lots)")}["strategy_id"]
        assert (column[3], column[4]) == (1, None)
        with pytest.raises(sqlite3.IntegrityError, match="strategy_id"):
            conn.execute(
                "INSERT INTO lots (lot_id, asset, original_qty, remaining_qty, opened_at) "
                "VALUES ('L3', 'SOL', '1', '1', '2026-01-04T10:00:00+00:00')"
            )
        indexes = {r[1] for r in conn.execute("PRAGMA index_list(lots)")}
        assert {"idx_lots_asset_opened_at", "idx_lots_trade_id"} <= indexes
    finally:
        conn.close()
