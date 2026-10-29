"""Read-only SQLite adapter for :class:`~hermes.ports.reconciliation.ReconciliationReadPort`.

Opens the ledger with SQLite's ``mode=ro`` URI, so the connection cannot write
even by accident, and never runs migrations. Safe next to a live
``hermes serve``. A ledger older than migration 0004 has no EUR columns; the
read fails with a :class:`~hermes.domain.errors.LedgerError` that names
``hermes migrate``.

Reference: openspec/archive/35-monthly-reconciliation/change.md D-1.
"""

import json
import sqlite3
from decimal import Decimal, localcontext

from hermes.adapters.ledger._cells import decimal_cell, optional_decimal_cell, time_cell
from hermes.adapters.ledger.connection import ReadOnlyLedgerReader
from hermes.adapters.ledger.sqlite_ledger import _trade_from_payload
from hermes.domain.errors import LedgerError
from hermes.domain.events.trade_executed import TradeExecuted
from hermes.domain.reconciliation.holdings import LedgerTrade
from hermes.domain.trading.order import OrderSide
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.precision import EXACT_CONTEXT

__all__ = ["SqliteReconciliationReader"]


class SqliteReconciliationReader(ReadOnlyLedgerReader):
    """Answer the reconciliation's ledger questions, read-only."""

    def trades(self) -> tuple[LedgerTrade, ...]:
        """Return every trades-table row, oldest fill first.

        Raises:
            LedgerError: On any read failure (including a ledger not migrated
                to 0004) or an unreadable row.
        """
        try:
            rows = self._conn.execute(
                "SELECT id, pair_base, pair_quote, side, qty_amount, price_amount, "
                "       fee_amount, fee_currency, executed_at, price_eur, fee_eur "
                "FROM trades"
            ).fetchall()
        except sqlite3.Error as exc:
            raise LedgerError(
                msg=f"cannot read trades: {exc} (is the ledger migrated? run `hermes migrate`)"
            ) from exc
        try:
            trades = [_row_to_trade(row) for row in rows]
        except (ValueError, TypeError) as exc:
            raise LedgerError(msg=f"unreadable trade row: {exc}") from exc
        return tuple(sorted(trades, key=lambda t: (t.executed_at, t.trade_id)))

    def open_lot_quantities(self) -> dict[Currency, Decimal]:
        """Return the lots table's remaining quantity summed per asset.

        Raises:
            LedgerError: On any read failure or an unreadable row.
        """
        try:
            rows = self._conn.execute("SELECT asset, remaining_qty FROM lots").fetchall()
        except sqlite3.Error as exc:
            raise LedgerError(msg=f"cannot read lots: {exc}") from exc
        totals: dict[Currency, Decimal] = {}
        try:
            with localcontext(EXACT_CONTEXT):
                for asset, remaining in rows:
                    ccy = Currency(asset)
                    totals[ccy] = totals.get(ccy, Decimal("0")) + decimal_cell(remaining)
        except (ValueError, ArithmeticError) as exc:
            raise LedgerError(msg=f"unreadable lot row: {exc}") from exc
        return totals

    def trade_events(self) -> tuple[TradeExecuted, ...]:
        """Return every ``TradeExecuted`` event, oldest first.

        Raises:
            LedgerError: On any read failure, a schema version this code does
                not read, or an unreadable payload.
        """
        try:
            rows = self._conn.execute(
                "SELECT event_schema_version, payload_json FROM events "
                "WHERE event_type = 'TradeExecuted' ORDER BY occurred_at ASC, id ASC"
            ).fetchall()
        except sqlite3.Error as exc:
            raise LedgerError(msg=f"cannot read trade events: {exc}") from exc
        events: list[TradeExecuted] = []
        for version, payload_json in rows:
            if version != TradeExecuted.__event_schema_version__:
                raise LedgerError(msg=f"unsupported TradeExecuted schema version {version}")
            try:
                events.append(_trade_from_payload(json.loads(payload_json)))
            except (KeyError, ValueError, TypeError) as exc:
                raise LedgerError(msg=f"unreadable TradeExecuted payload: {exc}") from exc
        return tuple(events)


def _row_to_trade(row: tuple[object, ...]) -> LedgerTrade:
    trade_id, base, quote, side, qty, price, fee, fee_ccy, executed_at, price_eur, fee_eur = row
    return LedgerTrade(
        trade_id=int(str(trade_id)),
        pair=Pair(base=Currency(str(base)), quote=Currency(str(quote))),
        side=OrderSide(str(side)),
        qty=decimal_cell(qty),
        price=decimal_cell(price),
        fee=Money(amount=decimal_cell(fee), currency=Currency(str(fee_ccy))),
        executed_at=time_cell(executed_at),
        price_eur=optional_decimal_cell(price_eur),
        fee_eur=optional_decimal_cell(fee_eur),
    )
