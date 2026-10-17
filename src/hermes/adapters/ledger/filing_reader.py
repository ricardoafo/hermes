"""Read-only ledger queries for the Modelo 100 and Modelo 721 reports.

Opens the ledger with SQLite's ``mode=ro`` URI and never migrates, so a
report can run next to ``hermes serve`` and can never change the ledger. It
reads the FIFO projection ``SqliteLedger`` stores (``lot_consumptions``
linked to their SELL trade); it does not replay FIFO itself. Consumptions
recorded before trade links existed (no ``trade_id``) are not read: the
filing figures need the SELL's EUR proceeds and fee, which only linked rows
carry.

Times are compared after parsing, not as stored strings, because stored ISO
strings omit zero microseconds and do not sort as instants in every case.

Reference: openspec/changes/36-modelo-100-721/change.md D-1.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Self

from hermes.adapters.ledger._cells import decimal_cell, optional_decimal_cell, time_cell
from hermes.domain.errors import LedgerError
from hermes.domain.filing.modelo100 import DisposalPiece, PurchaseRecord
from hermes.domain.filing.modelo721 import UnitTrade
from hermes.domain.trading.order import OrderSide
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair

__all__ = ["LedgerFilingReader"]


class LedgerFilingReader:
    """The :class:`~hermes.ports.filing.FilingLedgerReaderPort` over a SQLite ledger."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        """Wrap an open connection; :meth:`open` makes a read-only one.

        Args:
            connection: The ledger connection.
        """
        self._conn = connection

    @classmethod
    def open(cls, path: Path) -> Self:
        """Open ``path`` read-only.

        Args:
            path: The ledger file.

        Returns:
            A reader over a ``mode=ro`` connection.

        Raises:
            FileNotFoundError: There is no file at ``path``; nothing is created.
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
        """Close the connection."""
        self.close()

    def disposal_pieces(self, start: datetime, end: datetime) -> tuple[DisposalPiece, ...]:
        """Return the stored consumptions of every SELL filled in ``[start, end)``.

        Raises:
            LedgerError: A query failed or a row is unreadable.
        """
        rows = self._query(
            "SELECT c.trade_id, s.pair_base, s.pair_quote, s.executed_at, "
            "       COALESCE(CAST(l.trade_id AS TEXT), l.lot_id), l.opened_at, "
            "       c.consumed_qty, c.proceeds_eur, c.cost_eur, c.fee_eur "
            "FROM lot_consumptions c "
            "JOIN trades s ON s.id = c.trade_id "
            "JOIN lots l ON l.lot_id = c.lot_id "
            "WHERE s.side = 'SELL' "
            "ORDER BY c.id"
        )
        pieces: list[DisposalPiece] = []
        try:
            for row in rows:
                disposed_at = time_cell(row[3])
                if not start <= disposed_at < end:
                    continue
                pieces.append(
                    DisposalPiece(
                        sell_ref=str(row[0]),
                        asset=Currency(row[1]),
                        received=Currency(row[2]),
                        disposed_at=disposed_at,
                        buy_ref=str(row[4]),
                        acquired_at=time_cell(row[5]),
                        qty=decimal_cell(row[6]),
                        proceeds_eur=optional_decimal_cell(row[7]),
                        cost_eur=optional_decimal_cell(row[8]),
                        fee_eur=optional_decimal_cell(row[9]),
                    )
                )
        except (ValueError, TypeError) as exc:
            raise LedgerError(msg=f"disposal_pieces: unreadable row: {exc}") from exc
        return tuple(pieces)

    def purchases(self, start: datetime, end: datetime) -> tuple[PurchaseRecord, ...]:
        """Return every BUY filled in ``[start, end]``.

        Raises:
            LedgerError: A query failed or a row is unreadable.
        """
        rows = self._query(
            "SELECT id, pair_base, executed_at FROM trades WHERE side = 'BUY' ORDER BY id"
        )
        try:
            records = [
                PurchaseRecord(
                    ref=str(row[0]), asset=Currency(row[1]), acquired_at=time_cell(row[2])
                )
                for row in rows
            ]
        except (ValueError, TypeError) as exc:
            raise LedgerError(msg=f"purchases: unreadable row: {exc}") from exc
        return tuple(r for r in records if start <= r.acquired_at <= end)

    def unit_trades(self, until: datetime) -> tuple[UnitTrade, ...]:
        """Return every trade filled before ``until``, in fill order.

        Raises:
            LedgerError: A query failed or a row is unreadable.
        """
        rows = self._query(
            "SELECT id, pair_base, side, qty_amount, executed_at FROM trades ORDER BY id"
        )
        try:
            trades = [
                UnitTrade(
                    ref=str(row[0]),
                    asset=Currency(row[1]),
                    side=OrderSide(row[2]),
                    qty=decimal_cell(row[3]),
                    executed_at=time_cell(row[4]),
                )
                for row in rows
            ]
        except (ValueError, TypeError) as exc:
            raise LedgerError(msg=f"unit_trades: unreadable row: {exc}") from exc
        return tuple(
            sorted((t for t in trades if t.executed_at < until), key=lambda t: t.executed_at)
        )

    def last_close(self, pair: Pair, at: datetime) -> Decimal | None:
        """Return the close of the last stored bar of ``pair`` closed at or before ``at``.

        Raises:
            LedgerError: A query failed or a row is unreadable.
        """
        rows = self._query(
            "SELECT close_time, close FROM live_bars WHERE pair_base = ? AND pair_quote = ?",
            (pair.base.value, pair.quote.value),
        )
        try:
            bars = [(time_cell(row[0]), decimal_cell(row[1])) for row in rows]
        except (ValueError, TypeError) as exc:
            raise LedgerError(msg=f"last_close: unreadable bar: {exc}") from exc
        closed = [bar for bar in bars if bar[0] <= at]
        if not closed:
            return None
        return max(closed, key=lambda bar: bar[0])[1]

    def _query(self, sql: str, params: tuple[str, ...] = ()) -> list[tuple[str, ...]]:
        try:
            return self._conn.execute(sql, params).fetchall()
        except sqlite3.Error as exc:
            raise LedgerError(msg=f"filing reader query failed: {exc}") from exc
