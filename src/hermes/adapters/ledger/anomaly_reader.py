"""Read-only access to the ``AnomalyDetected`` rows of the ledger ``events`` table (#136).

Opens the ledger read-only through
:class:`~hermes.adapters.ledger.connection.ReadOnlyLedgerReader` (``mode=ro``
with the shared busy timeout) and never migrates, so
``hermes anomalies`` is safe to run next to ``hermes serve``. Each row is
rehydrated into the :class:`AnomalyDetected` that was appended.

Reference: GitHub issue #136 (record-only anomaly intake).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime

from hermes.adapters.ledger.connection import ReadOnlyLedgerReader
from hermes.domain.errors import LedgerError
from hermes.domain.events.anomaly_detected import AnomalyDetected, AnomalySeverity

__all__ = ["SqliteAnomalyReader", "latest_anomaly"]


class SqliteAnomalyReader(ReadOnlyLedgerReader):
    """List recorded anomalies from a ledger file without write access."""

    def since(self, since: datetime) -> tuple[AnomalyDetected, ...]:
        """Return every anomaly that occurred at or after ``since``, newest first.

        Args:
            since: Inclusive lower bound; must be timezone-aware.

        Returns:
            The recorded anomalies, newest first (ties in append order, last first).

        Raises:
            LedgerError: On a naive ``since``, a SQLite read failure or an
                unreadable payload.
        """
        if since.tzinfo is None:
            raise LedgerError(msg=f"anomaly reader: naive since {since!r}")
        try:
            rows = self._conn.execute(
                "SELECT payload_json FROM events "
                "WHERE event_type = 'AnomalyDetected' AND occurred_at >= ? "
                "ORDER BY occurred_at DESC, id DESC",
                (since.astimezone(UTC).isoformat(),),
            ).fetchall()
        except sqlite3.Error as exc:
            raise LedgerError(msg=f"anomaly read failed: {exc}") from exc
        try:
            return tuple(_anomaly(json.loads(payload)) for (payload,) in rows)
        except (KeyError, ValueError, TypeError) as exc:
            raise LedgerError(msg=f"anomalies: unreadable payload: {exc}") from exc


def latest_anomaly(connection: sqlite3.Connection, source: str) -> AnomalyDetected | None:
    """Return the newest anomaly recorded by ``source``, or ``None`` when there is none.

    Args:
        connection: An open ledger connection, read-only or not.
        source: The ``source`` field to match exactly.

    Returns:
        The anomaly appended last. Append order, not ``occurred_at``, decides:
        a clock-skew anomaly is stamped by the skewed clock itself, so a later
        row can carry an earlier time.

    Raises:
        LedgerError: On a SQLite read failure or an unreadable payload.
    """
    try:
        row = connection.execute(
            "SELECT payload_json FROM events "
            "WHERE event_type = 'AnomalyDetected' "
            "  AND json_extract(payload_json, '$.source') = ? "
            "ORDER BY id DESC LIMIT 1",
            (source,),
        ).fetchone()
    except sqlite3.Error as exc:
        raise LedgerError(msg=f"latest anomaly read failed: {exc}") from exc
    if row is None:
        return None
    try:
        return _anomaly(json.loads(row[0]))
    except (KeyError, ValueError, TypeError) as exc:
        raise LedgerError(msg=f"latest anomaly: unreadable payload: {exc}") from exc


def _anomaly(payload: dict[str, object]) -> AnomalyDetected:
    return AnomalyDetected(
        occurred_at=datetime.fromisoformat(str(payload["occurred_at"])),
        correlation_id=str(payload["correlation_id"]),
        source=str(payload["source"]),
        severity=AnomalySeverity(str(payload["severity"])),
        reason=str(payload["reason"]),
        event_seq=int(str(payload.get("event_seq", 0))),
    )
