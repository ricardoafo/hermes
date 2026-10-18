"""Read-only SQLite adapter for :class:`~hermes.ports.parity_source.ParitySourcePort`.

Opens the ledger with SQLite's ``mode=ro`` URI, so the parity check (#358)
cannot modify the ledger even by accident, and never runs migrations (unlike
:class:`~hermes.adapters.ledger.sqlite_ledger.SqliteLedger`, whose constructor
migrates). Safe next to a running ``hermes serve``.

Reference: openspec/changes/358-parity-check/change.md.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Self

from hermes.adapters.ledger.connection import open_ledger
from hermes.adapters.ledger.sqlite_ledger import (
    _signal_from_payload,
    _trades_from_rows,
    live_bar_from_row,
)
from hermes.domain.errors import LedgerError
from hermes.domain.events.signal_emitted import SignalEmitted
from hermes.domain.events.trade_executed import TradeExecuted
from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.strategy.signal import SignalKind
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.strategy_id import StrategyId

__all__ = ["SqliteParityReader"]


class SqliteParityReader:
    """Answer the parity check's questions from a ledger file, read-only."""

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
            FileNotFoundError: There is no file at ``path``.
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

    def bars_closing_between(
        self, pair: Pair, interval: KlineInterval, after: datetime, upto: datetime
    ) -> tuple[Bar, ...]:
        """Return stored live bars with ``after < close_time <= upto``, oldest first.

        Raises:
            LedgerError: On any SQLite read failure or an unreadable bar.
        """
        rows = self._fetchall(
            "SELECT open_time, close_time, open, high, low, close, volume FROM live_bars "
            "WHERE pair_base = ? AND pair_quote = ? AND interval = ? "
            "  AND close_time > ? AND close_time <= ? ORDER BY close_time",
            (
                pair.base.value,
                pair.quote.value,
                interval.value,
                after.isoformat(),
                upto.isoformat(),
            ),
        )
        try:
            return tuple(live_bar_from_row(pair, interval, row) for row in rows)
        except (ValueError, TypeError) as exc:
            raise LedgerError(msg=f"bars_closing_between: unreadable bar: {exc}") from exc

    def signals_between(
        self, strategy_id: StrategyId, after: datetime, upto: datetime
    ) -> tuple[SignalEmitted, ...]:
        """Return the strategy's ``SignalEmitted`` with ``after < occurred_at <= upto``.

        Raises:
            LedgerError: On a read failure or an unreadable payload.
        """
        rows = self._fetchall(
            "SELECT payload_json FROM events "
            "WHERE event_type = 'SignalEmitted' AND occurred_at > ? AND occurred_at <= ? "
            "  AND json_extract(payload_json, '$.strategy_id.value') = ? "
            "ORDER BY occurred_at, id",
            (after.isoformat(), upto.isoformat(), strategy_id.value),
        )
        return self._signals(rows, op="signals_between")

    def latest_signal(
        self,
        strategy_id: StrategyId,
        pair: Pair,
        *,
        at_or_before: datetime,
        kind: SignalKind | None = None,
    ) -> SignalEmitted | None:
        """Return the newest ``SignalEmitted`` of the strategy and pair up to a bound.

        With ``kind``, only signals of that kind count (any kind when ``None``).

        Raises:
            LedgerError: On a read failure or an unreadable payload.
        """
        wanted = None if kind is None else kind.value
        rows = self._fetchall(
            "SELECT payload_json FROM events "
            "WHERE event_type = 'SignalEmitted' AND occurred_at <= ? "
            "  AND json_extract(payload_json, '$.strategy_id.value') = ? "
            "  AND json_extract(payload_json, '$.pair.base') = ? "
            "  AND json_extract(payload_json, '$.pair.quote') = ? "
            "  AND (? IS NULL OR json_extract(payload_json, '$.kind') = ?) "
            "ORDER BY occurred_at DESC, id DESC LIMIT 1",
            (
                at_or_before.isoformat(),
                strategy_id.value,
                pair.base.value,
                pair.quote.value,
                wanted,
                wanted,
            ),
        )
        found = self._signals(rows, op="latest_signal")
        return found[0] if found else None

    def trades(self, strategy_id: StrategyId) -> tuple[TradeExecuted, ...]:
        """Return every ``TradeExecuted`` of the strategy, oldest first.

        Raises:
            LedgerError: On a read failure, an unsupported schema version or an
                unreadable payload.
        """
        rows = self._fetchall(
            "SELECT event_schema_version, payload_json FROM events "
            "WHERE event_type = 'TradeExecuted' "
            "  AND json_extract(payload_json, '$.strategy_id.value') = ? "
            "ORDER BY occurred_at ASC, id ASC",
            (strategy_id.value,),
        )
        return _trades_from_rows(rows, op="trades")

    def missed_bars_between(self, after: datetime, upto: datetime) -> int:
        """Return how many bars closing in ``(after, upto]`` serve reached late (#1078).

        Raises:
            LedgerError: On a read failure.
        """
        rows = self._fetchall(
            "SELECT COUNT(*) FROM events "
            "WHERE event_type = 'BarMissed' AND occurred_at > ? AND occurred_at <= ?",
            (after.isoformat(), upto.isoformat()),
        )
        return int(rows[0][0])

    @staticmethod
    def _signals(rows: list[tuple[str]], *, op: str) -> tuple[SignalEmitted, ...]:
        try:
            return tuple(_signal_from_payload(json.loads(row[0])) for row in rows)
        except (KeyError, ValueError, TypeError) as exc:
            raise LedgerError(msg=f"{op}: unreadable payload: {exc}") from exc

    def _fetchall(self, sql: str, params: tuple[object, ...]) -> list[tuple[str]]:
        try:
            return self._conn.execute(sql, params).fetchall()
        except sqlite3.Error as exc:
            raise LedgerError(msg=f"parity read failed: {exc}") from exc
