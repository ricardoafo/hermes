"""Builds ledger files for the console read-model tests through the real ledger API.

Every row is written by :class:`SqliteLedger` (``record_trade``,
``record_portfolio_mark``, ``append_event``, ``record_capital_seed`` and
``apply_seed_eur``), so the read model is tested against the schema and
serialisation ``hermes serve`` actually produces.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from hermes.adapters.ledger.sqlite_ledger import SqliteLedger
from hermes.domain.events.bar_processed import BarProcessed
from hermes.domain.events.shadow_capital_seeded import ShadowCapitalSeeded
from hermes.domain.events.signal_emitted import SignalEmitted
from hermes.domain.events.trade_executed import TradeExecuted, TradeSide
from hermes.domain.fx.eur_pricing import EurPricing
from hermes.domain.lots.quote_leg import CapitalSeed
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

    def seed(
        self, amount: str, at: datetime, *, priced: bool = True, strategy: StrategyId = SWING
    ) -> None:
        """Record the strategy's USDT start capital; price it at :data:`RATE` unless pending."""
        capital = Money(amount=Decimal(amount), currency=Currency.USDT)
        seed_id = self.ledger.record_capital_seed(
            CapitalSeed(strategy_id=strategy, amount=capital, seeded_at=at),
            ShadowCapitalSeeded(
                occurred_at=at, correlation_id=self._cid(), strategy_id=strategy, amount=capital
            ),
        )
        if priced:
            self.ledger.apply_seed_eur(
                seed_id,
                EurPricing(
                    quote=Currency.USDT,
                    rate=RATE,
                    rate_date=at.date(),
                    source="ecb:frankfurter",
                ),
            )

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
        cash: str = "50000",
        starting_cash: str = "100000",
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
            starting_cash=Decimal(starting_cash),
            cash=Decimal(cash),
            positions=positions,
            realized_pnl=Decimal(0),
            peak_equity=Decimal(starting_cash),
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


SEEDED_AT = T0 - timedelta(hours=1)


def home_ledger(path: Path, *, priced: bool = True) -> None:
    """Write the home example: a 5,000 USDT seed, a buy, a partial sale and three marks.

    In USDT: seeded 07:00 UTC with 5,000; 0.02 BTC bought at 50,000 (fee 1) just
    after the 08:00 bar, so cash 3,999 and equity 4,999; marked at 52,000 at
    12:00 (equity 5,039); 0.01 BTC sold at 55,000 (fee 0.55) just after 12:00, so
    cash 4,548.45; marked at 55,000 at 16:00 (equity 5,098.45).

    In EUR (rate 1.25, so 0.8 EUR per USDT): the seed is worth 4,000; the sale
    has proceeds 440, cost 400.40 of which 0.40 is the buy fee, sell fee 0.44, so
    gross 40, fees 0.84, net 39.16. The 0.01 BTC still held has the same net,
    39.16. With ``priced=False`` neither the seed nor the fills have EUR values.
    """
    b = LedgerBuilder(path)
    try:
        b.seed("5000", SEEDED_AT, priced=priced)
        b.bar(T0)
        b.entry_signal(T0 - _ONE_MS, stop="48000", atr="1000", close="50000")
        bought_at = T0 + timedelta(seconds=5)
        b.trade(OrderSide.BUY, "0.02", "50000", "1", bought_at, priced=priced)
        b.mark(
            T0,
            (Held(BTC, "0.02", "1000", "50000", bought_at),),
            cash="3999",
            starting_cash="5000",
        )
        b.bar(T0 + H4)
        b.mark(
            T0 + H4,
            (Held(BTC, "0.02", "1000", "52000", T0 + H4 - _ONE_MS),),
            cash="3999",
            starting_cash="5000",
        )
        sold_at = T0 + H4 + timedelta(seconds=5)
        b.trade(OrderSide.SELL, "0.01", "55000", "0.55", sold_at, priced=priced)
        b.bar(T0 + H4 * 2)
        b.mark(
            T0 + H4 * 2,
            (Held(BTC, "0.01", "500", "55000", T0 + H4 * 2 - _ONE_MS),),
            cash="4548.45",
            starting_cash="5000",
        )
    finally:
        b.close()
