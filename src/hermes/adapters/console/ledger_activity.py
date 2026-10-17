"""Activity rows read from the SQLite ledger, read-only (#374, #546).

The ledger holds bar checks (``BarProcessed``), strategy signals
(``SignalEmitted``) and, once fills are recorded, trades with their EUR values
(#435). Opened and closed rows come from the best source the ledger has:

- with recorded trades, a BUY is an ``opened`` row and a SELL a ``closed`` row,
  at the fill price shown in EUR at the fill-date rate; a closed row carries the
  realised net (the sum of its FIFO gains). A fill still EUR pending shows no
  price and no net rather than a mislabelled figure;
- a ledger with no trade at all (before simulated fills, #353) falls back to the
  signals: an entry signal is an ``opened`` row and an exit signal a ``closed``
  row, both without price or net;
- with ``--all``, every bar check and every signal also gets its own quiet row.

Restarts, warnings and errors live in the journal, not the ledger; the read
model adds them from the journal reader when it is readable (#653).

The connection is opened with SQLite's ``mode=ro`` URI and never migrates, so
the feed is safe to read next to a running ``hermes serve``.

Reference: openspec/changes/374-hermes-activity/change.md;
openspec/changes/live-home-screen/change.md (D-5).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Final, Self

from hermes.adapters.ledger.connection import open_ledger
from hermes.application.console.activity import candle_boundary
from hermes.application.console.views import ActivityItem, ActivityKind
from hermes.domain.errors import LedgerError

__all__ = ["LedgerActivityReader"]

_ONE: Final = Decimal(1)
_OK: Final = "ok"
_BUY: Final = "BUY"


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
            connection = open_ledger(resolved, read_only=True)
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
        """Return the activity rows of every bar check, signal and fill at or after ``since``.

        Args:
            since: Inclusive lower bound, UTC-aware.

        Returns:
            Rows oldest first; quiet ones included (the caller filters).

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
            has_trades = self._conn.execute("SELECT 1 FROM trades LIMIT 1").fetchone() is not None
            trade_rows = self._conn.execute(
                "SELECT t.id, t.pair_base, t.side, t.price_amount, t.executed_at, "
                "       t.eur_status, t.eur_rate, "
                "       (SELECT COUNT(*) FROM lot_consumptions c "
                "        WHERE c.trade_id = t.id AND c.consumed_asset = t.pair_base), "
                "       (SELECT COUNT(c.realized_gain_eur) FROM lot_consumptions c "
                "        WHERE c.trade_id = t.id AND c.consumed_asset = t.pair_base), "
                "       (SELECT group_concat(c.realized_gain_eur, ' ') FROM lot_consumptions c "
                "        WHERE c.trade_id = t.id AND c.consumed_asset = t.pair_base) "
                "FROM trades t WHERE t.executed_at >= ? ORDER BY t.executed_at, t.id",
                (since.isoformat(),),
            ).fetchall()
        except sqlite3.Error as exc:
            raise LedgerError(msg=f"activity read failed: {exc}") from exc
        items: list[ActivityItem] = []
        try:
            for event_type, payload_json in rows:
                items.extend(
                    _items(str(event_type), json.loads(payload_json), from_signals=not has_trades)
                )
            items.extend(_trade_item(row) for row in trade_rows)
        except (KeyError, ValueError, TypeError, ArithmeticError) as exc:
            raise LedgerError(msg=f"activity: unreadable payload: {exc}") from exc
        return tuple(sorted(items, key=lambda item: item.at))


def _trade_item(row: tuple[object, ...]) -> ActivityItem:
    """Return the opened (BUY) or closed (SELL) row of one recorded fill."""
    _id, base, side, price, executed_at, status, rate, pieces, gains, gain_text = row
    priced = status == _OK and rate is not None
    kind = ActivityKind.OPENED if side == _BUY else ActivityKind.CLOSED
    net = None
    if kind is ActivityKind.CLOSED and priced and pieces and pieces == gains:
        net = sum((Decimal(g) for g in str(gain_text).split()), start=Decimal(0))
    return ActivityItem(
        at=datetime.fromisoformat(str(executed_at)),
        kind=kind,
        coin=str(base),
        price=Decimal(str(price)) if priced else None,
        net=net,
        eur_per_usdt=_ONE / Decimal(str(rate)) if priced else _ONE,
    )


def _items(
    event_type: str, payload: dict[str, object], *, from_signals: bool = True
) -> list[ActivityItem]:
    at = candle_boundary(datetime.fromisoformat(str(payload["occurred_at"])))
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
    if not from_signals:
        return [signal]
    if kind == "entry":
        return [signal, ActivityItem(at=at, kind=ActivityKind.OPENED, coin=coin)]
    return [signal, ActivityItem(at=at, kind=ActivityKind.CLOSED, coin=coin)]
