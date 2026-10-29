"""Tests for SqliteLedger's trade rows with EUR values (change trade-eur-values R-2, R-3, D-6).

Reference: ADR-002 §3, ADR-003 §00 I-06, ADR-016 items 3 and 5.
"""

import sqlite3
from collections.abc import Iterator
from contextlib import closing
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from hermes.adapters.ledger.migration_runner import apply_migrations
from hermes.adapters.ledger.sqlite_ledger import SqliteLedger
from hermes.domain.errors import LedgerError
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
from hermes.ports.ledger import PendingTrade
from tests.unit.adapters.ledger.test_sqlite_ledger import CommitCountingConnection

_BTC_USDT = Pair(base=Currency.BTC, quote=Currency.USDT)
_T0 = datetime(2026, 1, 2, 10, 0, tzinfo=UTC)
# 1.25 USD per EUR keeps the expected EUR figures readable.
_PRICING = EurPricing(
    quote=Currency.USDT, rate=Decimal("1.25"), rate_date=date(2026, 1, 2), source="ecb:frankfurter"
)


def _trade(
    side: OrderSide, qty: str, price: str, fee: str, *, minutes: int, priced: bool = True
) -> TradeRecord:
    eur = None
    if priced:
        eur = TradeEur.price(
            _PRICING,
            price=Money(amount=Decimal(price), currency=Currency.USDT),
            fee=Money(amount=Decimal(fee), currency=Currency.USDT),
        )
    return TradeRecord(
        correlation_id=f"c-{minutes}",
        pair=_BTC_USDT,
        side=side,
        qty=Decimal(qty),
        price=Decimal(price),
        fee=Money(amount=Decimal(fee), currency=Currency.USDT),
        executed_at=_T0 + timedelta(minutes=minutes),
        strategy_id=StrategyId("swing"),
        lot_id=LotId.new() if side == OrderSide.BUY else None,
        eur=eur,
    )


def _event(trade: TradeRecord) -> TradeExecuted:
    return TradeExecuted(
        occurred_at=trade.executed_at,
        correlation_id=trade.correlation_id,
        pair=trade.pair,
        side=TradeSide(trade.side.value),
        qty=Quantity(amount=trade.qty, asset=trade.pair.base),
        price=Price(amount=trade.price, pair=trade.pair),
        fee=trade.fee,
        strategy_id=trade.strategy_id,
    )


def _record(ledger: SqliteLedger, trade: TradeRecord) -> int:
    return ledger.record_trade(trade, _event(trade))


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    """Yield a fresh in-memory SQLite connection and close it afterwards."""
    with closing(sqlite3.connect(":memory:")) as connection:
        yield connection


@pytest.fixture
def ledger(conn: sqlite3.Connection) -> SqliteLedger:
    """Return a migrated ledger on ``conn``."""
    return SqliteLedger(conn)


