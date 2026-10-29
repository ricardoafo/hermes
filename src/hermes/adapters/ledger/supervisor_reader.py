"""Read-only SQLite adapter for :class:`~hermes.ports.supervisor.SupervisorReadPort`.

Builds on :class:`~hermes.adapters.ledger.status_reader.SqliteStatusReader`,
so the ledger is opened with ``mode=ro`` and can't be written.

Reference: issue #470.
"""

import json
import sqlite3
from datetime import datetime

from hermes.adapters.ledger._cells import time_cell
from hermes.adapters.ledger.status_reader import SqliteStatusReader
from hermes.domain.errors import LedgerError
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair
from hermes.ports.supervisor import BarCount, FillFact, SignalFact

__all__ = ["SqliteSupervisorReader"]


class SqliteSupervisorReader(SqliteStatusReader):
    """Status reads plus the price-free signal, bar and fill history."""

    def signals_since(self, since: datetime) -> tuple[SignalFact, ...]:
        """Return the signals at or after ``since``, oldest first.

        Raises:
            LedgerError: On a read failure or an unreadable payload.
        """
        try:
            rows = self._conn.execute(
                "SELECT payload_json FROM events WHERE event_type = 'SignalEmitted' "
                "AND occurred_at >= ? ORDER BY occurred_at, id",
                (since.isoformat(),),
            ).fetchall()
        except sqlite3.Error as exc:
            raise LedgerError(msg=f"signals_since failed: {exc}") from exc
        try:
            return tuple(_fact(json.loads(payload)) for (payload,) in rows)
        except (KeyError, ValueError, TypeError) as exc:
            raise LedgerError(msg=f"signals_since: unreadable payload: {exc}") from exc

    def bars_since(self, since: datetime) -> tuple[BarCount, ...]:
        """Return the handled-bar count of each stream at or after ``since``.

        Raises:
            LedgerError: On a read failure or an unreadable payload.
        """
        rows = self._rows(
            "SELECT json_extract(payload_json, '$.pair.base'), "
            "json_extract(payload_json, '$.pair.quote'), "
            "json_extract(payload_json, '$.interval'), COUNT(*) "
            "FROM events WHERE event_type = 'BarProcessed' AND occurred_at >= ? "
            "GROUP BY 1, 2, 3 ORDER BY 1, 2, 3",
            since,
            op="bars_since",
        )
        try:
            return tuple(
                BarCount(
                    pair=_pair(base, quote),
                    interval=KlineInterval(str(interval)),
                    count=int(str(count)),
                )
                for base, quote, interval, count in rows
            )
        except (ValueError, TypeError) as exc:
            raise LedgerError(msg=f"bars_since: unreadable payload: {exc}") from exc

    def fills_since(self, since: datetime) -> tuple[FillFact, ...]:
        """Return the fills at or after ``since``: pair, side and time only.

        Raises:
            LedgerError: On a read failure or an unreadable payload.
        """
        rows = self._rows(
            "SELECT json_extract(payload_json, '$.pair.base'), "
            "json_extract(payload_json, '$.pair.quote'), "
            "json_extract(payload_json, '$.side'), occurred_at "
            "FROM events WHERE event_type = 'TradeExecuted' AND occurred_at >= ? "
            "ORDER BY occurred_at, id",
            since,
            op="fills_since",
        )
        try:
            return tuple(
                FillFact(
                    pair=_pair(base, quote),
                    side=str(side),
                    occurred_at=time_cell(occurred_at),
                )
                for base, quote, side, occurred_at in rows
            )
        except (ValueError, TypeError) as exc:
            raise LedgerError(msg=f"fills_since: unreadable payload: {exc}") from exc

    def _rows(self, sql: str, since: datetime, *, op: str) -> list[tuple[object, ...]]:
        try:
            rows: list[tuple[object, ...]] = self._conn.execute(
                sql, (since.isoformat(),)
            ).fetchall()
        except sqlite3.Error as exc:
            raise LedgerError(msg=f"{op} failed: {exc}") from exc
        return rows


def _pair(base: object, quote: object) -> Pair:
    return Pair(base=Currency(str(base)), quote=Currency(str(quote)))


def _fact(payload: dict[str, object]) -> SignalFact:
    pair = payload["pair"]
    if not isinstance(pair, dict):
        raise TypeError("pair must be an object")
    reason = payload.get("exit_reason")
    return SignalFact(
        pair=Pair(base=Currency(pair["base"]), quote=Currency(pair["quote"])),
        kind=str(payload["kind"]),
        occurred_at=time_cell(payload["occurred_at"]),
        exit_reason=None if reason is None else str(reason),
    )
