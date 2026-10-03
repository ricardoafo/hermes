"""Tests for SqliteLedger.list_eur_trades (change tax-estimates, port TaxTradeSourcePort).

Reference: ADR-016 decision 3; change trade-eur-values D-6 (the rows the FIFO replays).
"""

from __future__ import annotations

import sqlite3
from datetime import timedelta
from decimal import Decimal

import pytest

from hermes.adapters.ledger.sqlite_ledger import SqliteLedger
from hermes.domain.errors import LedgerError
from hermes.domain.trading.order import OrderSide
from hermes.domain.value_objects.currency import Currency
from hermes.ports.ledger import EurTradeRow, TaxTradeSourcePort
from tests.unit.adapters.ledger.test_sqlite_trade_ledger import _T0, _record, _trade


def test_lists_priced_and_pending_trades_in_fill_order() -> None:
    """Each row carries its EUR values, or None while pending, oldest first."""
    ledger = SqliteLedger(sqlite3.connect(":memory:"))
    buy_id = _record(ledger, _trade(OrderSide.BUY, "1", "125", "1.25", minutes=0))
    _record(ledger, _trade(OrderSide.SELL, "1", "150", "1.25", minutes=10, priced=False))
    rows = ledger.list_eur_trades()
    assert rows == (
        EurTradeRow(
            trade_id=buy_id,
            asset=Currency.BTC,
            side=OrderSide.BUY,
            qty=Decimal("1"),
            executed_at=_T0,
            price_eur=Decimal("100"),
            fee_eur=Decimal("1"),
        ),
        EurTradeRow(
            trade_id=buy_id + 1,
            asset=Currency.BTC,
            side=OrderSide.SELL,
            qty=Decimal("1"),
            executed_at=_T0 + timedelta(minutes=10),
            price_eur=None,
            fee_eur=None,
        ),
    )


def test_sqlite_ledger_satisfies_the_tax_source_port() -> None:
    """The adapter is usable wherever the tax service expects the port."""
    assert isinstance(SqliteLedger(sqlite3.connect(":memory:")), TaxTradeSourcePort)


def test_read_failure_raises_ledger_error() -> None:
    """A closed connection surfaces as LedgerError, never a raw sqlite error."""
    connection = sqlite3.connect(":memory:")
    ledger = SqliteLedger(connection)
    connection.close()
    with pytest.raises(LedgerError, match="list_eur_trades failed"):
        ledger.list_eur_trades()
