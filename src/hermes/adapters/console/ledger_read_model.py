"""The console read model over the SQLite ledger, read-only (#546, slice 1).

Every view call opens the ledger with SQLite's ``mode=ro`` URI, runs its
queries and closes the connection again, so the console never writes, never
migrates and never holds a lock between two reads of a running ``hermes serve``.

This slice serves the positions views: the open set, size, cost and latest
price come from the newest portfolio mark of each strategy (#387), the stop
from the entry signal, and the EUR values from the recorded trades (#435).
The heartbeat is derived from the newest bar checks. Portfolio, activity and
the shadow test raise :class:`ViewNotAvailableError` until their slices land.

Reference: openspec/changes/ledger-read-model/change.md (D-1, D-3 to D-9).
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from contextlib import closing
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Final, Self

from hermes.application.console.position_figures import LotFacts, PositionFacts, build_detail
from hermes.application.console.read_model import ViewNotAvailableError
from hermes.application.console.views import (
    ActivityDay,
    Heartbeat,
    HeartbeatState,
    PortfolioRange,
    PortfolioSummary,
    PositionDetail,
    PositionRow,
    ShadowTest,
)
from hermes.domain.errors import LedgerError
from hermes.domain.market_data.bar_schedule import interval_duration
from hermes.domain.market_data.kline import KlineInterval
from hermes.infrastructure.time import now_utc

__all__ = ["LATE_AFTER", "LedgerReadModel", "connect_read_only"]

LATE_AFTER: Final = timedelta(minutes=15)
"""How long past a due bar check the heartbeat turns LATE."""

_ONE_MS: Final = timedelta(milliseconds=1)
_ZERO: Final = Decimal(0)
_OK: Final = "ok"


def connect_read_only(path: Path) -> sqlite3.Connection:
    """Open ``path`` with SQLite's ``mode=ro`` URI.

    Args:
        path: An existing ledger file.

    Returns:
        A connection that refuses every write.

    Raises:
        LedgerError: SQLite refused to open the file.
    """
    try:
        return sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        raise LedgerError(msg=f"cannot open {path} read-only: {exc}") from exc


class LedgerReadModel:
    """Console views read from a ledger file; implements ``ConsoleReadModel``."""

    def __init__(self, path: Path, *, clock: Callable[[], datetime] = now_utc) -> None:
        """Bind to a ledger file without opening it.

        Args:
            path: The ledger file; use :meth:`open` to check it exists.
            clock: Read time for the heartbeat; the wall clock by default.
        """
        self._path = path
        self._clock = clock

    @classmethod
    def open(cls, path: Path, *, clock: Callable[[], datetime] = now_utc) -> Self:
        """Return a read model over ``path``, which must already exist.

        Args:
            path: The ledger file.
            clock: Read time for the heartbeat.

        Returns:
            The read model; nothing is created on disk.

        Raises:
            FileNotFoundError: There is no file at ``path``.
        """
        if not path.is_file():
            raise FileNotFoundError(str(path))
        return cls(path, clock=clock)

    @property
    def demo_scenario(self) -> str | None:
        """Return ``None``: these are real figures."""
        return None

    def heartbeat(self) -> Heartbeat:
        """Return the bar-check timing seen in the ledger; never DOWN (no systemd here).

        Raises:
            LedgerError: On any SQLite failure or an unreadable row.
        """
        as_of = self._clock()
        rows = self._query(
            "SELECT json_extract(payload_json, '$.interval'), MAX(occurred_at) FROM events "
            "WHERE event_type = 'BarProcessed' "
            "GROUP BY json_extract(payload_json, '$.pair.base'), "
            "         json_extract(payload_json, '$.pair.quote'), "
            "         json_extract(payload_json, '$.interval')",
            (),
        )
        if not rows:
            return Heartbeat(
                state=HeartbeatState.OK, as_of=as_of, last_check_at=as_of, next_check_at=as_of
            )
        try:
            boundaries = [
                (datetime.fromisoformat(str(last)) + _ONE_MS, KlineInterval(str(interval)))
                for interval, last in rows
            ]
        except ValueError as exc:
            raise LedgerError(msg=f"heartbeat: unreadable bar check: {exc}") from exc
        last_check = max(boundary for boundary, _ in boundaries)
        next_check = min(boundary + interval_duration(iv) for boundary, iv in boundaries)
        late = as_of > next_check + LATE_AFTER
        return Heartbeat(
            state=HeartbeatState.LATE if late else HeartbeatState.OK,
            as_of=as_of,
            last_check_at=last_check,
            next_check_at=next_check,
        )

    def portfolio(self, range_: PortfolioRange = PortfolioRange.TODAY) -> PortfolioSummary:
        """Raise: the portfolio view lands with the home slice of #546."""
        msg = f"the portfolio ({range_.value}) is not yet available in the live read model"
        raise ViewNotAvailableError(msg)

    def positions(self) -> tuple[PositionRow, ...]:
        """Return the open positions of every strategy, oldest entry first.

        Raises:
            LedgerError: On any SQLite failure, an unreadable row, or an open
                position without an entry signal.
        """
        return tuple(detail.row for detail in self._details())

    def position(self, coin: str) -> PositionDetail | None:
        """Return the detail of the open position in ``coin``, or ``None``.

        Raises:
            LedgerError: As :meth:`positions`.
        """
        wanted = coin.upper()
        return next((d for d in self._details() if d.row.coin == wanted), None)

    def activity(self, *, include_quiet: bool = False) -> tuple[ActivityDay, ...]:
        """Raise: ``hermes activity`` reads the ledger itself until the activity slice."""
        scope = "all activity" if include_quiet else "activity"
        msg = f"{scope} is not yet available in the live read model"
        raise ViewNotAvailableError(msg)

    def shadow_test(self) -> ShadowTest:
        """Raise: the shadow test start date is not recorded in the ledger yet."""
        msg = "the shadow test status is not yet available in the live read model"
        raise ViewNotAvailableError(msg)

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def _query(self, sql: str, params: tuple[str, ...]) -> list[tuple[object, ...]]:
        """Run one query on a fresh read-only connection and close it."""
        try:
            with closing(connect_read_only(self._path)) as conn:
                rows: list[tuple[object, ...]] = conn.execute(sql, params).fetchall()
        except sqlite3.Error as exc:
            raise LedgerError(msg=f"console read failed: {exc}") from exc
        return rows

    def _details(self) -> list[PositionDetail]:
        details: list[PositionDetail] = []
        try:
            with closing(connect_read_only(self._path)) as conn:
                reader = _Reader(conn)
                for strategy in reader.strategies():
                    details.extend(build_detail(f) for f in reader.position_facts(strategy))
        except sqlite3.Error as exc:
            raise LedgerError(msg=f"console positions read failed: {exc}") from exc
        except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
            raise LedgerError(msg=f"console positions: unreadable row: {exc}") from exc
        return sorted(details, key=lambda d: (d.row.opened_at, d.row.coin))