class TestRecordTrade:
    """A trade row carries EUR values or a pending status from insert."""

    def test_priced_trade_stores_eur_values(
        self, ledger: SqliteLedger, conn: sqlite3.Connection
    ) -> None:
        """Invariant (A-2.1): price_eur = price / rate, fee_eur = fee / rate, status ok."""
        trade_id = _record(ledger, _trade(OrderSide.BUY, "0.5", "50000", "25", minutes=0))
        row = conn.execute(
            "SELECT eur_rate, eur_rate_date, eur_source, price_eur, fee_eur, eur_status, "
            "price_amount FROM trades WHERE id = ?",
            (trade_id,),
        ).fetchone()
        assert row == ("1.25", "2026-01-02", "ecb:frankfurter", "40000", "20", "ok", "50000")

    def test_pending_trade_stores_null_eur_values(
        self, ledger: SqliteLedger, conn: sqlite3.Connection
    ) -> None:
        """Invariant (A-2.2): a trade without a rate is stored, pending, with NULL EUR columns."""
        trade_id = _record(
            ledger, _trade(OrderSide.BUY, "0.5", "50000", "25", minutes=0, priced=False)
        )
        row = conn.execute(
            "SELECT eur_rate, price_eur, fee_eur, eur_status FROM trades WHERE id = ?", (trade_id,)
        ).fetchone()
        assert row == (None, None, None, "pending")

    def test_buy_opens_a_linked_lot_with_fee_inclusive_bases(
        self, ledger: SqliteLedger, conn: sqlite3.Connection
    ) -> None:
        """Invariant: the lot's EUR and USDT bases both include the buy fee."""
        trade_id = _record(ledger, _trade(OrderSide.BUY, "0.5", "50000", "25", minutes=0))
        row = conn.execute(
            "SELECT cost_basis_per_unit, cost_basis_native_per_unit, native_currency, "
            "remaining_qty FROM lots WHERE trade_id = ?",
            (trade_id,),
        ).fetchone()
        # EUR: (40000 x 0.5 + 20) / 0.5 = 40040; USDT: (50000 x 0.5 + 25) / 0.5 = 50050.
        assert (Decimal(row[0]), Decimal(row[1]), row[2], Decimal(row[3])) == (
            Decimal("40040"),
            Decimal("50050"),
            "USDT",
            Decimal("0.5"),
        )

    def test_sell_writes_consumption_with_gain_net_of_fees(
        self, ledger: SqliteLedger, conn: sqlite3.Connection
    ) -> None:
        """Invariant (R-4): gain = EUR proceeds - EUR cost incl. buy fee - EUR sell fee."""
        _record(ledger, _trade(OrderSide.BUY, "0.5", "50000", "25", minutes=0))
        sell_id = _record(ledger, _trade(OrderSide.SELL, "0.5", "60000", "30", minutes=1))
        rows = conn.execute(
            "SELECT proceeds_eur, cost_eur, fee_eur, realized_gain_eur FROM lot_consumptions "
            "WHERE trade_id = ?",
            (sell_id,),
        ).fetchall()
        # proceeds 48000 x 0.5 = 24000; cost 20020; fee 24; gain 3956.
        assert [tuple(Decimal(v) for v in row) for row in rows] == [
            (Decimal("24000"), Decimal("20020"), Decimal("24"), Decimal("3956"))
        ]
        assert ledger.list_open_lots(Currency.BTC) == ()

    def test_record_trade_is_one_commit(self) -> None:
        """Invariant (I-06): trade row, lot effects and event commit together, once."""
        with closing(sqlite3.connect(":memory:", factory=CommitCountingConnection)) as conn:
            apply_migrations(conn)
            ledger = SqliteLedger(conn)
            conn.commit_count = 0
            _record(ledger, _trade(OrderSide.BUY, "1", "100", "0", minutes=0))
            assert conn.commit_count == 1

    def test_record_trade_stores_its_further_events_in_the_same_commit(self) -> None:
        """Invariant (#857): events passed with a trade land in its one commit, after its event."""
        with closing(sqlite3.connect(":memory:", factory=CommitCountingConnection)) as conn:
            apply_migrations(conn)
            ledger = SqliteLedger(conn)
            trade = _trade(OrderSide.BUY, "1", "100", "0", minutes=0)
            quality = replace(_event(trade), correlation_id="quality-of-the-buy")
            conn.commit_count = 0
            ledger.record_trade(trade, _event(trade), quality)
            ids = [row[0] for row in conn.execute("SELECT correlation_id FROM events ORDER BY id")]
            assert (conn.commit_count, ids) == (1, [trade.correlation_id, "quality-of-the-buy"])

    def test_a_trade_that_rolls_back_takes_its_further_events_with_it(
        self, ledger: SqliteLedger, conn: sqlite3.Connection
    ) -> None:
        """Invariant (#857): a failed trade stores none of the events passed with it."""
        trade = _trade(OrderSide.SELL, "1", "100", "0", minutes=0)
        quality = replace(_event(trade), correlation_id="quality-of-the-sell")
        with pytest.raises(LedgerError, match="FIFO replay of BTC failed"):
            ledger.record_trade(trade, _event(trade), quality)
        assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0

    def test_sell_beyond_inventory_stores_nothing(
        self, ledger: SqliteLedger, conn: sqlite3.Connection
    ) -> None:
        """Invariant: a SELL with no open quantity rolls back the trade and its event."""
        with pytest.raises(LedgerError, match="FIFO replay of BTC failed"):
            _record(ledger, _trade(OrderSide.SELL, "1", "100", "0", minutes=0))
        counts = (
            conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0],
            conn.execute("SELECT COUNT(*) FROM events").fetchone()[0],
        )
        assert counts == (0, 0)

    def test_open_lot_with_pending_basis_reads_back_with_native_basis(
        self, ledger: SqliteLedger
    ) -> None:
        """Invariant (D-7): a pending lot rehydrates with no EUR basis and its USDT basis."""
        _record(ledger, _trade(OrderSide.BUY, "0.5", "50000", "25", minutes=0, priced=False))
        (lot,) = ledger.list_open_lots(Currency.BTC)
        assert (lot.cost_basis_per_unit, lot.cost_basis_native_per_unit) == (
            None,
            Money(amount=Decimal("50050"), currency=Currency.USDT),
        )


