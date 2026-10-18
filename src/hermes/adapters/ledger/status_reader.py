"""Read-only SQLite adapter for :class:`~hermes.ports.status.StatusReadPort`.

Opens the ledger with SQLite's ``mode=ro`` URI, so the connection cannot
write even by accident, and never runs migrations (unlike
:class:`~hermes.adapters.ledger.sqlite_ledger.SqliteLedger`, whose constructor
migrates). Safe to run next to a live ``hermes serve``: a read takes only a
shared lock for the length of one query.

Reference: openspec/changes/370-hermes-status/change.md.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Self

from hermes.adapters.ledger._cells import decimal_cell, time_cell
from hermes.adapters.ledger.anomaly_reader import latest_anomaly
from hermes.adapters.ledger.connection import open_ledger
from hermes.adapters.ledger.risk_halt_rows import RISK_HALT_EVENT_TYPE, risk_halt_from_payload
from hermes.domain.errors import LedgerError
from hermes.domain.events.anomaly_detected import CLOCK_SKEW_SOURCE, AnomalyDetected
from hermes.domain.events.risk_halt_recorded import RiskHaltRecorded
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.strategy_id import StrategyId
from hermes.ports.status import ShadowEntry

__all__ = ["SqliteStatusReader"]


class SqliteStatusReader:
    """Answer the status questions from a ledger file, read-only."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        """Wrap an already-open read-only connection.

        Args:
            connection: A connection opened by :meth:`open` (or a test's own).
        """
        self._conn = connection

    @classmethod
    def open(cls, path: Path, *, any_thread: bool = False) -> Self:
        """Open ``path`` read-only.

        Args:
            path: The ledger file.
            any_thread: Let another thread than this one read through it;
                see :func:`~hermes.adapters.ledger.connection.open_ledger`.

        Returns:
            A reader over a ``mode=ro`` connection.

        Raises:
            FileNotFoundError: There is no file at ``path``. SQLite would
                otherwise report a generic "unable to open" error.
            LedgerError: SQLite refused to open the file.
        """
        resolved = path.resolve()
        if not resolved.is_file():
            raise FileNotFoundError(str(path))
        try:
            connection = open_ledger(resolved, read_only=True, any_thread=any_thread)
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

    def last_processed_close(self, pair: Pair, interval: KlineInterval) -> datetime | None:
        """Return the newest ``BarProcessed`` close time for one stream.

        Args:
            pair: Trading pair.
            interval: Candle-duration bucket.

        Returns:
            The newest ``occurred_at``, or ``None`` when the stream has no row.

        Raises:
            LedgerError: On any SQLite read failure, or a stored time that is
                not ISO 8601 with a UTC offset.
        """
        row = self._fetchone(
            "SELECT MAX(occurred_at) FROM events "
            "WHERE event_type = 'BarProcessed' "
            "  AND json_extract(payload_json, '$.pair.base') = ? "
            "  AND json_extract(payload_json, '$.pair.quote') = ? "
            "  AND json_extract(payload_json, '$.interval') = ?",
            (pair.base.value, pair.quote.value, interval.value),
        )
        value = None if row is None else row[0]
        if value is None:
            return None
        try:
            return time_cell(value)
        except ValueError as exc:
            raise LedgerError(msg=f"last_processed_close: unreadable time: {exc}") from exc

    def count_bars_since(self, interval: KlineInterval, since: datetime) -> int:
        """Return the number of ``BarProcessed`` rows of ``interval`` at or after ``since``.

        Args:
            interval: Candle-duration bucket, across every pair.
            since: Inclusive lower bound, UTC-aware.

        Returns:
            The count.

        Raises:
            LedgerError: On any SQLite read failure.
        """
        row = self._fetchone(
            "SELECT COUNT(*) FROM events WHERE event_type = 'BarProcessed' "
            "  AND json_extract(payload_json, '$.interval') = ? AND occurred_at >= ?",
            (interval.value, since.isoformat()),
        )
        value = None if row is None else row[0]
        return value if isinstance(value, int) else 0

    def count_signals_since(self, since: datetime) -> int:
        """Return the number of ``SignalEmitted`` rows at or after ``since``.

        Args:
            since: Inclusive lower bound, UTC-aware.

        Returns:
            The count.

        Raises:
            LedgerError: On any SQLite read failure.
        """
        row = self._fetchone(
            "SELECT COUNT(*) FROM events WHERE event_type = 'SignalEmitted' AND occurred_at >= ?",
            (since.isoformat(),),
        )
        value = None if row is None else row[0]
        return value if isinstance(value, int) else 0

    def round_trips(self, strategy_id: StrategyId) -> int:
        """Return how many round trips ``strategy_id`` closed: its SELL trades (#797).

        Raises:
            LedgerError: On any SQLite read failure.
        """
        try:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM events WHERE event_type = 'TradeExecuted' "
                "AND json_extract(payload_json, '$.side') = 'SELL' "
                "AND json_extract(payload_json, '$.strategy_id.value') = ?",
                (strategy_id.value,),
            ).fetchone()
        except sqlite3.Error as exc:
            raise LedgerError(msg=f"round_trips failed: {exc}") from exc
        return int(row[0])

    def open_entries(self) -> tuple[ShadowEntry, ...]:
        """Return every pair whose newest filled entry has no exit after it, oldest first.

        An entry counts only when a fill shares its correlation id: an entry
        the executor refused or halted (exposure cap, stale book) opened
        nothing, so it must not show as a position (#962).

        Returns:
            The open shadow positions, entry facts only.

        Raises:
            LedgerError: On any SQLite read failure or an unreadable payload.
        """
        try:
            rows = self._conn.execute(
                "SELECT s.payload_json, EXISTS ("
                "  SELECT 1 FROM events t WHERE t.event_type = 'TradeExecuted' "
                "  AND t.correlation_id = s.correlation_id"
                ") FROM events s WHERE s.event_type = 'SignalEmitted' "
                "ORDER BY s.occurred_at, s.id"
            ).fetchall()
        except sqlite3.Error as exc:
            raise LedgerError(msg=f"open_entries failed: {exc}") from exc
        latest: dict[Pair, ShadowEntry | None] = {}
        try:
            for payload_json, filled in rows:
                payload = json.loads(payload_json)
                if payload["kind"] == "entry" and not filled:
                    continue
                pair = Pair(
                    base=Currency(payload["pair"]["base"]),
                    quote=Currency(payload["pair"]["quote"]),
                )
                latest[pair] = (
                    ShadowEntry(
                        pair=pair,
                        entry_time=time_cell(payload["occurred_at"]),
                        entry_price=decimal_cell(payload["entry_price"]),
                    )
                    if payload["kind"] == "entry"
                    else None
                )
        except (KeyError, ValueError, TypeError) as exc:
            raise LedgerError(msg=f"open_entries: unreadable payload: {exc}") from exc
        return tuple(
            sorted((e for e in latest.values() if e is not None), key=lambda e: e.entry_time)
        )

    def halts_since(self, since: datetime) -> tuple[RiskHaltRecorded, ...]:
        """Return every halt and kill recorded at or after ``since``, newest first.

        Args:
            since: Inclusive lower bound on the signal time, UTC-aware.

        Returns:
            The rehydrated records.

        Raises:
            LedgerError: On any SQLite read failure or an unreadable payload.
        """
        try:
            rows = self._conn.execute(
                "SELECT payload_json FROM events WHERE event_type = ? AND occurred_at >= ? "
                "ORDER BY occurred_at DESC, id DESC",
                (RISK_HALT_EVENT_TYPE, since.isoformat()),
            ).fetchall()
        except sqlite3.Error as exc:
            raise LedgerError(msg=f"halts_since failed: {exc}") from exc
        try:
            return tuple(risk_halt_from_payload(json.loads(payload)) for (payload,) in rows)
        except (KeyError, ValueError, TypeError) as exc:
            raise LedgerError(msg=f"halts_since: unreadable payload: {exc}") from exc

    def last_clock_skew(self) -> AnomalyDetected | None:
        """Return the newest clock-skew anomaly serve recorded, or ``None`` (#859).

        Returns:
            The anomaly; ``CRITICAL`` means the clock is still off.

        Raises:
            LedgerError: On any SQLite read failure or an unreadable payload.
        """
        return latest_anomaly(self._conn, CLOCK_SKEW_SOURCE)

    def bar_check_seconds(self, limit: int) -> tuple[Decimal, ...]:
        """Return how many seconds after each bar's close its check was recorded.

        The console's System screen draws these as the bar-check time
        sparkline (issue #717). ``BarProcessed.occurred_at`` is the bar's
        close and ``recorded_at`` the wall clock when the row was written, so
        the gap is the time the loop took to fetch, check and record the bar.

        Args:
            limit: How many of the newest bar checks to return.

        Returns:
            Whole seconds, oldest first; empty when no bar was checked yet.

        Raises:
            LedgerError: On any SQLite read failure or an unreadable time.
        """
        try:
            rows = self._conn.execute(
                "SELECT occurred_at, recorded_at FROM events "
                "WHERE event_type = 'BarProcessed' ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        except sqlite3.Error as exc:
            raise LedgerError(msg=f"bar_check_seconds failed: {exc}") from exc
        try:
            gaps = [
                datetime.fromisoformat(recorded) - datetime.fromisoformat(occurred)
                for occurred, recorded in rows
            ]
        except (TypeError, ValueError) as exc:
            raise LedgerError(msg=f"bar_check_seconds: unreadable time: {exc}") from exc
        return tuple(Decimal(int(gap.total_seconds())) for gap in reversed(gaps))

    def _fetchone(self, sql: str, params: tuple[str, ...]) -> tuple[str | int | None, ...] | None:
        """Run one read query and return its first row."""
        try:
            row: tuple[str | int | None, ...] | None = self._conn.execute(sql, params).fetchone()
        except sqlite3.Error as exc:
            raise LedgerError(msg=f"status read failed: {exc}") from exc
        return row
