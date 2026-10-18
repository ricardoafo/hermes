"""Tests for TradeRecorder (change trade-eur-values R-2).

Reference: ADR-002 §3, ADR-016 items 3 and 5.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import closing
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from hermes.adapters.ledger.sqlite_ledger import SqliteLedger
from hermes.application.ledger.eur_pricer import EurPricer
from hermes.application.ledger.trade_recorder import TradeRecorder
from hermes.domain.trading.fill import Fill
from hermes.domain.trading.order import Order, OrderSide
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.quantity import Quantity
from hermes.domain.value_objects.strategy_id import StrategyId
from tests.unit.application.ledger.fake_fx import FakeFx

_BTC_USDT = Pair(base=Currency.BTC, quote=Currency.USDT)
_FRI = date(2026, 1, 2)
# 16:30 Madrid time, after the ECB publishes.
_AT = datetime(2026, 1, 2, 15, 30, tzinfo=UTC)


def _fill(side: OrderSide, price: str, fee: str) -> Fill:
    return Fill(
        order=Order(
            side=side,
            pair=_BTC_USDT,
            qty=Quantity(amount=Decimal("1"), asset=Currency.BTC),
            strategy_id=StrategyId("swing"),
        ),
        executed_qty=Decimal("1"),
        avg_price=Decimal(price),
        fee=Money(amount=Decimal(fee), currency=Currency.USDT),
        timestamp=_AT,
    )


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    """Yield a fresh in-memory SQLite connection and close it afterwards."""
    with closing(sqlite3.connect(":memory:")) as connection:
        yield connection


def _setup(fx: FakeFx, conn: sqlite3.Connection) -> TradeRecorder:
    return TradeRecorder(SqliteLedger(conn), EurPricer(fx))


def test_fill_with_rate_is_stored_in_eur(conn: sqlite3.Connection) -> None:
    """Invariant (A-2.1): price_eur = price / rate with the ECB rate of the fill date."""
    recorder = _setup(FakeFx(rates={_FRI: (Decimal("1.25"), _FRI)}), conn)
    trade_id = recorder.record(_fill(OrderSide.BUY, "50000", "50"), correlation_id="c-1")
    row = conn.execute(
        "SELECT eur_status, eur_rate, price_eur, fee_eur FROM trades WHERE id = ?", (trade_id,)
    ).fetchone()
    assert row == ("ok", "1.25", "40000", "40")


def test_fx_outage_stores_the_fill_pending_and_does_not_raise(conn: sqlite3.Connection) -> None:
    """Invariant (A-2.2): with FX down the fill is still recorded, pending, with its event."""
    recorder = _setup(FakeFx(down=True), conn)
    trade_id = recorder.record(_fill(OrderSide.BUY, "50000", "50"), correlation_id="c-2")
    row = conn.execute(
        "SELECT eur_status, price_eur, price_amount FROM trades WHERE id = ?", (trade_id,)
    ).fetchone()
    events = conn.execute("SELECT event_type, correlation_id FROM events").fetchall()
    assert (row, events) == (("pending", None, "50000"), [("TradeExecuted", "c-2")])


def test_round_trip_gain_includes_both_fees(conn: sqlite3.Connection) -> None:
    """Invariant (R-4): buy 125 fee 1.25, sell 150 fee 1.25 at 1.25 -> gain 120 - 101 - 1 = 18."""
    recorder = _setup(FakeFx(rates={_FRI: (Decimal("1.25"), _FRI)}), conn)
    recorder.record(_fill(OrderSide.BUY, "125", "1.25"), correlation_id="c-3")
    recorder.record(_fill(OrderSide.SELL, "150", "1.25"), correlation_id="c-4")
    (gain,) = conn.execute("SELECT realized_gain_eur FROM lot_consumptions").fetchone()
    assert Decimal(gain) == Decimal("18")


def test_an_estimated_fee_reaches_the_ledger_marked(conn: sqlite3.Connection) -> None:
    """A fill whose fee is an estimate is listed by the ledger for correction (#1014).

    A fill with an exact fee next to it is not listed.
    """
    recorder = _setup(FakeFx(rates={_FRI: (Decimal("1.25"), _FRI)}), conn)
    exact = recorder.record(_fill(OrderSide.BUY, "40000", "40"), correlation_id="exact")
    estimated = recorder.record(
        replace(_fill(OrderSide.BUY, "40000", "40"), fee_estimated=True), correlation_id="guess"
    )

    listed = SqliteLedger(conn).list_estimated_fees()

    assert ([fee.trade_id for fee in listed], exact != estimated) == ([estimated], True)
