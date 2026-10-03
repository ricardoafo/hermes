"""Tests for backfill_pending_eur (change trade-eur-values R-3).

Reference: ADR-016 item 5.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime
from decimal import Decimal

from hermes.adapters.ledger.sqlite_ledger import SqliteLedger
from hermes.application.ledger.eur_backfill import BackfillReport, backfill_pending_eur
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
_SAT = date(2026, 1, 3)
_MON = date(2026, 1, 5)


def _fill(side: OrderSide, price: str, fee: str, at: datetime) -> Fill:
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
        timestamp=at,
    )


def _ledger_with_pending_round_trip() -> tuple[SqliteLedger, sqlite3.Connection]:
    """Record a Saturday buy and sell during an FX outage, leaving both rows pending."""
    conn = sqlite3.connect(":memory:")
    ledger = SqliteLedger(conn)
    recorder = TradeRecorder(ledger, EurPricer(FakeFx(down=True)))
    recorder.record(
        _fill(OrderSide.BUY, "125", "1.25", datetime(2026, 1, 3, 10, tzinfo=UTC)),
        correlation_id="b",
    )
    recorder.record(
        _fill(OrderSide.SELL, "150", "1.25", datetime(2026, 1, 3, 11, tzinfo=UTC)),
        correlation_id="s",
    )
    return ledger, conn


def test_backfill_prices_pending_rows_and_fills_the_gain() -> None:
    """Invariant (A-3.1): after backfill both rows are ok and the consumption has its EUR gain."""
    ledger, conn = _ledger_with_pending_round_trip()
    fx = FakeFx(rates={_SAT: (Decimal("1.25"), _FRI)})

    report = backfill_pending_eur(ledger, EurPricer(fx), today=_MON)

    statuses = conn.execute("SELECT eur_status, eur_rate_date FROM trades ORDER BY id").fetchall()
    (gain,) = conn.execute("SELECT realized_gain_eur FROM lot_consumptions").fetchone()
    assert (report, statuses, Decimal(gain)) == (
        BackfillReport(filled=(1, 2), pending=()),
        [("ok", "2026-01-02"), ("ok", "2026-01-02")],
        Decimal("18"),
    )
    assert len(fx.calls) == 1


def test_backfill_during_an_outage_leaves_rows_pending() -> None:
    """Invariant (A-3.2): a provider failure keeps rows pending and does not raise."""
    ledger, conn = _ledger_with_pending_round_trip()
    report = backfill_pending_eur(ledger, EurPricer(FakeFx(down=True)), today=_MON)
    assert report == BackfillReport(filled=(), pending=(1, 2))
    assert conn.execute("SELECT COUNT(*) FROM trades WHERE eur_status = 'pending'").fetchone() == (
        2,
    )


def test_backfill_on_the_fill_day_waits_for_the_days_rate() -> None:
    """Invariant (D-2): before the fill date has passed, a prior-day rate is not accepted."""
    ledger, _conn = _ledger_with_pending_round_trip()
    fx = FakeFx(rates={_SAT: (Decimal("1.25"), _FRI)})
    report = backfill_pending_eur(ledger, EurPricer(fx), today=_SAT)
    assert report == BackfillReport(filled=(), pending=(1, 2))
