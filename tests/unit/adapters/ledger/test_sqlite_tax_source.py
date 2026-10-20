"""Tests for SqliteLedger.list_eur_trades (change tax-estimates, port TaxTradeSourcePort).

Reference: ADR-016 decision 3; change trade-eur-values D-6 (the rows the FIFO replays).
"""

import sqlite3
from collections.abc import Iterator
from datetime import timedelta
from decimal import Decimal

import pytest

from hermes.adapters.ledger.sqlite_ledger import SqliteLedger
from hermes.domain.errors import LedgerError
from hermes.domain.trading.order import OrderSide
from hermes.domain.value_objects.currency import Currency
from hermes.ports.ledger import EurTradeRow
from tests.unit.adapters.ledger.test_sqlite_capital_seed import _pricing, _record_seed
from tests.unit.adapters.ledger.test_sqlite_capital_seed import _record as _record_usdt
from tests.unit.adapters.ledger.test_sqlite_capital_seed import _trade as _usdt_trade
from tests.unit.adapters.ledger.test_sqlite_trade_ledger import _T0, _record, _trade


@pytest.fixture
def connection() -> Iterator[sqlite3.Connection]:
    """Yield an in-memory connection, closed after the test."""
    conn = sqlite3.connect(":memory:")
    yield conn
    conn.close()


def test_lists_priced_and_pending_trades_in_fill_order(connection: sqlite3.Connection) -> None:
    """Each row carries its EUR values, or None while pending, oldest first."""
    ledger = SqliteLedger(connection)
    buy_id = _record(ledger, _trade(OrderSide.BUY, "1", "125", "1.25", minutes=0))
    _record(ledger, _trade(OrderSide.SELL, "1", "150", "1.25", minutes=10, priced=False))
    rows = ledger.list_eur_trades()
    assert rows == (
        EurTradeRow(
            ref=str(buy_id),
            asset=Currency.BTC,
            side=OrderSide.BUY,
            qty=Decimal("1"),
            executed_at=_T0,
            price_eur=Decimal("100"),
            fee_eur=Decimal("1"),
        ),
        EurTradeRow(
            ref=str(buy_id + 1),
            asset=Currency.BTC,
            side=OrderSide.SELL,
            qty=Decimal("1"),
            executed_at=_T0 + timedelta(minutes=10),
            price_eur=None,
            fee_eur=None,
        ),
    )


def test_read_failure_raises_ledger_error() -> None:
    """A closed connection surfaces as LedgerError, never a raw sqlite error."""
    connection = sqlite3.connect(":memory:")
    ledger = SqliteLedger(connection)
    connection.close()
    with pytest.raises(LedgerError, match="list_eur_trades failed"):
        ledger.list_eur_trades()


def test_seeded_quote_currency_adds_its_leg(connection: sqlite3.Connection) -> None:
    """With a USDT seed the rows hold the USDT replay too: the seed and each trade's quote leg.

    BUY 1 BTC for 500 USDT disposes of 500 USDT (a USDT SELL); SELL 1 BTC for 600
    acquires 600 USDT (a USDT BUY). Each USDT unit is worth 1/1.25 = 0.8 EUR.
    """
    ledger = SqliteLedger(connection)
    seed_id = _record_seed(ledger)
    ledger.apply_seed_eur(seed_id, _pricing("1.25"))
    buy_id = _record_usdt(ledger, _usdt_trade(OrderSide.BUY, "1", "500", "0", minutes=1))
    sell_id = _record_usdt(ledger, _usdt_trade(OrderSide.SELL, "1", "600", "0", minutes=2))
    usdt = [
        (row.ref, row.side, row.qty, row.price_eur)
        for row in ledger.list_eur_trades()
        if row.asset == Currency.USDT
    ]
    unit = Decimal("0.8")
    assert usdt == [
        (f"seed:{seed_id}", OrderSide.BUY, Decimal("1000"), unit),
        (str(buy_id), OrderSide.SELL, Decimal("500"), unit),
        (str(sell_id), OrderSide.BUY, Decimal("600"), unit),
    ]
    assert {row.quote_leg for row in ledger.list_eur_trades() if row.asset == Currency.BTC} == {
        False
    }


def test_base_fee_on_a_buy_lists_the_net_quantity(connection: sqlite3.Connection) -> None:
    """A BUY fee taken in BTC leaves the lot net of it, so the replay row is net too (D-2)."""
    ledger = SqliteLedger(connection)
    _record_usdt(
        ledger,
        _usdt_trade(OrderSide.BUY, "1", "500", "0.001", minutes=1, fee_currency=Currency.BTC),
    )
    (row,) = ledger.list_eur_trades()
    assert (row.asset, row.qty) == (Currency.BTC, Decimal("0.999"))
