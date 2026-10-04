"""Tests for migration 0004_trade_eur_values (change trade-eur-values R-1).

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


_PRE_0004 = (
    "0001_initial_schema.sql",
    "0002_lots_add_strategy_id.sql",
    "0003_live_bars.sql",
)
_LOTS_SQL = (
    "SELECT lot_id, asset, original_qty, remaining_qty, cost_basis_per_unit, opened_at, "
    "strategy_id FROM lots ORDER BY lot_id"
)
_CONSUMPTIONS_SQL = (
    "SELECT id, lot_id, consumed_qty, consumed_asset, realized_gain_eur, consumed_at "
    "FROM lot_consumptions ORDER BY id"
)
_ALL_ROWS_SQL = {
    "trades": "SELECT * FROM trades ORDER BY 1",
    "lots": "SELECT * FROM lots ORDER BY 1",
    "lot_consumptions": "SELECT * FROM lot_consumptions ORDER BY 1",
    "schema_migrations": "SELECT * FROM schema_migrations ORDER BY 1",
}


def _through_0004(tmp_path: Path) -> Path:
    """Return a migrations directory ending at 0004, so these tests pin the 0004 step alone."""
    through = tmp_path / "through_0004"
    if not through.exists():
        through.mkdir()
        for name in (*_PRE_0004, "0004_trade_eur_values.sql"):
            shutil.copy(MIGRATIONS_DIR / name, through / name)
    return through


def _seeded_at_0003(tmp_path: Path) -> sqlite3.Connection:
    """Return a ledger migrated through 0003_live_bars holding representative legacy rows."""
    pre = tmp_path / "pre_0004"
    pre.mkdir()
    for name in _PRE_0004:
        shutil.copy(MIGRATIONS_DIR / name, pre / name)
    conn = sqlite3.connect(":memory:")
    assert apply_migrations(conn, pre) == _PRE_0004
    conn.execute(
        "INSERT INTO trades (correlation_id, pair_base, pair_quote, side, qty_amount, qty_asset, "
        "price_amount, fee_amount, fee_currency, executed_at) VALUES "
        "('c-1', 'BTC', 'USDT', 'BUY', '1', 'BTC', '60000', '6', 'USDT', "
        "'2026-01-02T10:00:00+00:00')"
    )
    conn.executemany(
        "INSERT INTO lots (lot_id, asset, original_qty, remaining_qty, cost_basis_per_unit, "
        "opened_at, strategy_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            ("L1", "BTC", "1", "0.5", "60000", "2026-01-02T10:00:00+00:00", "swing"),
            ("L2", "ETH", "10", "10", "2500.12345678", "2026-01-03T10:00:00+00:00", "trend"),
            ("L3", "BTC", "0.25", "0", "61000", "2026-01-04T10:00:00+00:00", "swing"),
        ],
    )
    # Written without strategy_id: relies on the legacy DEFAULT 'swing' from 0002.
    conn.execute(
        "INSERT INTO lots (lot_id, asset, original_qty, remaining_qty, cost_basis_per_unit, "
        "opened_at) VALUES ('L4', 'SOL', '5', '5', '150', '2026-01-05T10:00:00+00:00')"
    )
    conn.executemany(
        "INSERT INTO lot_consumptions (lot_id, consumed_qty, consumed_asset, realized_gain_eur, "
        "consumed_at) VALUES (?, ?, ?, ?, ?)",
        [
            ("L1", "0.5", "BTC", "100", "2026-01-03T10:00:00+00:00"),
            ("L3", "0.25", "BTC", "-12.5", "2026-01-06T10:00:00+00:00"),
            ("L3", "0", "BTC", "0", "2026-01-07T10:00:00+00:00"),
        ],
    )
    conn.commit()
    return conn


def _schema_snapshot(conn: sqlite3.Connection) -> list[tuple[str, str, str | None]]:
    """Return ``(type, name, sql)`` for every schema object, sorted for comparison."""
    rows: list[tuple[str, str, str | None]] = [
        (row[0], row[1], row[2])
        for row in conn.execute(
            "SELECT type, name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
        ).fetchall()
    ]
    return sorted(rows, key=lambda r: (r[0], r[1]))


def _indexes(conn: sqlite3.Connection, table: str) -> set[str]:
    """Return the names of the explicitly created indexes on ``table``."""
    return {
        row[1] for row in conn.execute(f"PRAGMA index_list({table})").fetchall() if row[3] == "c"
    }


def _foreign_keys(conn: sqlite3.Connection, table: str) -> set[tuple[str, str, str]]:
    """Return ``(parent_table, from_column, to_column)`` for each FK of ``table``."""
    return {
        (row[2], row[3], row[4])
        for row in conn.execute(f"PRAGMA foreign_key_list({table})").fetchall()
    }


def test_0004_sorts_after_0003_live_bars_in_a_fresh_ledger() -> None:
    """Invariant: the runner orders by filename, so 0004 applies after 0003_live_bars."""
    conn = sqlite3.connect(":memory:")

    applied = apply_migrations(conn)

    assert applied[:4] == (
        "0001_initial_schema.sql",
        "0002_lots_add_strategy_id.sql",
        "0003_live_bars.sql",
        "0004_trade_eur_values.sql",
    )


def test_ledger_migrated_through_0003_live_bars_upgrades_cleanly_to_0004(tmp_path: Path) -> None:
    """Invariant: a ledger already at 0003_live_bars applies 0004 next and keeps live_bars."""
    conn = _seeded_at_0003(tmp_path)
    conn.execute(
        "INSERT INTO live_bars VALUES ('BTC', 'USDT', '1h', 't0', 't1', '1', '2', '0.5', '1.5', "
        "'10', 'r')"
    )
    conn.commit()

    applied = apply_migrations(conn, _through_0004(tmp_path))

    assert applied[0] == "0004_trade_eur_values.sql"
    assert conn.execute(
        "SELECT filename FROM schema_migrations ORDER BY filename LIMIT 4"
    ).fetchall() == [
        ("0001_initial_schema.sql",),
        ("0002_lots_add_strategy_id.sql",),
        ("0003_live_bars.sql",),
        ("0004_trade_eur_values.sql",),
    ]
    assert conn.execute("SELECT pair_base, close FROM live_bars").fetchall() == [("BTC", "1.5")]


def test_rows_written_before_0004_survive_it(tmp_path: Path) -> None:
    """Invariant (A-1.4): old trades read pending; old lots and consumptions have no trade link."""
    conn = _seeded_at_0003(tmp_path)

    apply_migrations(conn, _through_0004(tmp_path))

    assert conn.execute("SELECT correlation_id, eur_status, price_eur FROM trades").fetchall() == [
        ("c-1", "pending", None)
    ]
    assert conn.execute("SELECT lot_id, trade_id FROM lots ORDER BY lot_id").fetchall() == [
        ("L1", None),
        ("L2", None),
        ("L3", None),
        ("L4", None),
    ]
    assert conn.execute("SELECT id, trade_id FROM lot_consumptions ORDER BY id").fetchall() == [
        (1, None),
        (2, None),
        (3, None),
    ]


def test_rebuild_preserves_every_lot_and_consumption_value(tmp_path: Path) -> None:
    """Invariant: the lots / lot_consumptions rebuild loses no row and changes no original value.

    Includes lots.strategy_id: explicit values ('swing', 'trend') and the legacy DEFAULT
    ('swing') applied to a row written without it.
    """
    conn = _seeded_at_0003(tmp_path)
    lots_before = conn.execute(_LOTS_SQL).fetchall()
    consumptions_before = conn.execute(_CONSUMPTIONS_SQL).fetchall()

    apply_migrations(conn, _through_0004(tmp_path))

    lots_after = conn.execute(_LOTS_SQL).fetchall()
    consumptions_after = conn.execute(_CONSUMPTIONS_SQL).fetchall()
    assert lots_after == lots_before
    assert consumptions_after == consumptions_before
    assert lots_after == [
        ("L1", "BTC", "1", "0.5", "60000", "2026-01-02T10:00:00+00:00", "swing"),
        ("L2", "ETH", "10", "10", "2500.12345678", "2026-01-03T10:00:00+00:00", "trend"),
        ("L3", "BTC", "0.25", "0", "61000", "2026-01-04T10:00:00+00:00", "swing"),
        ("L4", "SOL", "5", "5", "150", "2026-01-05T10:00:00+00:00", "swing"),
    ]
    assert consumptions_after[1] == (2, "L3", "0.25", "BTC", "-12.5", "2026-01-06T10:00:00+00:00")


def test_strategy_id_keeps_not_null_and_legacy_default_after_0004(tmp_path: Path) -> None:
    """Invariant: 0004 leaves lots.strategy_id NOT NULL DEFAULT 'swing' (dropped later by 0005)."""
    conn = _seeded_at_0003(tmp_path)

    apply_migrations(conn, _through_0004(tmp_path))

    assert _columns(conn, "lots")["strategy_id"] == (True, "'swing'")


def test_second_migration_run_is_a_noop(tmp_path: Path) -> None:
    """Invariant: re-running the runner after 0004 applies nothing and changes nothing."""
    conn = _seeded_at_0003(tmp_path)
    apply_migrations(conn, _through_0004(tmp_path))
    schema_before = _schema_snapshot(conn)
    rows_before = {t: conn.execute(q).fetchall() for t, q in _ALL_ROWS_SQL.items()}

    second = apply_migrations(conn, _through_0004(tmp_path))

    assert second == ()
    assert _schema_snapshot(conn) == schema_before
    rows_after = {t: conn.execute(q).fetchall() for t, q in _ALL_ROWS_SQL.items()}
    assert rows_after == rows_before


def test_indexes_and_foreign_keys_present_before_0004_still_exist_after(tmp_path: Path) -> None:
    """Invariant: every index and foreign key of lots / lot_consumptions survives the rebuild."""
    conn = _seeded_at_0003(tmp_path)
    lots_indexes_before = _indexes(conn, "lots")
    consumption_indexes_before = _indexes(conn, "lot_consumptions")
    consumption_fks_before = _foreign_keys(conn, "lot_consumptions")
    assert lots_indexes_before == {"idx_lots_asset_opened_at"}
    assert consumption_fks_before == {("lots", "lot_id", "lot_id")}

    apply_migrations(conn, _through_0004(tmp_path))

    assert lots_indexes_before <= _indexes(conn, "lots")
    assert consumption_indexes_before <= _indexes(conn, "lot_consumptions")
    assert consumption_fks_before <= _foreign_keys(conn, "lot_consumptions")
    assert ("trades", "trade_id", "id") in _foreign_keys(conn, "lots")
    assert ("trades", "trade_id", "id") in _foreign_keys(conn, "lot_consumptions")
