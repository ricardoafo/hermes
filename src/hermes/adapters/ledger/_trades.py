"""Trades, lots and consumptions: the tax-lot tables."""

import sqlite3
from datetime import datetime
from decimal import Decimal

from returns.result import Failure

from hermes.adapters.ledger._projection import _LotProjection
from hermes.adapters.ledger.codecs import (
    eur_columns,
    eur_row,
)
from hermes.domain.errors import LedgerError
from hermes.domain.events.base import DomainEvent
from hermes.domain.fx.eur_pricing import EurStatus
from hermes.domain.lots.lot import Lot
from hermes.domain.lots.lot_consumption import LotConsumption
from hermes.domain.lots.quote_leg import (
    base_leg_trade,
    quote_leg_trades,
)
from hermes.domain.trading.order import OrderSide
from hermes.domain.trading.trade_record import TradeEur, TradeRecord
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.pair import Pair
from hermes.ports.ledger import EstimatedFee, EurTradeRow, PendingTrade


class _TradeStore(_LotProjection):
    """Trades, lots and consumptions: the tax-lot tables."""

    def open_lot(self, lot: Lot, event: DomainEvent) -> None:
        """Insert ``lot`` and append ``event`` inside a single transaction.

        The two INSERTs share ONE explicit ``BEGIN…COMMIT`` boundary. On
        error the transaction is rolled back so neither row lands in the
        ledger (invariant I-06).

        Args:
            lot: Freshly-opened :class:`Lot` (``remaining == original``).
            event: The BUY-side :class:`TradeExecuted` that opened it.

        Raises:
            LedgerError: On any SQLite failure.
        """
        with self._write("open_lot"):
            self._insert_lot(lot)
            self._insert_event(event)

    def consume_lot(
        self,
        consumptions: tuple[LotConsumption, ...],
        updated_lots: tuple[Lot, ...],
        event: DomainEvent,
    ) -> None:
        """Persist consumptions + updated remainders + event atomically.

        Args:
            consumptions: Ordered per :func:`select_lots_fifo` output.
            updated_lots: Same order — lot with reduced ``remaining_qty``.
            event: The SELL-side :class:`TradeExecuted` driving the draw.

        Raises:
            LedgerError: On any SQLite failure or when the arg lengths
                disagree (a programmer bug that must not silently pass).
        """
        if len(consumptions) != len(updated_lots):
            raise LedgerError(
                msg=(
                    f"consume_lot: consumptions ({len(consumptions)}) and "
                    f"updated_lots ({len(updated_lots)}) must have equal length"
                )
            )
        with self._write("consume_lot"):
            for consumption, updated in zip(consumptions, updated_lots, strict=True):
                self._insert_consumption(consumption)
                self._update_lot_remaining(updated)
            self._insert_event(event)

    def record_trade(self, trade: TradeRecord, event: DomainEvent, *also: DomainEvent) -> int:
        """Store a trade, its lot effects and its events in one transaction.

        A BUY inserts the lot it opens, linked to the trade; once the quote
        currency has a seed, a SELL inserts the quote-currency lot it opens.
        Both legs are then re-projected so lot remainders, consumptions and
        every EUR value follow from the trades (I-06 holds: one
        ``BEGIN…COMMIT``).

        Args:
            trade: The trade. ``trade.eur is None`` stores it pending.
            event: The ``TradeExecuted`` event for the trade.
            *also: Further events about the same fill, stored after ``event``.

        Returns:
            The new trade row id.

        Raises:
            LedgerError: On any SQLite failure or a SELL beyond the open
                quantity; the transaction is rolled back.
        """
        with self._write("record_trade"):
            trade_id = self._insert_trade(trade)
            if trade.fee_estimated:
                self._conn.execute("INSERT INTO estimated_fees (trade_id) VALUES (?)", (trade_id,))
            if trade.lot_id is not None:
                self._insert_trade_lot(trade, trade_id)
            elif self._has_seed(trade.pair.quote):
                self._insert_quote_lot(trade_id, trade.strategy_id.value)
            self._reproject(trade.pair.base)
            self._reproject_quote(trade.pair.quote)
            self._insert_event(event)
            for extra in also:
                self._insert_event(extra)
        return trade_id

    def list_estimated_fees(self) -> tuple[EstimatedFee, ...]:
        """Return every trade whose fee is an estimate, oldest first (issue #1014).

        Raises:
            LedgerError: On any SQLite read failure.
        """
        try:
            rows = self._conn.execute(
                "SELECT t.id, t.pair_base, t.pair_quote, t.side, t.executed_at, "
                "       t.fee_amount, t.fee_currency "
                "FROM estimated_fees AS e JOIN trades AS t ON t.id = e.trade_id "
                "ORDER BY t.executed_at, t.id"
            ).fetchall()
        except sqlite3.Error as exc:
            raise LedgerError(msg=f"list_estimated_fees failed: {exc}") from exc
        return tuple(
            EstimatedFee(
                trade_id=int(row[0]),
                pair=Pair(base=Currency(row[1]), quote=Currency(row[2])),
                side=OrderSide(row[3]),
                executed_at=datetime.fromisoformat(row[4]),
                fee=Money(amount=Decimal(row[5]), currency=Currency(row[6])),
            )
            for row in rows
        )

    def list_pending_trades(self) -> tuple[PendingTrade, ...]:
        """Return every trade with ``eur_status = 'pending'``, oldest first.

        Returns:
            Pending trades sorted by fill time, then row id.

        Raises:
            LedgerError: On any SQLite read failure.
        """
        try:
            rows = self._conn.execute(
                "SELECT id, pair_base, pair_quote, executed_at, price_amount, "
                "       fee_amount, fee_currency "
                "FROM trades WHERE eur_status = ?",
                (EurStatus.PENDING.value,),
            ).fetchall()
        except sqlite3.Error as exc:
            raise LedgerError(msg=f"list_pending_trades failed: {exc}") from exc
        pending = [
            PendingTrade(
                trade_id=int(row[0]),
                pair=Pair(base=Currency(row[1]), quote=Currency(row[2])),
                executed_at=datetime.fromisoformat(row[3]),
                price=Decimal(row[4]),
                fee=Money(amount=Decimal(row[5]), currency=Currency(row[6])),
            )
            for row in rows
        ]
        return tuple(sorted(pending, key=lambda p: (p.executed_at, p.trade_id)))

    def apply_trade_eur(self, trade_id: int, eur: TradeEur) -> None:
        """Store a trade's EUR values, mark it ``ok`` and re-project both legs.

        Args:
            trade_id: Row id of the trade.
            eur: The EUR values.

        Raises:
            LedgerError: On any SQLite failure or an unknown ``trade_id``;
                the transaction is rolled back.
        """
        with self._write("apply_trade_eur"):
            row = self._conn.execute(
                "SELECT pair_base, pair_quote FROM trades WHERE id = ?", (trade_id,)
            ).fetchone()
            if row is None:
                raise LedgerError(msg=f"apply_trade_eur: no trade with id {trade_id}")
            self._conn.execute(
                "UPDATE trades SET eur_rate = ?, eur_rate_date = ?, eur_source = ?, "
                "       price_eur = ?, fee_eur = ?, eur_status = ? "
                "WHERE id = ?",
                (*eur_columns(eur), EurStatus.OK.value, trade_id),
            )
            self._reproject(Currency(row[0]))
            self._reproject_quote(Currency(row[1]))

    def list_open_lots(self, asset: Currency) -> tuple[Lot, ...]:
        """Return every open lot for ``asset``, FIFO-ordered.

        Args:
            asset: Base currency filter.

        Returns:
            Tuple of :class:`Lot` sorted by ``opened_at`` asc, then
            ``lot_id`` asc — the shape :func:`select_lots_fifo` expects.

        Raises:
            LedgerError: On any SQLite read failure.
        """
        try:
            rows = self._conn.execute(
                "SELECT lot_id, asset, original_qty, remaining_qty, "
                "       cost_basis_per_unit, strategy_id, opened_at, "
                "       cost_basis_native_per_unit, native_currency "
                "FROM lots "
                "WHERE asset = ? AND CAST(remaining_qty AS REAL) > 0 "
                "ORDER BY opened_at ASC, lot_id ASC",
                (asset.value,),
            ).fetchall()
        except sqlite3.Error as exc:
            raise LedgerError(msg=f"list_open_lots failed: {exc}") from exc

        return tuple(self._row_to_lot(row) for row in rows)

    def list_eur_trades(self) -> tuple[EurTradeRow, ...]:
        """Return every row the FIFO projection replays, with its EUR values.

        The same sequences :meth:`_reproject` and :meth:`_reproject_quote`
        replay: per base asset its lot-opening BUYs and SELLs (net of a base
        fee), and per seeded quote currency its seed and the quote leg of every
        trade quoted in it (change ``405-usdt-fifo``).

        Returns:
            Each asset's rows in replay order.

        Raises:
            LedgerError: On any SQLite read failure or a trade without a quote leg.
        """
        try:
            bases = [
                Currency(row[0])
                for row in self._conn.execute(
                    "SELECT DISTINCT pair_base FROM trades ORDER BY pair_base"
                ).fetchall()
            ]
            quotes = [
                Currency(row[0])
                for row in self._conn.execute(
                    "SELECT DISTINCT asset FROM capital_seeds ORDER BY asset"
                ).fetchall()
            ]
            rows: list[EurTradeRow] = []
            for base in bases:
                legs = self._leg_trades("base", base.value)
                replay = sorted(
                    (base_leg_trade(leg) for leg in legs),
                    key=lambda t: (t.executed_at, int(t.ref)),
                )
                rows.extend(eur_row(base, trade, quote_leg=False) for trade in replay)
            for quote in quotes:
                derived = quote_leg_trades(
                    quote, self._seeds_of(quote), self._leg_trades("quote", quote.value)
                )
                if isinstance(derived, Failure):
                    raise LedgerError(msg=f"quote leg of {quote} failed: {derived.failure()}")
                rows.extend(eur_row(quote, trade, quote_leg=True) for trade in derived.unwrap())
        except sqlite3.Error as exc:
            raise LedgerError(msg=f"list_eur_trades failed: {exc}") from exc
        return tuple(rows)
