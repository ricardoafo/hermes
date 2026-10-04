"""Read-only SQLite adapter for :class:`~hermes.ports.supervisor.SupervisorReadPort`.

Builds on :class:`~hermes.adapters.ledger.status_reader.SqliteStatusReader`,
so the ledger is opened with ``mode=ro`` and can't be written.

Reference: issue #470.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime

from hermes.adapters.ledger.status_reader import SqliteStatusReader
from hermes.domain.errors import LedgerError
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair
from hermes.ports.supervisor import SignalFact

__all__ = ["SqliteSupervisorReader"]


class SqliteSupervisorReader(SqliteStatusReader):
    """Status reads plus the price-free signal history."""

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


def _fact(payload: dict[str, object]) -> SignalFact:
    pair = payload["pair"]
    if not isinstance(pair, dict):
        raise TypeError("pair must be an object")
    return SignalFact(
        pair=Pair(base=Currency(pair["base"]), quote=Currency(pair["quote"])),
        kind=str(payload["kind"]),
        occurred_at=datetime.fromisoformat(str(payload["occurred_at"])),
    )
