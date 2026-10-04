"""Read-only SQLite adapter for :class:`~hermes.ports.reconciliation.ReconciliationReadPort`.

Opens the ledger with SQLite's ``mode=ro`` URI, so the connection cannot write
even by accident, and never runs migrations. Safe next to a live
``hermes serve``. A ledger older than migration 0004 has no EUR columns; the
read fails with a :class:`~hermes.domain.errors.LedgerError` that names
``hermes migrate``.

Reference: openspec/changes/35-monthly-reconciliation/change.md D-1.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path
from typing import Self

from hermes.adapters.ledger.sqlite_ledger import _trade_from_payload
from hermes.domain.errors import LedgerError
from hermes.domain.events.trade_executed import TradeExecuted
from hermes.domain.portfolio.precision import EXACT_CONTEXT
from hermes.domain.reconciliation.holdings import LedgerTrade
from hermes.domain.trading.order import OrderSide
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.pair import Pair

__all__ = ["SqliteReconciliationReader"]


class SqliteReconciliationReader:
    """Answer the reconciliation's ledger questions, read-only."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        """Wrap an already-open connection.

        Args:
            connection: A connection opened by :meth:`open` (or a test's own).
        """
        self._conn = connection

    @classmethod
    def open(cls, path: Path) -> Self:
        """Open ``path`` read-only.

        Raises:
            FileNotFoundError: There is no file at ``path``.
            LedgerError: SQLite refused to open the file.
        """
        resolved = path.resolve()
        if not resolved.is_file():
            raise FileNotFoundError(str(path))
        try:
            connection = sqlite3.connect(f"{resolved.as_uri()}?mode=ro", uri=True)
        except sqlite3.Error as exc:
            raise LedgerError(msg=f"cannot open {path} read-only: {exc}") from exc
        return cls(connection)

    def close(self) -> None:
        """Close the connection."""
        self._conn.close()

    def __enter__(self) -> Self:
        """Return ``self`` for ``with`` blocks."""
        return self

    def __exit__(self, *_exc_info: object) -> None:
        """Close the connection, whatever ended the ``with`` block."""
        self.close()

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
        except (ValueError, InvalidOperation) as exc:
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
                    totals[ccy] = totals.get(ccy, Decimal("0")) + Decimal(remaining)
        except (ValueError, InvalidOperation) as exc:
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
        qty=Decimal(str(qty)),
        price=Decimal(str(price)),
        fee=Money(amount=Decimal(str(fee)), currency=Currency(str(fee_ccy))),
        executed_at=datetime.fromisoformat(str(executed_at)),
        price_eur=None if price_eur is None else Decimal(str(price_eur)),
        fee_eur=None if fee_eur is None else Decimal(str(fee_eur)),
    )
