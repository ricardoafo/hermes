"""Write ledgers for the reconciliation tests through the real SqliteLedger.

Every trade goes through ``record_trade``, so the trades table, the lot table
and the ``TradeExecuted`` event agree exactly as they do in production; tests
that need drift tamper with one table afterwards.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from hermes.adapters.ledger.sqlite_ledger import SqliteLedger
from hermes.domain.events.trade_executed import TradeExecuted, TradeSide
from hermes.domain.fx.eur_pricing import EurPricing
from hermes.domain.trading.order import OrderSide
from hermes.domain.trading.trade_record import TradeEur, TradeRecord
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.lot_id import LotId
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.price import Price
from hermes.domain.value_objects.quantity import Quantity
from hermes.domain.value_objects.strategy_id import StrategyId

BTC_USDT = Pair(base=Currency.BTC, quote=Currency.USDT)
STRATEGY = StrategyId("swing-donchian-4h")
SEPT_BUY_AT = datetime(2026, 9, 3, 10, tzinfo=UTC)
SEPT_SELL_AT = datetime(2026, 9, 20, 10, tzinfo=UTC)
# 1.25 USDT per EUR keeps the EUR figures readable.
_PRICING = EurPricing(
    quote=Currency.USDT, rate=Decimal("1.25"), rate_date=date(2026, 9, 2), source="ecb:test"
)


def record(
    ledger: SqliteLedger,
    side: OrderSide,
    qty: str,
    price: str,
    fee: str,
    at: datetime,
    *,
    priced: bool = True,
    strategy: StrategyId = STRATEGY,
) -> None:
    """Record one BTC/USDT fill, its lot effect and its event."""
    native_price = Money(amount=Decimal(price), currency=Currency.USDT)
    native_fee = Money(amount=Decimal(fee), currency=Currency.USDT)
    trade = TradeRecord(
        correlation_id=f"c-{at.isoformat()}-{side.value}",
        pair=BTC_USDT,
        side=side,
        qty=Decimal(qty),
        price=Decimal(price),
        fee=native_fee,
        executed_at=at,
        strategy_id=strategy,
        lot_id=LotId.new() if side == OrderSide.BUY else None,
        eur=TradeEur.price(_PRICING, price=native_price, fee=native_fee) if priced else None,
    )
    event = TradeExecuted(
        occurred_at=at,
        correlation_id=trade.correlation_id,
        pair=BTC_USDT,
        side=TradeSide(side.value),
        qty=Quantity(amount=trade.qty, asset=Currency.BTC),
        price=Price(amount=trade.price, pair=BTC_USDT),
        fee=native_fee,
        strategy_id=strategy,
    )
    ledger.record_trade(trade, event)


def write_september(path: Path, *, priced: bool = True) -> Path:
    """Write a ledger with September's BUY 0.01 at 60000 and SELL 0.004 at 65000.

    From 5000 USDT the book ends September with 0.006 BTC and 4659.14 USDT;
    in EUR the sale realizes 15.6 (proceeds 208, cost 192.192, fee 0.208).
    """
    connection = sqlite3.connect(path)
    ledger = SqliteLedger(connection)
    record(ledger, OrderSide.BUY, "0.01", "60000", "0.6", SEPT_BUY_AT, priced=priced)
    record(ledger, OrderSide.SELL, "0.004", "65000", "0.26", SEPT_SELL_AT, priced=priced)
    connection.close()
    return path
