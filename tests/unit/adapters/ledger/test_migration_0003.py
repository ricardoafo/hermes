"""Tests for migration 0003_trade_eur_values (change trade-eur-values R-1).

Reference: ADR-002 §3 (EUR values on every trade row), ADR-016 item 3.
"""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest

from hermes.adapters.ledger.migration_runner import MIGRATIONS_DIR, apply_migrations


def _columns(conn: sqlite3.Connection, table: str) -> dict[str, tuple[bool, str | None]]:
    """Return ``{column: (not_null, default)}`` for ``table``."""
    return {
        row[1]: (bool(row[3]), row[4])
        for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }


@pytest.fixture
def migrated() -> sqlite3.Connection:
    """Return an in-memory ledger with every shipped migration applied."""
    conn = sqlite3.connect(":memory:")
    apply_migrations(conn)
    return conn


def test_trades_gain_eur_columns_with_pending_default(migrated: sqlite3.Connection) -> None:
    """Invariant (A-1.1): trades carry EUR columns; eur_status is NOT NULL, default pending."""
    cols = _columns(migrated, "trades")
    assert {
        name: cols[name]
        for name in (
            "eur_rate",
            "eur_rate_date",
            "eur_source",
            "price_eur",
            "fee_eur",
            "eur_status",
        )
    } == {
        "eur_rate": (False, None),
        "eur_rate_date": (False, None),
        "eur_source": (False, None),
        "price_eur": (False, None),
        "fee_eur": (False, None),
        "eur_status": (True, "'pending'"),
    }


def test_eur_status_rejects_other_values(migrated: sqlite3.Connection) -> None:
    """Invariant (A-1.1): eur_status is 'ok' or 'pending', nothing else."""
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        migrated.execute(
            "INSERT INTO trades (correlation_id, pair_base, pair_quote, side, qty_amount, "
            "qty_asset, price_amount, fee_amount, fee_currency, executed_at, eur_status) "
            "VALUES ('c', 'BTC', 'USDT', 'BUY', '1', 'BTC', '1', '0', 'USDT', "
            "'2026-01-02T00:00:00+00:00', 'done')"
        )


def test_lot_eur_basis_is_nullable_and_native_columns_exist(migrated: sqlite3.Connection) -> None:
    """Invariant (A-1.2): a lot may wait for its EUR basis; it keeps native basis and trade link."""
    cols = _columns(migrated, "lots")
    assert {
        name: cols[name][0]
        for name in (
            "cost_basis_per_unit",
            "cost_basis_native_per_unit",
            "native_currency",
            "trade_id",
        )
    } == {
        "cost_basis_per_unit": False,
        "cost_basis_native_per_unit": False,
        "native_currency": False,
        "trade_id": False,
    }


def test_consumption_gain_is_nullable_and_eur_parts_exist(migrated: sqlite3.Connection) -> None:
    """Invariant (A-1.3): consumptions hold proceeds, cost and fee in EUR; gain may be pending."""
    cols = _columns(migrated, "lot_consumptions")
    assert {
        name: cols[name][0]
        for name in ("realized_gain_eur", "proceeds_eur", "cost_eur", "fee_eur", "trade_id")
    } == {
        "realized_gain_eur": False,
        "proceeds_eur": False,
        "cost_eur": False,
        "fee_eur": False,
        "trade_id": False,
    }


def test_rows_written_before_0003_survive_it(tmp_path: Path) -> None:
    """Invariant (A-1.4): old lot, consumption and trade rows survive; old trades read pending."""
    first_two = tmp_path / "first_two"
    first_two.mkdir()
    for name in ("0001_initial_schema.sql", "0002_lots_add_strategy_id.sql"):
        shutil.copy(MIGRATIONS_DIR / name, first_two / name)
    conn = sqlite3.connect(":memory:")
    apply_migrations(conn, first_two)
    conn.execute(
        "INSERT INTO trades (correlation_id, pair_base, pair_quote, side, qty_amount, qty_asset, "
        "price_amount, fee_amount, fee_currency, executed_at) VALUES "
        "('c-1', 'BTC', 'USDT', 'BUY', '1', 'BTC', '60000', '6', 'USDT', "
        "'2026-01-02T10:00:00+00:00')"
    )
    conn.execute(
        "INSERT INTO lots (lot_id, asset, original_qty, remaining_qty, cost_basis_per_unit, "
        "opened_at, strategy_id) VALUES ('L1', 'BTC', '1', '0.5', '60000', "
        "'2026-01-02T10:00:00+00:00', 'swing')"
    )
    conn.execute(
        "INSERT INTO lot_consumptions (lot_id, consumed_qty, consumed_asset, realized_gain_eur, "
        "consumed_at) VALUES ('L1', '0.5', 'BTC', '100', '2026-01-03T10:00:00+00:00')"
    )
    conn.commit()

    applied = apply_migrations(conn)

    assert applied == ("0003_trade_eur_values.sql",)
    assert conn.execute(
        "SELECT lot_id, remaining_qty, cost_basis_per_unit, strategy_id, trade_id FROM lots"
    ).fetchall() == [("L1", "0.5", "60000", "swing", None)]
    assert conn.execute(
        "SELECT id, lot_id, consumed_qty, realized_gain_eur, trade_id FROM lot_consumptions"
    ).fetchall() == [(1, "L1", "0.5", "100", None)]
    assert conn.execute("SELECT correlation_id, eur_status, price_eur FROM trades").fetchall() == [
        ("c-1", "pending", None)
    ]
