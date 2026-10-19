"""The event log: signals, notifications, risk halts, anomalies, trades and fill quality."""

import json
import sqlite3
from datetime import datetime

from hermes.adapters.ledger._ledger_core import _LedgerCore
from hermes.adapters.ledger.codecs import (
    fill_quality_from_payload,
    iso_utc,
    notified_from_payload,
    signal_from_payload,
    trades_from_rows,
)
from hermes.adapters.ledger.risk_halt_rows import RISK_HALT_EVENT_TYPE, risk_halt_from_payload
from hermes.domain.errors import LedgerError
from hermes.domain.events.base import DomainEvent
from hermes.domain.events.fill_quality_recorded import (
    FillQualityRecorded,
    upcast_fill_quality_payload,
)
from hermes.domain.events.risk_halt_recorded import RiskHaltRecorded
from hermes.domain.events.signal_emitted import SignalEmitted
from hermes.domain.events.signal_notified import SignalNotified
from hermes.domain.events.trade_executed import TradeExecuted
from hermes.domain.strategy.signal import SignalKind
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.strategy_id import StrategyId


class _EventStore(_LedgerCore):
    """The event log: signals, notifications, risk halts, anomalies, trades and fill quality."""

    def append_event(self, event: DomainEvent) -> None:
        """Append a standalone :class:`DomainEvent` to the ``events`` table.

        Args:
            event: Any concrete :class:`DomainEvent` subclass. Serialized
                via :func:`event_to_json` and stored with its schema
                version so the upcaster chain can rehydrate it.

        Raises:
            LedgerError: On any SQLite failure.
        """
        with self._write("append_event"):
            self._insert_event(event)

    def has_signal_event(self, event: SignalEmitted) -> bool:
        """Return whether a ``SignalEmitted`` row matches ``event``.

        Matches on strategy, pair, kind and ``occurred_at``.

        Args:
            event: The signal event to look up.

        Returns:
            ``True`` when a matching row exists.

        Raises:
            LedgerError: On any SQLite read failure.
        """
        try:
            row = self._conn.execute(
                "SELECT 1 FROM events "
                "WHERE event_type = 'SignalEmitted' AND occurred_at = ? "
                "  AND json_extract(payload_json, '$.strategy_id.value') = ? "
                "  AND json_extract(payload_json, '$.pair.base') IS ? "
                "  AND json_extract(payload_json, '$.pair.quote') IS ? "
                "  AND json_extract(payload_json, '$.kind') = ? "
                "LIMIT 1",
                (
                    iso_utc(event.occurred_at),
                    event.strategy_id.value,
                    event.pair.base.value,
                    event.pair.quote.value,
                    event.kind.value,
                ),
            ).fetchone()
        except sqlite3.Error as exc:
            raise LedgerError(msg=f"has_signal_event failed: {exc}") from exc
        return row is not None

    def has_risk_halt(self, event: RiskHaltRecorded) -> bool:
        """Return whether a ``RiskHaltRecorded`` row matches ``event`` (#749).

        Matches on strategy, pair (``None`` matches a strategy-wide row),
        kind, rule and ``occurred_at``, so a bar or day evaluated again after
        a restart is recorded once.

        Args:
            event: The halt or kill record to look up.

        Returns:
            ``True`` when a matching row exists.

        Raises:
            LedgerError: On any SQLite read failure.
        """
        try:
            row = self._conn.execute(
                "SELECT 1 FROM events "
                "WHERE event_type = ? AND occurred_at = ? "
                "  AND json_extract(payload_json, '$.strategy_id.value') = ? "
                "  AND json_extract(payload_json, '$.pair.base') IS ? "
                "  AND json_extract(payload_json, '$.pair.quote') IS ? "
                "  AND json_extract(payload_json, '$.kind') = ? "
                "  AND json_extract(payload_json, '$.rule') = ? "
                "LIMIT 1",
                (
                    RISK_HALT_EVENT_TYPE,
                    iso_utc(event.occurred_at),
                    event.strategy_id.value,
                    None if event.pair is None else event.pair.base.value,
                    None if event.pair is None else event.pair.quote.value,
                    event.kind.value,
                    event.rule,
                ),
            ).fetchone()
        except sqlite3.Error as exc:
            raise LedgerError(msg=f"has_risk_halt failed: {exc}") from exc
        return row is not None

    def risk_halts(self, strategy_id: StrategyId) -> tuple[RiskHaltRecorded, ...]:
        """Return every halt, kill and drift row of ``strategy_id``, oldest first (#796).

        Raises:
            LedgerError: On any SQLite failure or an unreadable payload.
        """
        try:
            rows = self._conn.execute(
                "SELECT payload_json FROM events WHERE event_type = ? "
                "  AND json_extract(payload_json, '$.strategy_id.value') = ? "
                "ORDER BY occurred_at, id",
                (RISK_HALT_EVENT_TYPE, strategy_id.value),
            ).fetchall()
        except sqlite3.Error as exc:
            raise LedgerError(msg=f"risk_halts failed: {exc}") from exc
        try:
            return tuple(risk_halt_from_payload(json.loads(payload)) for (payload,) in rows)
        except (KeyError, ValueError, TypeError) as exc:
            raise LedgerError(msg=f"risk_halts: unreadable payload: {exc}") from exc

    def has_anomaly_from(self, source: str) -> bool:
        """Return whether an ``AnomalyDetected`` row from ``source`` is recorded (#1117).

        Raises:
            LedgerError: On any SQLite read failure.
        """
        try:
            row = self._conn.execute(
                "SELECT 1 FROM events WHERE event_type = 'AnomalyDetected' "
                "  AND json_extract(payload_json, '$.source') = ? LIMIT 1",
                (source,),
            ).fetchone()
        except sqlite3.Error as exc:
            raise LedgerError(msg=f"has_anomaly_from failed: {exc}") from exc
        return row is not None

    def latest_signal_event(
        self,
        strategy_id: StrategyId,
        pair: Pair,
        *,
        at_or_before: datetime,
        kind: SignalKind | None = None,
    ) -> SignalEmitted | None:
        """Return the newest ``SignalEmitted`` of a strategy and pair up to a bound.

        Args:
            strategy_id: Strategy bucket that emitted the signal.
            pair: Trading pair.
            at_or_before: Inclusive upper bound on ``occurred_at``.
            kind: Only signals of this kind, or any kind when ``None``.

        Returns:
            The rehydrated event, or ``None`` when there is none.

        Raises:
            LedgerError: On any SQLite read failure or an unreadable payload.
        """
        wanted = None if kind is None else kind.value
        try:
            row = self._conn.execute(
                "SELECT payload_json FROM events "
                "WHERE event_type = 'SignalEmitted' AND occurred_at <= ? "
                "  AND json_extract(payload_json, '$.strategy_id.value') = ? "
                "  AND json_extract(payload_json, '$.pair.base') = ? "
                "  AND json_extract(payload_json, '$.pair.quote') = ? "
                "  AND (? IS NULL OR json_extract(payload_json, '$.kind') = ?) "
                "ORDER BY occurred_at DESC, id DESC LIMIT 1",
                (
                    iso_utc(at_or_before),
                    strategy_id.value,
                    pair.base.value,
                    pair.quote.value,
                    wanted,
                    wanted,
                ),
            ).fetchone()
        except sqlite3.Error as exc:
            raise LedgerError(msg=f"latest_signal_event failed: {exc}") from exc
        if row is None:
            return None
        try:
            return signal_from_payload(json.loads(row[0]))
        except (KeyError, ValueError, TypeError) as exc:
            raise LedgerError(msg=f"latest_signal_event: unreadable payload: {exc}") from exc

    def latest_signal_notified(self, signal: SignalEmitted) -> SignalNotified | None:
        """Return the newest push record of a stored signal (#854).

        Args:
            signal: The signal as stored; matched by its ``correlation_id``.

        Returns:
            The newest ``SignalNotified`` row, or ``None`` when it was never pushed.

        Raises:
            LedgerError: On any SQLite read failure or an unreadable payload.
        """
        try:
            row = self._conn.execute(
                "SELECT payload_json FROM events "
                "WHERE event_type = 'SignalNotified' AND correlation_id = ? "
                "ORDER BY id DESC LIMIT 1",
                (signal.correlation_id,),
            ).fetchone()
        except sqlite3.Error as exc:
            raise LedgerError(msg=f"latest_signal_notified failed: {exc}") from exc
        if row is None:
            return None
        try:
            return notified_from_payload(json.loads(row[0]))
        except (KeyError, ValueError, TypeError) as exc:
            raise LedgerError(msg=f"latest_signal_notified: unreadable payload: {exc}") from exc

    def signals_owed_a_push(
        self, strategy_id: StrategyId, pair: Pair, *, since: datetime, until: datetime
    ) -> tuple[tuple[SignalEmitted, tuple[str, ...]], ...]:
        """Return the signals whose newest push failed on some channel (#854).

        A signal never pushed is not listed: only the bar that emitted it
        pushes it the first time.

        Args:
            strategy_id: Strategy bucket that emitted the signals.
            pair: Trading pair.
            since: Inclusive lower bound on the signal time.
            until: Inclusive upper bound on the signal time.

        Returns:
            ``(signal, failed channels)`` pairs, oldest signal first.

        Raises:
            LedgerError: On any SQLite read failure or an unreadable payload.
        """
        try:
            rows = self._conn.execute(
                "SELECT s.payload_json, n.payload_json FROM events n "
                "JOIN events s ON s.correlation_id = n.correlation_id "
                "  AND s.event_type = 'SignalEmitted' "
                "WHERE n.event_type = 'SignalNotified' AND n.occurred_at >= ? "
                "  AND n.occurred_at <= ? "
                "  AND n.id = (SELECT MAX(m.id) FROM events m "
                "              WHERE m.event_type = 'SignalNotified' "
                "                AND m.correlation_id = n.correlation_id) "
                "  AND json_extract(s.payload_json, '$.strategy_id.value') = ? "
                "  AND json_extract(s.payload_json, '$.pair.base') = ? "
                "  AND json_extract(s.payload_json, '$.pair.quote') = ? "
                "ORDER BY s.occurred_at ASC, s.id ASC",
                (
                    iso_utc(since),
                    iso_utc(until),
                    strategy_id.value,
                    pair.base.value,
                    pair.quote.value,
                ),
            ).fetchall()
        except sqlite3.Error as exc:
            raise LedgerError(msg=f"signals_owed_a_push failed: {exc}") from exc
        try:
            owed = [
                (signal_from_payload(json.loads(signal)), notified_from_payload(json.loads(mark)))
                for signal, mark in rows
            ]
        except (KeyError, ValueError, TypeError) as exc:
            raise LedgerError(msg=f"signals_owed_a_push: unreadable payload: {exc}") from exc
        return tuple((signal, mark.failed) for signal, mark in owed if not mark.settled)

    def trade_events(self, strategy_id: StrategyId) -> tuple[TradeExecuted, ...]:
        """Return every ``TradeExecuted`` of a strategy, oldest first.

        Args:
            strategy_id: Strategy bucket the fills belong to.

        Returns:
            The rehydrated events ordered by ``occurred_at`` then row id.

        Raises:
            LedgerError: On any SQLite read failure, a row whose schema version
                is not 2, or an unreadable payload.
        """
        try:
            rows = self._conn.execute(
                "SELECT event_schema_version, payload_json FROM events "
                "WHERE event_type = 'TradeExecuted' "
                "  AND json_extract(payload_json, '$.strategy_id.value') = ? "
                "ORDER BY occurred_at ASC, id ASC",
                (strategy_id.value,),
            ).fetchall()
        except sqlite3.Error as exc:
            raise LedgerError(msg=f"trade_events failed: {exc}") from exc
        return trades_from_rows(rows, op="trade_events")

    def trades_between(self, start: datetime, end: datetime) -> tuple[TradeExecuted, ...]:
        """Return every ``TradeExecuted`` with ``start <= occurred_at <= end``, oldest first.

        All strategies are included: the recharacterization guard measures the
        taxpayer's activity, not one bucket's (ADR-002 §6, #135).

        Args:
            start: Window start (UTC, inclusive).
            end: Window end (UTC, inclusive).

        Returns:
            The rehydrated events ordered by ``occurred_at`` then row id.

        Raises:
            LedgerError: On any SQLite read failure, a row whose schema version
                is not 2, or an unreadable payload.
        """
        try:
            rows = self._conn.execute(
                "SELECT event_schema_version, payload_json FROM events "
                "WHERE event_type = 'TradeExecuted' AND occurred_at >= ? AND occurred_at <= ? "
                "ORDER BY occurred_at ASC, id ASC",
                (iso_utc(start), iso_utc(end)),
            ).fetchall()
        except sqlite3.Error as exc:
            raise LedgerError(msg=f"trades_between failed: {exc}") from exc
        return trades_from_rows(rows, op="trades_between")

    def fill_quality_events(self, strategy_id: StrategyId) -> tuple[FillQualityRecorded, ...]:
        """Return every ``FillQualityRecorded`` of a strategy, oldest first.

        Args:
            strategy_id: Strategy bucket the fills belong to.

        Returns:
            The rehydrated events ordered by ``occurred_at`` then row id.

        Raises:
            LedgerError: On any SQLite read failure, a row whose schema version
                is unknown, or an unreadable payload.
        """
        try:
            rows = self._conn.execute(
                "SELECT event_schema_version, payload_json FROM events "
                "WHERE event_type = 'FillQualityRecorded' "
                "  AND json_extract(payload_json, '$.strategy_id.value') = ? "
                "ORDER BY occurred_at ASC, id ASC",
                (strategy_id.value,),
            ).fetchall()
        except sqlite3.Error as exc:
            raise LedgerError(msg=f"fill_quality_events failed: {exc}") from exc
        events: list[FillQualityRecorded] = []
        for version, payload_json in rows:
            try:
                payload = upcast_fill_quality_payload(version, json.loads(payload_json))
                events.append(fill_quality_from_payload(payload))
            except (KeyError, ValueError, TypeError) as exc:
                raise LedgerError(msg=f"fill_quality_events: unreadable payload: {exc}") from exc
        return tuple(events)
