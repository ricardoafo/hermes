"""Tests for backfill_pending_eur (change trade-eur-values R-3).

Reference: ADR-016 item 5.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import closing
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from hermes.adapters.ledger.sqlite_ledger import SqliteLedger
from hermes.application.ledger.eur_backfill import BackfillReport, backfill_pending_eur
from hermes.application.ledger.eur_pricer import EurPricer
from hermes.application.ledger.trade_recorder import TradeRecorder
from hermes.domain.events.shadow_capital_seeded import ShadowCapitalSeeded
from hermes.domain.lots.quote_leg import CapitalSeed
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


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    """Yield a fresh in-memory SQLite connection and close it afterwards."""
    with closing(sqlite3.connect(":memory:")) as connection:
        yield connection


def _ledger_with_pending_round_trip(conn: sqlite3.Connection) -> SqliteLedger:
    """Record a Saturday buy and sell during an FX outage, leaving both rows pending."""
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
    return ledger


def test_backfill_prices_pending_rows_and_fills_the_gain(conn: sqlite3.Connection) -> None:
    """Invariant (A-3.1): after backfill both rows are ok and the consumption has its EUR gain."""
    ledger = _ledger_with_pending_round_trip(conn)
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


def test_backfill_during_an_outage_leaves_rows_pending(conn: sqlite3.Connection) -> None:
    """Invariant (A-3.2): a provider failure keeps rows pending and does not raise."""
    ledger = _ledger_with_pending_round_trip(conn)
    report = backfill_pending_eur(ledger, EurPricer(FakeFx(down=True)), today=_MON)
    assert report == BackfillReport(filled=(), pending=(1, 2))
    assert conn.execute("SELECT COUNT(*) FROM trades WHERE eur_status = 'pending'").fetchone() == (
        2,
    )


def test_backfill_on_the_fill_day_waits_for_the_days_rate(conn: sqlite3.Connection) -> None:
    """Invariant (D-2): before the fill date has passed, a prior-day rate is not accepted."""
    ledger = _ledger_with_pending_round_trip(conn)
    fx = FakeFx(rates={_SAT: (Decimal("1.25"), _FRI)})
    report = backfill_pending_eur(ledger, EurPricer(fx), today=_SAT)
    assert report == BackfillReport(filled=(), pending=(1, 2))


def _seeded_ledger_with_pending_buy(conn: sqlite3.Connection) -> SqliteLedger:
    """Seed 1000 USDT on Saturday, then record a pending Saturday BUY of 125 + 1.25 USDT."""
    ledger = SqliteLedger(conn)
    seed = CapitalSeed(
        strategy_id=StrategyId("swing"),
        amount=Money(amount=Decimal("1000"), currency=Currency.USDT),
        seeded_at=datetime(2026, 1, 3, 9, tzinfo=UTC),
    )
    ledger.record_capital_seed(
        seed,
        ShadowCapitalSeeded(
            occurred_at=seed.seeded_at,
            correlation_id="seed",
            strategy_id=seed.strategy_id,
            amount=seed.amount,
        ),
    )
    TradeRecorder(ledger, EurPricer(FakeFx(down=True))).record(
        _fill(OrderSide.BUY, "125", "1.25", datetime(2026, 1, 3, 10, tzinfo=UTC)),
        correlation_id="b",
    )
    return ledger


def test_backfill_prices_a_pending_seed_by_its_seed_date(conn: sqlite3.Connection) -> None:
    """Invariant (405 D-1): the seed is priced with the rate of its Madrid seed date."""
    ledger = _seeded_ledger_with_pending_buy(conn)
    fx = FakeFx(rates={_SAT: (Decimal("1.25"), _FRI)})

    report = backfill_pending_eur(ledger, EurPricer(fx), today=_MON)

    assert (report.seeds_filled, report.seeds_pending) == ((1,), ())
    assert conn.execute("SELECT eur_status, value_eur FROM capital_seeds").fetchone()[0] == "ok"


def test_backfill_values_the_quote_leg_once_seed_and_trade_are_priced(
    conn: sqlite3.Connection,
) -> None:
    """Invariant: after backfill the BUY's USDT consumption has its EUR gain (0 at one rate)."""
    ledger = _seeded_ledger_with_pending_buy(conn)
    fx = FakeFx(rates={_SAT: (Decimal("1.25"), _FRI)})

    backfill_pending_eur(ledger, EurPricer(fx), today=_MON)

    rows = conn.execute(
        "SELECT consumed_qty, realized_gain_eur FROM lot_consumptions WHERE consumed_asset = 'USDT'"
    ).fetchall()
    assert [(Decimal(qty), Decimal(gain)) for qty, gain in rows] == [(Decimal("126.25"), 0)]


def test_backfill_during_an_outage_leaves_the_seed_pending(conn: sqlite3.Connection) -> None:
    """Invariant: a provider failure keeps the seed pending and does not raise."""
    ledger = _seeded_ledger_with_pending_buy(conn)
    report = backfill_pending_eur(ledger, EurPricer(FakeFx(down=True)), today=_MON)
    assert (report.seeds_filled, report.seeds_pending) == ((), (1,))
