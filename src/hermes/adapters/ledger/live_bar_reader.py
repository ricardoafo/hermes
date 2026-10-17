"""Read-only access to the ``live_bars`` table for checks that must not write.

Opens the ledger with SQLite's ``mode=ro`` URI, so the archive check (#359)
cannot modify the ledger even by accident, and does not run migrations.

Reference: GitHub issues #359, #421.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from hermes.adapters.ledger.sqlite_ledger import _row_to_bar
from hermes.domain.errors import LedgerError
from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.value_objects.pair import Pair

__all__ = ["SqliteLiveBarReader"]


@dataclass(frozen=True, slots=True)
class SqliteLiveBarReader:
    """Read stored live bars from a ledger file without write access.

    Args:
        db_path: Ledger database file.
    """

    db_path: Path

    def live_bars(
        self, pair: Pair, interval: KlineInterval, start: datetime, end: datetime
    ) -> tuple[Bar, ...]:
        """Return stored bars with ``start <= open_time < end``, oldest first.

        Raises:
            LedgerError: The file is missing, has no ``live_bars`` table, a
                read fails, or a stored bar is unreadable.
        """
        uri = f"{self.db_path.resolve().as_uri()}?mode=ro"
        try:
            connection = sqlite3.connect(uri, uri=True)
        except sqlite3.Error as exc:
            raise LedgerError(msg=f"live bar reader cannot open {self.db_path}: {exc}") from exc
        try:
            rows = connection.execute(
                "SELECT open_time, close_time, open, high, low, close, volume FROM live_bars "
                "WHERE pair_base = ? AND pair_quote = ? AND interval = ? "
                "  AND open_time >= ? AND open_time < ? ORDER BY open_time",
                (
                    pair.base.value,
                    pair.quote.value,
                    interval.value,
                    start.isoformat(),
                    end.isoformat(),
                ),
            ).fetchall()
        except sqlite3.Error as exc:
            raise LedgerError(msg=f"live bar reader query failed: {exc}") from exc
        finally:
            connection.close()
        try:
            return tuple(_row_to_bar(pair, interval, row) for row in rows)
        except (ValueError, TypeError) as exc:
            raise LedgerError(msg=f"live bar reader found an unreadable bar: {exc}") from exc
