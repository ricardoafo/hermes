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

from hermes.domain.errors import LedgerError
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair
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
    def open(cls, path: Path) -> Self:
        """Open ``path`` read-only.

        Args:
            path: The ledger file.

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

    def last_processed_close(self, pair: Pair, interval: KlineInterval) -> datetime | None:
        """Return the newest ``BarProcessed`` close time for one stream.

        Args:
            pair: Trading pair.
            interval: Candle-duration bucket.

        Returns:
            The newest ``occurred_at``, or ``None`` when the stream has no row.

        Raises:
            LedgerError: On any SQLite read failure.
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
        return datetime.fromisoformat(value) if isinstance(value, str) else None

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

    def open_entries(self) -> tuple[ShadowEntry, ...]:
        """Return every pair whose newest signal is an entry, oldest entry first.

        Returns:
            The open shadow positions, entry facts only.

        Raises:
            LedgerError: On any SQLite read failure or an unreadable payload.
        """
        try:
            rows = self._conn.execute(
                "SELECT payload_json FROM events WHERE event_type = 'SignalEmitted' "
                "ORDER BY occurred_at, id"
            ).fetchall()
        except sqlite3.Error as exc:
            raise LedgerError(msg=f"open_entries failed: {exc}") from exc
        latest: dict[Pair, ShadowEntry | None] = {}
        try:
            for (payload_json,) in rows:
                payload = json.loads(payload_json)
                pair = Pair(
                    base=Currency(payload["pair"]["base"]),
                    quote=Currency(payload["pair"]["quote"]),
                )
                latest[pair] = (
                    ShadowEntry(
                        pair=pair,
                        entry_time=datetime.fromisoformat(payload["occurred_at"]),
                        entry_price=Decimal(str(payload["entry_price"])),
                    )
                    if payload["kind"] == "entry"
                    else None
                )
        except (KeyError, ValueError, TypeError) as exc:
            raise LedgerError(msg=f"open_entries: unreadable payload: {exc}") from exc
        return tuple(
            sorted((e for e in latest.values() if e is not None), key=lambda e: e.entry_time)
        )

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
