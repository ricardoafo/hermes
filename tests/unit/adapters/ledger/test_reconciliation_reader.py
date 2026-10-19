"""Tests for the read-only reconciliation reader (change 35-monthly-reconciliation D-1).

Invariants protected here:

- the connection is read-only and a missing file is reported, not created;
- trades come back with their native and EUR values, oldest fill first;
- the lot table is summed per asset from its remaining quantities;
- every TradeExecuted event is rehydrated, whatever its strategy;
- a ledger without the EUR columns fails naming ``hermes migrate``.
"""

import sqlite3
from decimal import Decimal
from pathlib import Path

import pytest

from hermes.adapters.ledger.reconciliation_reader import SqliteReconciliationReader
from hermes.adapters.ledger.sqlite_ledger import SqliteLedger
from hermes.domain.errors import LedgerError
from hermes.domain.trading.order import OrderSide
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.strategy_id import StrategyId
from hermes.ports.reconciliation import ReconciliationReadPort
from tests.support.reconciliation_ledger import (
    SEPT_BUY_AT,
    SEPT_SELL_AT,
    record,
    write_september,
)


def test_reader_satisfies_the_port() -> None:
    """The adapter is a structural ReconciliationReadPort."""
    assert issubclass(SqliteReconciliationReader, ReconciliationReadPort)


def test_a_write_through_the_reader_connection_fails(tmp_path: Path) -> None:
    """mode=ro: reconciliation can never change the ledger it reports on."""
    path = write_september(tmp_path / "hermes.sqlite3")

    with SqliteReconciliationReader.open(path) as reader, pytest.raises(sqlite3.OperationalError):
        reader._conn.execute("DELETE FROM trades")


def test_opening_a_missing_ledger_raises_and_creates_nothing(tmp_path: Path) -> None:
    """A wrong --db path must not leave an empty SQLite file behind."""
    path = tmp_path / "absent.sqlite3"

    with pytest.raises(FileNotFoundError):
        SqliteReconciliationReader.open(path)
    assert not path.exists()


def test_trades_carry_native_and_eur_values_oldest_first(tmp_path: Path) -> None:
    """The BUY comes first, with price 60000 USDT and EUR price 48000 (rate 1.25)."""
    path = write_september(tmp_path / "hermes.sqlite3")

    with SqliteReconciliationReader.open(path) as reader:
        buy, sell = reader.trades()

    assert (buy.side, buy.executed_at, buy.price, buy.price_eur, buy.fee_eur) == (
        OrderSide.BUY,
        SEPT_BUY_AT,
        Decimal(60000),
        Decimal(48000),
        Decimal("0.48"),
    )
    assert (sell.side, sell.executed_at, sell.qty) == (
        OrderSide.SELL,
        SEPT_SELL_AT,
        Decimal("0.004"),
    )


def test_pending_trades_read_with_no_eur_values(tmp_path: Path) -> None:
    """A trade awaiting its rate reads as pending, not as zero EUR."""
    path = write_september(tmp_path / "hermes.sqlite3", priced=False)

    with SqliteReconciliationReader.open(path) as reader:
        trades = reader.trades()

    assert [t.eur_pending for t in trades] == [True, True]


def test_open_lot_quantities_sum_remaining_per_asset(tmp_path: Path) -> None:
    """After BUY 0.01 and SELL 0.004 the lot table holds 0.006 BTC."""
    path = write_september(tmp_path / "hermes.sqlite3")

    with SqliteReconciliationReader.open(path) as reader:
        assert reader.open_lot_quantities() == {Currency.BTC: Decimal("0.006")}


def test_trade_events_cover_every_strategy(tmp_path: Path) -> None:
    """Events of a strategy the roster no longer seeds are still read."""
    path = tmp_path / "hermes.sqlite3"
    connection = sqlite3.connect(path)
    ledger = SqliteLedger(connection)
    record(ledger, OrderSide.BUY, "1", "10", "0", SEPT_BUY_AT, strategy=StrategyId("a"))
    record(ledger, OrderSide.BUY, "1", "10", "0", SEPT_SELL_AT, strategy=StrategyId("b"))
    connection.close()

    with SqliteReconciliationReader.open(path) as reader:
        events = reader.trade_events()

    assert [e.strategy_id.value for e in events] == ["a", "b"]


def test_an_unmigrated_ledger_fails_naming_migrate(tmp_path: Path) -> None:
    """A trades table without EUR columns is a LedgerError that says how to fix it."""
    path = tmp_path / "old.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE trades (id INTEGER PRIMARY KEY)")
    connection.commit()
    connection.close()

    with SqliteReconciliationReader.open(path) as reader, pytest.raises(LedgerError) as caught:
        reader.trades()
    assert "hermes migrate" in caught.value.msg


def test_an_unknown_event_schema_version_fails(tmp_path: Path) -> None:
    """A TradeExecuted row of another version is refused, not misread."""
    path = write_september(tmp_path / "hermes.sqlite3")
    connection = sqlite3.connect(path)
    connection.execute(
        "UPDATE events SET event_schema_version = 99 WHERE event_type = 'TradeExecuted'"
    )
    connection.commit()
    connection.close()

    with SqliteReconciliationReader.open(path) as reader, pytest.raises(LedgerError) as caught:
        reader.trade_events()
    assert "schema version 99" in caught.value.msg
