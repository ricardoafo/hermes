"""Live bars and the bar-processing evidence."""

import sqlite3
from datetime import datetime

from hermes.adapters.ledger._ledger_core import _LedgerCore
from hermes.adapters.ledger.codecs import (
    iso_utc,
    live_bar_from_row,
)
from hermes.domain.errors import LedgerError
from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.value_objects.pair import Pair


class _BarStore(_LedgerCore):
    """Live bars and the bar-processing evidence."""

    def record_live_bar(self, bar: Bar) -> Bar | None:
        """Store one closed live bar unless its key is already stored (#421).

        Data only: no strategy, signal or event reads this table.

        Args:
            bar: The closed bar the serve loop is handling.

        Returns:
            ``None`` when the bar was stored or an identical bar was already
            there; the stored bar when it differs (the stored one is kept).

        Raises:
            LedgerError: On any SQLite failure.
        """
        key = (
            bar.pair.base.value,
            bar.pair.quote.value,
            bar.interval.value,
            iso_utc(bar.open_time),
        )
        with self._write("record_live_bar"):
            inserted = self._conn.execute(
                "INSERT OR IGNORE INTO live_bars (pair_base, pair_quote, interval, open_time, "
                "close_time, open, high, low, close, volume, recorded_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    *key,
                    iso_utc(bar.close_time),
                    str(bar.open),
                    str(bar.high),
                    str(bar.low),
                    str(bar.close),
                    str(bar.volume),
                    iso_utc(self._now()),
                ),
            ).rowcount
        if inserted:
            return None
        try:
            row = self._conn.execute(
                "SELECT open_time, close_time, open, high, low, close, volume FROM live_bars "
                "WHERE pair_base = ? AND pair_quote = ? AND interval = ? AND open_time = ?",
                key,
            ).fetchone()
        except sqlite3.Error as exc:
            raise LedgerError(msg=f"record_live_bar failed: {exc}") from exc
        stored = live_bar_from_row(bar.pair, bar.interval, row)
        return None if stored == bar else stored

    def live_bars(
        self, pair: Pair, interval: KlineInterval, start: datetime, end: datetime
    ) -> tuple[Bar, ...]:
        """Return stored live bars with ``start <= open_time < end``, oldest first.

        Raises:
            LedgerError: On any SQLite failure.
        """
        try:
            rows = self._conn.execute(
                "SELECT open_time, close_time, open, high, low, close, volume FROM live_bars "
                "WHERE pair_base = ? AND pair_quote = ? AND interval = ? "
                "  AND open_time >= ? AND open_time < ? ORDER BY open_time",
                (pair.base.value, pair.quote.value, interval.value, iso_utc(start), iso_utc(end)),
            ).fetchall()
        except sqlite3.Error as exc:
            raise LedgerError(msg=f"live_bars failed: {exc}") from exc
        return tuple(live_bar_from_row(pair, interval, row) for row in rows)

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
        try:
            row = self._conn.execute(
                "SELECT occurred_at FROM events "
                "WHERE event_type = 'BarProcessed' "
                "  AND json_extract(payload_json, '$.pair.base') = ? "
                "  AND json_extract(payload_json, '$.pair.quote') = ? "
                "  AND json_extract(payload_json, '$.interval') = ? "
                "ORDER BY occurred_at DESC LIMIT 1",
                (pair.base.value, pair.quote.value, interval.value),
            ).fetchone()
        except sqlite3.Error as exc:
            raise LedgerError(msg=f"last_processed_close failed: {exc}") from exc
        return None if row is None else datetime.fromisoformat(row[0])

    def missed_bar_count(self) -> int:
        """Return how many distinct bars this ledger records as missed (#1117).

        A bar re-handled after a crash between its ``BarMissed`` and
        ``BarProcessed`` rows has two ``BarMissed`` rows; it counts once.

        Raises:
            LedgerError: On any SQLite read failure.
        """
        try:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM (SELECT DISTINCT occurred_at, "
                "  json_extract(payload_json, '$.pair.base'), "
                "  json_extract(payload_json, '$.pair.quote'), "
                "  json_extract(payload_json, '$.interval') "
                "FROM events WHERE event_type = 'BarMissed')"
            ).fetchone()
        except sqlite3.Error as exc:
            raise LedgerError(msg=f"missed_bar_count failed: {exc}") from exc
        return int(row[0])
