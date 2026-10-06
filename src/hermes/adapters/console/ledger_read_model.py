"""The console read model over the SQLite ledger, read-only (#546).

Every view call opens the ledger with SQLite's ``mode=ro`` URI, runs its
queries and closes the connection again, so the console never writes, never
migrates and never holds a lock between two reads of a running ``hermes serve``.

Positions: the open set, size, cost and latest price come from the newest
portfolio mark of each strategy (#387), the stop from the entry signal, and the
EUR values from the recorded trades (#435). The heartbeat is derived from the
newest bar checks. The portfolio sums each strategy's equity from its marks and
capital seed (#405) and the closed trades' FIFO values; activity reuses the
ledger activity rows; the shadow test counts days from the capital seed. A view
the ledger cannot answer yet raises :class:`ViewNotAvailableError`.

Reference: openspec/changes/ledger-read-model/change.md (D-1, D-3 to D-9);
openspec/changes/live-home-screen/change.md (D-1 to D-6).
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Sequence
from contextlib import closing
from datetime import UTC, date, datetime, timedelta, tzinfo
from decimal import Decimal
from pathlib import Path
from typing import Final, Self

from hermes.adapters.console.ledger_activity import LedgerActivityReader
from hermes.application.console.activity import group_activity
from hermes.application.console.journal_activity import fold_checks, journal_activity
from hermes.application.console.logs import LogLine
from hermes.application.console.portfolio_figures import (
    ClosedPiece,
    EquityPoint,
    PortfolioFacts,
    SeedFacts,
    StrategyBook,
    build_portfolio,
    range_start,
)
from hermes.application.console.position_figures import LotFacts, PositionFacts, build_detail
from hermes.application.console.read_model import ViewNotAvailableError
from hermes.application.console.render import MADRID
from hermes.application.console.shadow_figures import build_shadow_test
from hermes.application.console.views import (
    ActivityDay,
    ConsoleBrief,
    Heartbeat,
    HeartbeatState,
    PortfolioRange,
    PortfolioSummary,
    PositionDetail,
    PositionRow,
    ShadowTest,
    WatchedPair,
)
from hermes.domain.errors import LedgerError
from hermes.domain.market_data.bar_schedule import interval_duration
from hermes.domain.market_data.kline import KlineInterval
from hermes.infrastructure.time import now_utc

__all__ = ["ACTIVITY_WINDOW", "LATE_AFTER", "LedgerReadModel", "connect_read_only"]

LATE_AFTER: Final = timedelta(minutes=15)
"""How long past a due bar check the heartbeat turns LATE."""

ACTIVITY_WINDOW: Final = timedelta(days=7)
"""How far back :meth:`LedgerReadModel.activity` reads by default."""

JOURNAL_CACHE: Final = timedelta(seconds=30)
"""How long one journal read serves the activity feed before it is read again."""

type JournalSource = Callable[[timedelta], Sequence[LogLine] | None]
"""Returns the hermes journal lines of the last ``window``, or ``None`` when it is not readable."""

type BriefSource = Callable[[], tuple[ConsoleBrief, ...]]
"""Returns the supervisor desk's published briefs, newest first."""

