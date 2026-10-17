"""Tests for migration 0006_capital_seeds (change 405-usdt-fifo).

Reference: ADR-016 item 4, ADR-006 §2 (forward-only migrations).
"""

from __future__ import annotations

import shutil
import sqlite3
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path

import pytest

from hermes.adapters.ledger.migration_runner import (
    DESTRUCTIVE_MARKER,
    MIGRATIONS_DIR,
    apply_migrations,
)

_MIGRATION = "0006_capital_seeds.sql"


@pytest.fixture
def at_0004(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """Yield a ledger migrated through 0004 holding one trade-linked lot; closed afterwards."""
    pre = tmp_path / "pre_0006"
    pre.mkdir()
    for path in MIGRATIONS_DIR.glob("*.sql"):
        if path.name < _MIGRATION:
            shutil.copy(path, pre / path.name)
    conn = sqlite3.connect(":memory:")
    apply_migrations(conn, pre)
    conn.execute(
        "INSERT INTO trades (correlation_id, pair_base, pair_quote, side, qty_amount, qty_asset, "
        "price_amount, fee_amount, fee_currency, executed_at) VALUES "
        "('c-1', 'BTC', 'USDT', 'BUY', '1', 'BTC', '60000', '6', 'USDT', "
        "'2026-01-02T10:00:00+00:00')"
    )
    conn.execute(
        "INSERT INTO lots (lot_id, asset, original_qty, remaining_qty, cost_basis_per_unit, "
        "opened_at, strategy_id, trade_id) "
        "VALUES ('L1', 'BTC', '1', '1', '48000', '2026-01-02T10:00:00+00:00', 'swing', 1)"
    )
    conn.commit()
    yield conn
    conn.close()


def _seed_row(conn: sqlite3.Connection, strategy_id: str, lot_id: str) -> None:
    conn.execute(
        "INSERT INTO capital_seeds (strategy_id, asset, amount, seeded_at, lot_id) "
        "VALUES (?, 'USDT', '10000', '2026-10-03T11:45:00+00:00', ?)",
        (strategy_id, lot_id),
    )


def test_0006_is_additive_so_every_ledger_start_applies_it() -> None:
    """Invariant: 0006 carries no destructive marker; apply_migrations runs it unattended."""
    first_line = (MIGRATIONS_DIR / _MIGRATION).read_text(encoding="utf-8").splitlines()[0]
    assert first_line.strip() != DESTRUCTIVE_MARKER


def test_ledger_at_0004_applies_0006_and_keeps_its_lots(
    tmp_path: Path, at_0004: sqlite3.Connection
) -> None:
    """Invariant: upgrading adds capital_seeds and leaves existing lot rows untouched."""
    conn = at_0004
    before = conn.execute("SELECT * FROM lots").fetchall()
    # Pinned to 0006 so later migrations do not change what this run applies.
    through_0006 = tmp_path / "pre_0006"
    shutil.copy(MIGRATIONS_DIR / _MIGRATION, through_0006 / _MIGRATION)

    applied = apply_migrations(conn, through_0006)

    assert (applied, conn.execute("SELECT * FROM lots").fetchall()) == ((_MIGRATION,), before)


def test_seed_rows_start_pending() -> None:
    """Invariant (D-1): a seed row without EUR columns reads eur_status 'pending'."""
    with closing(sqlite3.connect(":memory:")) as conn:
        apply_migrations(conn)
        _seed_row(conn, "swing", "L1")
        assert conn.execute("SELECT eur_status, value_eur FROM capital_seeds").fetchone() == (
            "pending",
            None,
        )


def test_one_seed_per_strategy() -> None:
    """Invariant (A-1.1): the schema refuses a second seed for the same strategy."""
    with closing(sqlite3.connect(":memory:")) as conn:
        apply_migrations(conn)
        _seed_row(conn, "swing", "L1")
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
            _seed_row(conn, "swing", "L2")
