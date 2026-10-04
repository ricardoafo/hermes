"""Builds ledger files for the console read-model tests through the real ledger API.

Every row is written by :class:`SqliteLedger` (``record_trade``,
``record_portfolio_mark``, ``append_event``), so the read model is tested
against the schema and serialisation ``hermes serve`` actually produces.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from hermes.adapters.ledger.sqlite_ledger import SqliteLedger
from hermes.domain.events.bar_processed import BarProcessed
from hermes.domain.events.signal_emitted import SignalEmitted
from hermes.domain.events.trade_executed import TradeExecuted, TradeSide
from hermes.domain.fx.eur_pricing import EurPricing
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.portfolio.mark import PortfolioMark
from hermes.domain.portfolio.position import Position
from hermes.domain.portfolio.snapshot import PortfolioSnapshot
from hermes.domain.strategy.signal import SignalKind, SignalSide
from hermes.domain.trading.order import OrderSide
from hermes.domain.trading.trade_record import TradeEur, TradeRecord
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.lot_id import LotId
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.price import Price
from hermes.domain.value_objects.quantity import Quantity
from hermes.domain.value_objects.strategy_id import StrategyId

BTC = Pair(base=Currency.BTC, quote=Currency.USDT)
ETH = Pair(base=Currency.ETH, quote=Currency.USDT)
SWING = StrategyId("swing-donchian-4h")
T0 = datetime(2026, 10, 1, 8, tzinfo=UTC)
H4 = timedelta(hours=4)
# 1.25 USDT per EUR keeps the EUR figures exact: eur_per_usdt is 0.8.
RATE = Decimal("1.25")
_ONE_MS = timedelta(milliseconds=1)


@dataclass(frozen=True, slots=True)
class Held:
    """One position inside a portfolio mark."""

    pair: Pair
    quantity: str
    cost_basis: str
    mark_price: str
    marked_at: datetime


class LedgerBuilder:
    """Writes trades, signals, bar checks and marks to a ledger file."""

    def __init__(self, path: Path) -> None:
        """Create (and migrate) the ledger at ``path``."""
        self._conn = sqlite3.connect(path)
        self.ledger = SqliteLedger(self._conn)
        self._seq = 0

    def close(self) -> None:
        """Close the writer connection."""
        self._conn.close()

    def _cid(self) -> str:
        self._seq += 1
        return f"c-{self._seq}"

    def trade(
        self,
        side: OrderSide,
        qty: str,
        price: str,
        fee: str,
        at: datetime,
        *,
        pair: Pair = BTC,
        priced: bool = True,
        strategy: StrategyId = SWING,
    ) -> int:
        """Record one fill, priced at :data:`RATE` or pending."""
        fee_money = Money(amount=Decimal(fee), currency=pair.quote)
        eur = None
        if priced:
            pricing = EurPricing(
                quote=pair.quote, rate=RATE, rate_date=date(2026, 10, 1), source="ecb:frankfurter"
            )
            eur = TradeEur.price(
                pricing, price=Money(amount=Decimal(price), currency=pair.quote), fee=fee_money
            )
        record = TradeRecord(
            correlation_id=self._cid(),
            pair=pair,
            side=side,
            qty=Decimal(qty),
            price=Decimal(price),
            fee=fee_money,
            executed_at=at,
            strategy_id=strategy,
            lot_id=LotId.new() if side == OrderSide.BUY else None,
            eur=eur,
        )
        event = TradeExecuted(
            occurred_at=at,
            correlation_id=record.correlation_id,
            pair=pair,
            side=TradeSide(side.value),
            qty=Quantity(amount=record.qty, asset=pair.base),
            price=Price(amount=record.price, pair=pair),
            fee=fee_money,
            strategy_id=strategy,
        )
        return self.ledger.record_trade(record, event)

    def entry_signal(
        self,
        at: datetime,
        *,
        stop: str,
        atr: str,
        close: str,
        pair: Pair = BTC,
        strategy: StrategyId = SWING,
    ) -> None:
        """Record the entry signal that opened a position."""
        self.ledger.append_event(
            SignalEmitted(
                occurred_at=at,
                correlation_id=self._cid(),
                strategy_id=strategy,
                pair=pair,
                kind=SignalKind.ENTRY,
                side=SignalSide.LONG,
                entry_price=Decimal(close),
                stop_price=Decimal(stop),
                atr_context=Decimal(atr),
            )
        )

    def bar(self, boundary: datetime, *, pair: Pair = BTC) -> None:
        """Record a 4h bar check closing at ``boundary`` (stored as boundary - 1 ms)."""
        self.ledger.append_event(
            BarProcessed(
                occurred_at=boundary - _ONE_MS,
                correlation_id=self._cid(),
                pair=pair,
                interval=KlineInterval.H4,
            )
        )

    def mark(
        self,
        boundary: datetime,
        held: tuple[Held, ...],
        *,
        pair: Pair = BTC,
        strategy: StrategyId = SWING,
    ) -> None:
        """Record the portfolio after the 4h bar of ``pair`` closing at ``boundary``."""
        positions = {
            h.pair: Position(
                pair=h.pair,
                quantity=Decimal(h.quantity),
                cost_basis=Decimal(h.cost_basis),
                mark_price=Decimal(h.mark_price),
                marked_at=h.marked_at,
            )
            for h in held
        }
        snapshot = PortfolioSnapshot(
            strategy_id=strategy,
            quote=Currency.USDT,
            starting_cash=Decimal(100000),
            cash=Decimal(50000),
            positions=positions,
            realized_pnl=Decimal(0),
            peak_equity=Decimal(100000),
        )
        self.ledger.record_portfolio_mark(
            PortfolioMark(
                snapshot=snapshot,
                pair=pair,
                interval=KlineInterval.H4,
                bar_close_time=boundary - _ONE_MS,
            )
        )


def one_btc_position(path: Path, *, priced: bool = True) -> None:
    """Write the worked example: 0.5 BTC bought at 50,000 USDT, marked at 52,000.

    In EUR (rate 1.25): entry 40,000, fee 20, now 41,600; size 0.5, so cost
    20,020, now worth 20,800, gross 800, fees to close 20.80, net 759.20. The
    stop is the entry signal's 48,000 with ATR 1,000; marks go 50,000, 53,000,
    52,000, so the high since entry is 53,000.
    """
    b = LedgerBuilder(path)
    try:
        b.bar(T0)
        b.entry_signal(T0 - _ONE_MS, stop="48000", atr="1000", close="50000")
        filled_at = T0 + timedelta(seconds=5)
        b.trade(OrderSide.BUY, "0.5", "50000", "25", filled_at, priced=priced)
        for i, mark in enumerate(("50000", "53000", "52000")):
            boundary = T0 + H4 * i
            if i:
                b.bar(boundary)
            marked_at = filled_at if i == 0 else boundary - _ONE_MS
            b.mark(boundary, (Held(BTC, "0.5", "25000", mark, marked_at),))
    finally:
        b.close()