class _Reader:
    """Row-level reads for one open connection."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def strategies(self) -> list[str]:
        rows = self._conn.execute(
            "SELECT DISTINCT strategy_id FROM portfolio_marks ORDER BY strategy_id"
        ).fetchall()
        return [str(row[0]) for row in rows]

    def position_facts(self, strategy: str) -> list[PositionFacts]:
        latest = self._conn.execute(
            "SELECT positions_json FROM portfolio_marks WHERE strategy_id = ? "
            "ORDER BY bar_close_time DESC, id DESC LIMIT 1",
            (strategy,),
        ).fetchone()
        if latest is None:
            return []
        facts: list[PositionFacts] = []
        for item in json.loads(str(latest[0])):
            base, quote = str(item["base"]), str(item["quote"])
            stop, atr, signal_at = self._entry_signal(strategy, base, quote)
            lots = self._open_lots(strategy, base)
            opened_at = min((lot.opened_at for lot in lots), default=signal_at)
            facts.append(
                PositionFacts(
                    strategy=strategy,
                    coin=base,
                    timeframe=self._timeframe(strategy, base, quote),
                    size=Decimal(item["quantity"]),
                    cost_native=Decimal(item["cost_basis"]),
                    current_price=Decimal(item["mark_price"]),
                    stop_price=stop,
                    atr=atr,
                    signal_at=signal_at,
                    lots=lots,
                    price_path=self._price_path(strategy, base, opened_at),
                    eur_per_usdt=self._eur_per_quote(quote),
                )
            )
        return facts

    def _timeframe(self, strategy: str, base: str, quote: str) -> str:
        row = self._conn.execute(
            "SELECT interval FROM portfolio_marks "
            "WHERE strategy_id = ? AND pair_base = ? AND pair_quote = ? "
            "ORDER BY bar_close_time DESC, id DESC LIMIT 1",
            (strategy, base, quote),
        ).fetchone()
        if row is None:
            row = self._conn.execute(
                "SELECT interval FROM portfolio_marks WHERE strategy_id = ? "
                "ORDER BY bar_close_time DESC, id DESC LIMIT 1",
                (strategy,),
            ).fetchone()
        return str(row[0])

    def _entry_signal(
        self, strategy: str, base: str, quote: str
    ) -> tuple[Decimal, Decimal, datetime]:
        row = self._conn.execute(
            "SELECT payload_json FROM events WHERE event_type = 'SignalEmitted' "
            "  AND json_extract(payload_json, '$.strategy_id.value') = ? "
            "  AND json_extract(payload_json, '$.pair.base') = ? "
            "  AND json_extract(payload_json, '$.pair.quote') = ? "
            "  AND json_extract(payload_json, '$.kind') = 'entry' "
            "ORDER BY occurred_at DESC, id DESC LIMIT 1",
            (strategy, base, quote),
        ).fetchone()
        if row is None:
            raise LedgerError(
                msg=f"open {base}/{quote} position of {strategy} has no entry signal in the ledger"
            )
        payload = json.loads(str(row[0]))
        return (
            Decimal(str(payload["stop_price"])),
            Decimal(str(payload["atr_context"])),
            datetime.fromisoformat(str(payload["occurred_at"])),
        )

    def _open_lots(self, strategy: str, base: str) -> tuple[LotFacts, ...]:
        rows = self._conn.execute(
            "SELECT l.remaining_qty, l.original_qty, l.opened_at, "
            "       t.price_eur, t.fee_eur, t.eur_status "
            "FROM lots l LEFT JOIN trades t ON t.id = l.trade_id "
            "WHERE l.strategy_id = ? AND l.asset = ? "
            "ORDER BY l.opened_at, l.lot_id",
            (strategy, base),
        ).fetchall()
        lots: list[LotFacts] = []
        for remaining, original, opened_at, price_eur, fee_eur, status in rows:
            remaining_qty = Decimal(str(remaining))
            if remaining_qty <= _ZERO:
                continue
            priced = status == _OK and price_eur is not None and fee_eur is not None
            lots.append(
                LotFacts(
                    remaining_qty=remaining_qty,
                    original_qty=Decimal(str(original)),
                    opened_at=datetime.fromisoformat(str(opened_at)),
                    price_eur=Decimal(str(price_eur)) if priced else None,
                    fee_eur=Decimal(str(fee_eur)) if priced else None,
                )
            )
        return tuple(lots)

    def _price_path(self, strategy: str, base: str, since: datetime) -> tuple[Decimal, ...]:
        rows = self._conn.execute(
            "SELECT positions_json FROM portfolio_marks WHERE strategy_id = ? "
            "ORDER BY bar_close_time, id",
            (strategy,),
        ).fetchall()
        path: dict[datetime, Decimal] = {}
        for (positions_json,) in rows:
            for item in json.loads(str(positions_json)):
                marked_at = datetime.fromisoformat(str(item["marked_at"]))
                if item["base"] == base and marked_at >= since:
                    path.setdefault(marked_at, Decimal(item["mark_price"]))
        return tuple(path[at] for at in sorted(path))

    def _eur_per_quote(self, quote: str) -> Decimal | None:
        row = self._conn.execute(
            "SELECT eur_rate FROM trades "
            "WHERE eur_status = 'ok' AND pair_quote = ? AND eur_rate IS NOT NULL "
            "ORDER BY executed_at DESC, id DESC LIMIT 1",
            (quote,),
        ).fetchone()
        if row is None:
            return None
        return 1 / Decimal(str(row[0]))