_NO_BOOK: Final = "the portfolio is not available yet: no portfolio mark or capital seed"

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

    def __init__(
        self,
        path: Path,
        *,
        clock: Callable[[], datetime] = now_utc,
        tz: tzinfo = MADRID,
        activity_window: timedelta = ACTIVITY_WINDOW,
        journal: JournalSource | None = None,
        briefs: BriefSource | None = None,
    ) -> None:
        """Bind to a ledger file without opening it.

        Args:
            path: The ledger file; use :meth:`open` to check it exists.
            clock: Read time of every view; the wall clock by default.
            tz: Timezone of the calendar days (activity days, "today", the
                tax year, shadow test days).
            activity_window: How far back :meth:`activity` reads.
            journal: Where restarts, warnings, errors and entry-condition counts
                come from; ``None`` or an unreadable journal leaves those rows
                out of the feed.
            briefs: The supervisor desk's published briefs; ``None`` leaves the
                Briefs screen "not available yet".
        """
        self._path = path
        self._clock = clock
        self._tz = tz
        self._activity_window = activity_window
        self._journal = journal
        self._journal_read: tuple[datetime, Sequence[LogLine] | None] | None = None
        self._briefs = briefs

    @classmethod
    def open(
        cls,
        path: Path,
        *,
        clock: Callable[[], datetime] = now_utc,
        tz: tzinfo = MADRID,
        activity_window: timedelta = ACTIVITY_WINDOW,
        journal: JournalSource | None = None,
        briefs: BriefSource | None = None,
    ) -> Self:
        """Return a read model over ``path``, which must already exist.

        Args:
            path: The ledger file.
            clock: Read time of every view.
            tz: Timezone of the calendar days.
            activity_window: How far back :meth:`activity` reads.
            journal: Journal source for the feed's journal rows; see ``__init__``.
            briefs: The desk's published briefs; see ``__init__``.

        Returns:
            The read model; nothing is created on disk.

        Raises:
            FileNotFoundError: There is no file at ``path``.
        """
        if not path.is_file():
            raise FileNotFoundError(str(path))
        return cls(
            path,
            clock=clock,
            tz=tz,
            activity_window=activity_window,
            journal=journal,
            briefs=briefs,
        )

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
        """Return the portfolio summed over every strategy, in EUR (live-home-screen D-1 to D-3).

        Raises:
            ViewNotAvailableError: The ledger has no portfolio mark and no capital
                seed yet, or the strategies are quoted in different currencies.
            LedgerError: On any SQLite failure or an unreadable row.
        """
        as_of = self._clock()
        year_start = range_start(PortfolioRange.YEAR, as_of, self._tz)
        try:
            with closing(connect_read_only(self._path)) as conn:
                reader = _Reader(conn)
                books, quote = reader.books()
                closed = reader.closed_pieces(year_start)
                newest = reader.eur_rate(quote) if quote is not None else None
                seeds = reader.seeds()
        except sqlite3.Error as exc:
            raise LedgerError(msg=f"console portfolio read failed: {exc}") from exc
        except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
            raise LedgerError(msg=f"console portfolio: unreadable row: {exc}") from exc
        if not books:
            raise ViewNotAvailableError(_NO_BOOK)
        details = self._details()
        open_net = (
            None
            if any(d.row.eur_pending for d in details)
            else sum((d.net for d in details), start=_ZERO)
        )
        facts = PortfolioFacts(
            as_of=as_of,
            tz=self._tz,
            books=books,
            closed=closed,
            open_net=open_net,
            eur_per_quote=None if newest is None else newest[0],
            seeds=seeds,
            quote=quote,
            eur_rate_date=None if newest is None else newest[1],
        )
        return build_portfolio(facts, range_)

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
        """Return the activity of the last ``activity_window`` by local day (live-home-screen D-5).

        Raises:
            LedgerError: On any SQLite failure or an unreadable payload.
        """
        since = self._clock() - self._activity_window
        try:
            with closing(connect_read_only(self._path)) as conn:
                items = LedgerActivityReader(conn).items_since(since.astimezone(UTC))
        except sqlite3.Error as exc:
            raise LedgerError(msg=f"console activity read failed: {exc}") from exc
        lines = self._journal_lines()
        if not lines:
            return group_activity(fold_checks(items), self._tz, include_quiet=include_quiet)
        from_journal = journal_activity(lines, since=since.astimezone(UTC))
        folded = fold_checks(items, from_journal.entry_counts)
        return group_activity((*folded, *from_journal.items), self._tz, include_quiet=include_quiet)

    def _journal_lines(self) -> Sequence[LogLine] | None:
        """Return the journal lines of the activity window, re-read at most every 30 s."""
        if self._journal is None:
            return None
        now = self._clock()
        cached = self._journal_read
        if cached is not None and now - cached[0] < JOURNAL_CACHE:
            return cached[1]
        lines = self._journal(self._activity_window)
        self._journal_read = (now, lines)
        return lines

    def shadow_test(self) -> ShadowTest:
        """Return the day count since the capital seed and the fills so far (live-home-screen D-4).

        Raises:
            LedgerError: On any SQLite failure or an unreadable row.
        """
        as_of = self._clock()
        try:
            with closing(connect_read_only(self._path)) as conn:
                first = conn.execute("SELECT MIN(seeded_at) FROM capital_seeds").fetchone()[0]
                seeded_at = None if first is None else datetime.fromisoformat(str(first))
                fills = 0
                if seeded_at is not None:
                    fills = int(
                        conn.execute(
                            "SELECT COUNT(*) FROM trades WHERE executed_at >= ?",
                            (seeded_at.astimezone(UTC).isoformat(),),
                        ).fetchone()[0]
                    )
                checked = int(
                    conn.execute(
                        "SELECT COUNT(*) FROM events WHERE event_type = 'FillQualityRecorded'"
                    ).fetchone()[0]
                )
        except sqlite3.Error as exc:
            raise LedgerError(msg=f"console shadow test read failed: {exc}") from exc
        except (TypeError, ValueError) as exc:
            raise LedgerError(msg=f"console shadow test: unreadable row: {exc}") from exc
        return build_shadow_test(
            seeded_at=seeded_at, as_of=as_of, tz=self._tz, fills=fills, quality_checked=checked
        )

    def market_watch(self) -> tuple[WatchedPair, ...]:
        """Return each checked stream's last bar check, its signal and the next check (#650).

        A stream is a (pair, interval) with at least one ``BarProcessed`` row.
        The signal of the last check is the ``SignalEmitted`` row written in
        the same processing cycle (same correlation id). No price is read.

        Raises:
            LedgerError: On any SQLite failure or an unreadable row.
        """
        rows = self._query(
            "SELECT json_extract(b.payload_json, '$.pair.base'), "
            "       json_extract(b.payload_json, '$.pair.quote'), "
            "       json_extract(b.payload_json, '$.interval'), b.occurred_at, "
            "       (SELECT json_extract(s.payload_json, '$.kind') FROM events s "
            "        WHERE s.event_type = 'SignalEmitted' "
            "          AND s.correlation_id = b.correlation_id "
            "        ORDER BY s.id DESC LIMIT 1) "
            "FROM events b "
            "WHERE b.event_type = 'BarProcessed' AND b.id = ("
            "  SELECT n.id FROM events n WHERE n.event_type = 'BarProcessed' "
            "    AND json_extract(n.payload_json, '$.pair.base') "
            "        = json_extract(b.payload_json, '$.pair.base') "
            "    AND json_extract(n.payload_json, '$.pair.quote') "
            "        = json_extract(b.payload_json, '$.pair.quote') "
            "    AND json_extract(n.payload_json, '$.interval') "
            "        = json_extract(b.payload_json, '$.interval') "
            "  ORDER BY n.occurred_at DESC, n.id DESC LIMIT 1)",
            (),
        )
        watched: list[WatchedPair] = []
        try:
            for base, quote, interval, occurred_at, signal in rows:
                timeframe = KlineInterval(str(interval))
                last = datetime.fromisoformat(str(occurred_at)) + _ONE_MS
                watched.append(
                    WatchedPair(
                        coin=str(base),
                        quote=str(quote),
                        timeframe=timeframe.value,
                        last_check_at=last,
                        signal=None if signal is None else str(signal),
                        next_check_at=last + interval_duration(timeframe),
                    )
                )
        except ValueError as exc:
            raise LedgerError(msg=f"market watch: unreadable bar check: {exc}") from exc
        return tuple(sorted(watched, key=lambda w: (w.coin, w.quote, w.timeframe)))

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def briefs(self) -> tuple[ConsoleBrief, ...]:
        """Return the supervisor desk's published briefs, newest first (#720).

        Raises:
            ViewNotAvailableError: No desk was given to read them from.
        """
        if self._briefs is None:
            msg = "briefs: no supervisor desk to read them from"
            raise ViewNotAvailableError(msg)
        return self._briefs()

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
                    eur_per_usdt=self.eur_per_quote(quote),
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

    def eur_per_quote(self, quote: str) -> Decimal | None:
        """Return EUR per quote unit at the newest priced trade or seed rate.

        Reference: ``ledger-read-model`` D-4; ``live-home-screen`` D-1.
        """
        newest = self.eur_rate(quote)
        return None if newest is None else newest[0]

    def eur_rate(self, quote: str) -> tuple[Decimal, date] | None:
        """Return EUR per quote unit and its ECB rate date, by the newest-rate rule (#692)."""
        row = self._conn.execute(
            "SELECT eur_rate, eur_rate_date, 1, executed_at, id FROM trades "
            "WHERE eur_status = 'ok' AND pair_quote = ? AND eur_rate IS NOT NULL "
            "UNION ALL "
            "SELECT eur_rate, eur_rate_date, 0, seeded_at, id FROM capital_seeds "
            "WHERE eur_status = 'ok' AND asset = ? AND eur_rate IS NOT NULL "
            "ORDER BY 2 DESC, 3 DESC, 4 DESC, 5 DESC LIMIT 1",
            (quote, quote),
        ).fetchone()
        if row is None:
            return None
        return 1 / Decimal(str(row[0])), date.fromisoformat(str(row[1]))

    def books(self) -> tuple[tuple[StrategyBook, ...], str | None]:
        """Return each strategy's equity history and the one quote currency they share."""
        seeds = {
            str(strategy): (str(asset), Decimal(str(amount)), datetime.fromisoformat(str(at)))
            for strategy, asset, amount, at in self._conn.execute(
                "SELECT strategy_id, asset, amount, seeded_at FROM capital_seeds"
            ).fetchall()
        }
        marks: dict[str, list[tuple[datetime, Decimal, Decimal, Decimal, str]]] = {}
        for strategy, close, quote, cash, market, equity in self._conn.execute(
            "SELECT strategy_id, bar_close_time, quote, cash, market_value, equity "
            "FROM portfolio_marks ORDER BY bar_close_time, id"
        ).fetchall():
            marks.setdefault(str(strategy), []).append(
                (
                    datetime.fromisoformat(str(close)) + _ONE_MS,
                    Decimal(str(cash)),
                    Decimal(str(market)),
                    Decimal(str(equity)),
                    str(quote),
                )
            )
        quotes = {asset for asset, _, _ in seeds.values()} | {
            row[4] for rows in marks.values() for row in rows
        }
        if len(quotes) > 1:
            msg = f"the portfolio is not available: strategies are quoted in {sorted(quotes)}"
            raise ViewNotAvailableError(msg)
        books: list[StrategyBook] = []
        for strategy in sorted(seeds.keys() | marks.keys()):
            seed = seeds.get(strategy)
            rows = marks.get(strategy, [])
            points: list[EquityPoint] = []
            if seed is not None and (not rows or seed[2] < rows[0][0]):
                points.append(EquityPoint(at=seed[2], equity=seed[1]))
            points.extend(EquityPoint(at=at, equity=equity) for at, _, _, equity, _ in rows)
            if rows:
                cash, invested = rows[-1][1], rows[-1][2]
            else:
                cash, invested = (seed[1] if seed is not None else _ZERO), _ZERO
            books.append(StrategyBook(points=tuple(points), cash=cash, invested=invested))
        return tuple(books), next(iter(quotes), None)

    def closed_pieces(self, since: datetime) -> tuple[ClosedPiece, ...]:
        """Return the coin-sale lot consumptions filled since ``since`` (live-home-screen D-2)."""
        rows = self._conn.execute(
            "SELECT c.consumed_at, c.proceeds_eur, c.cost_eur, c.fee_eur, "
            "       c.consumed_qty, l.original_qty, b.fee_eur, b.eur_status "
            "FROM lot_consumptions c "
            "JOIN trades s ON s.id = c.trade_id "
            "JOIN lots l ON l.lot_id = c.lot_id "
            "LEFT JOIN trades b ON b.id = l.trade_id "
            "WHERE c.consumed_asset = s.pair_base AND c.consumed_at >= ? "
            "ORDER BY c.consumed_at, c.id",
            (since.astimezone(UTC).isoformat(),),
        ).fetchall()
        pieces: list[ClosedPiece] = []
        for at, proceeds, cost, sell_fee, qty, original, buy_fee, buy_status in rows:
            buy_share = None
            if buy_status == _OK and buy_fee is not None:
                buy_share = Decimal(str(buy_fee)) * Decimal(str(qty)) / Decimal(str(original))
            pieces.append(
                ClosedPiece(
                    at=datetime.fromisoformat(str(at)),
                    proceeds=_optional(proceeds),
                    cost=_optional(cost),
                    buy_fee=buy_share,
                    sell_fee=_optional(sell_fee),
                )
            )
        return tuple(pieces)

    def seeds(self) -> tuple[SeedFacts, ...]:
        """Return every capital seed, with its EUR value once priced (live-home-screen D-3)."""
        rows = self._conn.execute(
            "SELECT amount, seeded_at, value_eur, eur_status, eur_rate_date FROM capital_seeds "
            "ORDER BY seeded_at, id"
        ).fetchall()
        return tuple(
            SeedFacts(
                amount=Decimal(str(amount)),
                seeded_at=datetime.fromisoformat(str(at)),
                value_eur=_optional(value) if status == _OK else None,
                rate_date=(
                    date.fromisoformat(str(rate_date))
                    if status == _OK and rate_date is not None
                    else None
                ),
            )
            for amount, at, value, status, rate_date in rows
        )


def _optional(value: object) -> Decimal | None:
    return None if value is None else Decimal(str(value))
