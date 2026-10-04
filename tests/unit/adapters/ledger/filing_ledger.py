"""Synthetic ledgers for the tax-filing tests. No real figures.

Builds a ledger file through the production write path (``TradeRecorder`` on
``SqliteLedger``), so the filing reader is tested against the projection the
ledger really stores. The rate is 1.25 USD per EUR on every day, so a USDT
amount divided by 1.25 is its EUR value.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Final

from hermes.adapters.ledger.sqlite_ledger import SqliteLedger
from hermes.application.ledger.eur_pricer import EurPricer
from hermes.application.ledger.trade_recorder import TradeRecorder
from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.trading.fill import Fill
from hermes.domain.trading.order import Order, OrderSide
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.quantity import Quantity
from hermes.domain.value_objects.strategy_id import StrategyId
from tests.unit.application.ledger.fake_fx import FakeFx

__all__ = [
    "BTC_USDT",
    "BUY1_AT",
    "BUY2_AT",
    "RATE",
    "SELL_AT",
    "constant_fx",
    "record_close",
    "record_fill",
    "standard_ledger",
]

RATE: Final[Decimal] = Decimal("1.25")
BTC_USDT: Final[Pair] = Pair(base=Currency.BTC, quote=Currency.USDT)
BUY1_AT: Final[datetime] = datetime(2026, 3, 2, 10, tzinfo=UTC)
BUY2_AT: Final[datetime] = datetime(2026, 4, 1, 10, tzinfo=UTC)
SELL_AT: Final[datetime] = datetime(2026, 6, 1, 10, tzinfo=UTC)


def constant_fx(start: date = date(2025, 1, 1), days: int = 800) -> FakeFx:
    """Return a fake FX answering :data:`RATE` on every day from ``start``."""
    return FakeFx(
        rates={start + timedelta(days=i): (RATE, start + timedelta(days=i)) for i in range(days)}
    )


def record_fill(
    conn: sqlite3.Connection,
    side: OrderSide,
    qty: str,
    price: str,
    fee: str,
    at: datetime,
    *,
    pair: Pair = BTC_USDT,
    fx: FakeFx | None = None,
) -> int:
    """Record one fill through the production write path; return its trade id."""
    recorder = TradeRecorder(SqliteLedger(conn), EurPricer(fx if fx is not None else constant_fx()))
    return recorder.record(
        Fill(
            order=Order(
                side=side,
                pair=pair,
                qty=Quantity(amount=Decimal(qty), asset=pair.base),
                strategy_id=StrategyId("swing"),
            ),
            executed_qty=Decimal(qty),
            avg_price=Decimal(price),
            fee=Money(amount=Decimal(fee), currency=pair.quote),
            timestamp=at,
        ),
        correlation_id=f"c-{side.value}-{at.isoformat()}",
    )


def record_close(conn: sqlite3.Connection, close_time: datetime, close: str) -> None:
    """Store one 4h bar of BTC/USDT closing at ``close_time``."""
    open_time = close_time - timedelta(hours=4) + timedelta(milliseconds=1)
    SqliteLedger(conn).record_live_bar(
        Bar(
            pair=BTC_USDT,
            interval=KlineInterval.H4,
            open_time=open_time,
            close_time=close_time,
            open=Decimal(close),
            high=Decimal(close),
            low=Decimal(close),
            close=Decimal(close),
            volume=Decimal("1"),
        )
    )


def standard_ledger(path: Path) -> None:
    """Write the standard scenario to ``path``.

    Two BTC buys (1 @ 50,000 + 50 fee; 1 @ 60,000 + 60 fee, USDT), one sale of
    1.5 BTC @ 70,000 with a 105 USDT fee, and a 31 December close of 80,000.
    In EUR at 1.25: the sale transmits 83,916, cost 64,064, gain 19,852; 0.5
    BTC is held at year end, valued 32,000.
    """
    conn = sqlite3.connect(path)
    try:
        record_fill(conn, OrderSide.BUY, "1", "50000", "50", BUY1_AT)
        record_fill(conn, OrderSide.BUY, "1", "60000", "60", BUY2_AT)
        record_fill(conn, OrderSide.SELL, "1.5", "70000", "105", SELL_AT)
        record_close(conn, datetime(2026, 12, 31, 19, 59, 59, tzinfo=UTC), "80000")
        record_close(conn, datetime(2026, 12, 31, 23, 59, 59, tzinfo=UTC), "99999")
    finally:
        conn.close()
