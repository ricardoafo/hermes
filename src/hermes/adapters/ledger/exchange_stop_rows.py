"""Ledger rows of each exchange-held stop: the ``ExchangeStopChanged`` events (#1080).

No new table, so no migration: the rows are appended like any other event by
:meth:`~hermes.adapters.ledger.sqlite_ledger.SqliteLedger.append_event`, one
transaction each, and an older release ignores them. A crash between two rows
of one pass leaves the exchange ahead of the ledger, and the next pass (at the
latest the restart's reconcile) adopts or cancels what it finds.

Reference: GitHub issue #1080; openspec/changes/1080-exchange-stops/change.md.
"""

import json
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Final

from hermes.adapters.ledger.sqlite_ledger import SqliteLedger
from hermes.domain.errors import LedgerError
from hermes.domain.events.base import DomainEvent
from hermes.domain.events.exchange_stop_changed import ExchangeStopChanged, StopAction
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.strategy_id import StrategyId

__all__ = ["EXCHANGE_STOP_EVENT_TYPE", "SqliteExchangeStopJournal", "exchange_stop_from_payload"]

EXCHANGE_STOP_EVENT_TYPE: Final = ExchangeStopChanged.__qualname__
"""``events.event_type`` of every exchange-stop row."""


@dataclass(frozen=True, slots=True)
class SqliteExchangeStopJournal:
    """Reads and appends the exchange-stop rows of serve's ledger.

    Attributes:
        ledger: Serve's ledger, which appends each row in its own transaction.
        connection: The same ledger's connection, for the reads.
    """

    ledger: SqliteLedger
    connection: sqlite3.Connection

    def latest_exchange_stop(
        self, strategy_id: StrategyId, pair: Pair
    ) -> ExchangeStopChanged | None:
        """Return the newest row of ``pair`` that is not a failure, or ``None``.

        Append order decides, as for anomalies: a row is stamped by serve's
        clock, which can step back.

        Raises:
            LedgerError: On a SQLite read failure or an unreadable payload.
        """
        try:
            row = self.connection.execute(
                "SELECT payload_json FROM events "
                "WHERE event_type = ? "
                "  AND json_extract(payload_json, '$.strategy_id.value') = ? "
                "  AND json_extract(payload_json, '$.pair.base') = ? "
                "  AND json_extract(payload_json, '$.pair.quote') = ? "
                "  AND json_extract(payload_json, '$.action') != ? "
                "ORDER BY id DESC LIMIT 1",
                (
                    EXCHANGE_STOP_EVENT_TYPE,
                    strategy_id.value,
                    pair.base.value,
                    pair.quote.value,
                    StopAction.FAILED.value,
                ),
            ).fetchone()
        except sqlite3.Error as exc:
            raise LedgerError(msg=f"exchange stop read failed: {exc}") from exc
        if row is None:
            return None
        try:
            return exchange_stop_from_payload(json.loads(row[0]))
        except (KeyError, ValueError, TypeError) as exc:
            raise LedgerError(msg=f"exchange stop: unreadable payload: {exc}") from exc

    def record_exchange_stops(self, events: Sequence[DomainEvent]) -> None:
        """Append ``events`` in order, one transaction each.

        Raises:
            LedgerError: On any SQLite failure; the rows before it stay.
        """
        for event in events:
            self.ledger.append_event(event)


def exchange_stop_from_payload(payload: dict[str, Any]) -> ExchangeStopChanged:
    """Rehydrate an :class:`ExchangeStopChanged` from its stored JSON payload.

    Raises:
        KeyError: A field is missing from the payload.
        ValueError: A field holds a value of the wrong shape.
    """
    pair = payload["pair"]
    entry_time = payload["entry_time"]
    return ExchangeStopChanged(
        occurred_at=datetime.fromisoformat(payload["occurred_at"]),
        correlation_id=payload["correlation_id"],
        event_seq=int(payload["event_seq"]),
        strategy_id=StrategyId(payload["strategy_id"]["value"]),
        pair=Pair(base=Currency(pair["base"]), quote=Currency(pair["quote"])),
        action=StopAction(payload["action"]),
        entry_time=None if entry_time is None else datetime.fromisoformat(entry_time),
        order_id=payload["order_id"],
        qty=_decimal(payload["qty"]),
        stop_price=_decimal(payload["stop_price"]),
        reason=payload["reason"],
    )


def _decimal(raw: object) -> Decimal | None:
    return None if raw is None else Decimal(str(raw))