class TestPendingAndBackfill:
    """Pending rows are listed and, once priced, re-project their lots and consumptions."""

    def test_list_pending_trades_returns_only_pending_rows(self, ledger: SqliteLedger) -> None:
        """Invariant: list_pending_trades returns pending rows with their native values."""
        _record(ledger, _trade(OrderSide.BUY, "1", "100", "0", minutes=0))
        pending_id = _record(
            ledger, _trade(OrderSide.BUY, "1", "110", "1", minutes=1, priced=False)
        )
        assert ledger.list_pending_trades() == (
            PendingTrade(
                trade_id=pending_id,
                pair=_BTC_USDT,
                executed_at=_T0 + timedelta(minutes=1),
                price=Decimal("110"),
                fee=Money(amount=Decimal("1"), currency=Currency.USDT),
            ),
        )

    def test_applying_eur_to_a_pending_buy_fills_the_dependent_gain(
        self, ledger: SqliteLedger, conn: sqlite3.Connection
    ) -> None:
        """Invariant (A-3.1): pricing a pending BUY fills the later SELL's gain."""
        pending_buy = _trade(OrderSide.BUY, "1", "125", "1.25", minutes=0, priced=False)
        buy_id = _record(ledger, pending_buy)
        _record(ledger, _trade(OrderSide.SELL, "1", "150", "1.25", minutes=1))
        before = conn.execute("SELECT realized_gain_eur FROM lot_consumptions").fetchall()

        ledger.apply_trade_eur(
            buy_id,
            TradeEur(pricing=_PRICING, price_eur=Decimal("100"), fee_eur=Decimal("1")),
        )

        after = conn.execute("SELECT realized_gain_eur FROM lot_consumptions").fetchone()[0]
        status = conn.execute("SELECT eur_status FROM trades WHERE id = ?", (buy_id,)).fetchone()[0]
        # proceeds 120 - cost 101 - fee 1 = 18.
        assert (before, Decimal(after), status) == ([(None,)], Decimal("18"), "ok")

    def test_apply_trade_eur_to_unknown_trade_fails(self, ledger: SqliteLedger) -> None:
        """Invariant: pricing a trade id that does not exist is an error, not a no-op."""
        with pytest.raises(LedgerError, match="no trade with id 99"):
            ledger.apply_trade_eur(
                99, TradeEur(pricing=_PRICING, price_eur=Decimal("1"), fee_eur=Decimal("0"))
            )
