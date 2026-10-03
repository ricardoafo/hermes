"""Activity rows read from the SQLite ledger, read-only (#374).

Until simulated fills land (#353) the ledger holds bar checks
(``BarProcessed``) and strategy signals (``SignalEmitted``). The shadow
position opens on an entry signal and closes on an exit signal, so:

- an entry signal is an ``opened`` row at the signal bar's close price;
- an exit signal is a ``closed`` row with no price and no net (there is no
  fill, so no money to show yet);
- with ``--all``, every bar check and every signal also gets its own quiet row.

Restarts, warnings and errors live in the journal, not the ledger; they join
the feed when the journal reader (#376) lands.

The connection is opened with SQLite's ``mode=ro`` URI and never migrates, so
the feed is safe to read next to a running ``hermes serve``.

Reference: openspec/changes/374-hermes-activity/change.md.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Final, Self

from hermes.application.console.views import ActivityItem, ActivityKind
from hermes.domain.errors import LedgerError

__all__ = ["LedgerActivityReader"]

_ONE_MS: Final = timedelta(milliseconds=1)


class LedgerActivityReader:
    """Turn ledger events into activity rows."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        """Wrap an already-open read-only connection.

        Args:
            connection: A connection opened by :meth:`open` (or a test's own).
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
        """Close the connection, whatever ended the ``with`` block."""
        self.close()

    def items_since(self, since: datetime) -> tuple[ActivityItem, ...]:
        """Return the activity rows of every bar check and signal at or after ``since``.

        Args:
            since: Inclusive lower bound, UTC-aware.

        Returns:
            Rows in ledger order; quiet ones included (the caller filters).

        Raises:
            LedgerError: On any SQLite read failure or an unreadable payload.
        """
        try:
            rows = self._conn.execute(
                "SELECT event_type, payload_json FROM events "
                "WHERE event_type IN ('BarProcessed', 'SignalEmitted') AND occurred_at >= ? "
                "ORDER BY occurred_at, id",
                (since.isoformat(),),
            ).fetchall()
        except sqlite3.Error as exc:
            raise LedgerError(msg=f"activity read failed: {exc}") from exc
        items: list[ActivityItem] = []
        try:
            for event_type, payload_json in rows:
                items.extend(_items(str(event_type), json.loads(payload_json)))
        except (KeyError, ValueError, TypeError) as exc:
            raise LedgerError(msg=f"activity: unreadable payload: {exc}") from exc
        return tuple(items)


def _items(event_type: str, payload: dict[str, object]) -> list[ActivityItem]:
    at = _boundary(datetime.fromisoformat(str(payload["occurred_at"])))
    pair = payload["pair"]
    if not isinstance(pair, dict):
        msg = "pair is not an object"
        raise TypeError(msg)
    coin = str(pair["base"])
    if event_type == "BarProcessed":
        return [
            ActivityItem(
                at=at, kind=ActivityKind.BAR, coin=coin, text=f"{payload['interval']} check"
            )
        ]
    kind = str(payload["kind"])
    side = str(payload["side"])
    signal = ActivityItem(at=at, kind=ActivityKind.SIGNAL, coin=coin, text=f"{kind} {side}")
    if kind == "entry":
        price = payload["entry_price"]
        opened = ActivityItem(
            at=at,
            kind=ActivityKind.OPENED,
            coin=coin,
            price=None if price is None else Decimal(str(price)),
        )
        return [signal, opened]
    return [signal, ActivityItem(at=at, kind=ActivityKind.CLOSED, coin=coin)]


def _boundary(at: datetime) -> datetime:
    """Return the candle boundary for Binance's inclusive close (boundary - 1 ms)."""
    bumped = at + _ONE_MS
    return bumped if bumped.second == 0 and bumped.microsecond == 0 else at
