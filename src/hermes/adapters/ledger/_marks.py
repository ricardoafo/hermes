"""Portfolio marks, stop marks, spread samples and pair correlations."""

import json
import sqlite3
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from hermes.adapters.ledger._ledger_core import _LedgerCore
from hermes.adapters.ledger.codecs import (
    iso_utc,
)
from hermes.domain.errors import LedgerError
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.market_data.spread_sample import SpreadSample
from hermes.domain.portfolio.correlation import PairCorrelation
from hermes.domain.portfolio.mark import PortfolioMark
from hermes.domain.portfolio.position import Position
from hermes.domain.portfolio.snapshot import PortfolioSnapshot
from hermes.domain.strategy.stop_mark import StopMark, StopRecord
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.strategy_id import StrategyId


class _MarkStore(_LedgerCore):
    """Portfolio marks, stop marks, spread samples and pair correlations."""

    def record_portfolio_mark(self, mark: PortfolioMark) -> bool:
        """Store the portfolio as it stood after one processed bar.

        Args:
            mark: The snapshot and the bar it followed.

        Returns:
            ``False`` when that bar's mark is already stored (a replay after a
            restart); the stored row is kept.

        Raises:
            LedgerError: On any SQLite failure.
        """
        snap = mark.snapshot
        positions = [
            {
                "base": p.pair.base.value,
                "quote": p.pair.quote.value,
                "quantity": str(p.quantity),
                "cost_basis": str(p.cost_basis),
                "mark_price": str(p.mark_price),
                "marked_at": iso_utc(p.marked_at),
            }
            for p in sorted(snap.positions.values(), key=lambda p: str(p.pair))
        ]
        with self._write("record_portfolio_mark"):
            inserted = self._conn.execute(
                "INSERT OR IGNORE INTO portfolio_marks (strategy_id, pair_base, pair_quote, "
                "interval, bar_close_time, quote, starting_cash, cash, market_value, equity, "
                "realized_pnl, peak_equity, positions_json, recorded_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    snap.strategy_id.value,
                    mark.pair.base.value,
                    mark.pair.quote.value,
                    mark.interval.value,
                    iso_utc(mark.bar_close_time),
                    snap.quote.value,
                    str(snap.starting_cash),
                    str(snap.cash),
                    str(snap.market_value),
                    str(snap.equity),
                    str(snap.realized_pnl),
                    str(snap.peak_equity),
                    json.dumps(positions, sort_keys=True),
                    iso_utc(self._now()),
                ),
            ).rowcount
        return bool(inserted)

    def portfolio_marks(
        self, strategy_id: StrategyId, start: datetime, end: datetime
    ) -> tuple[PortfolioMark, ...]:
        """Return the strategy's marks with ``start <= bar_close_time < end``, oldest first.

        Raises:
            LedgerError: On any SQLite failure or an unreadable row.
        """
        try:
            rows = self._conn.execute(
                "SELECT strategy_id, pair_base, pair_quote, interval, bar_close_time, quote, "
                "starting_cash, cash, realized_pnl, peak_equity, positions_json "
                "FROM portfolio_marks "
                "WHERE strategy_id = ? AND bar_close_time >= ? AND bar_close_time < ? "
                "ORDER BY bar_close_time, id",
                (strategy_id.value, iso_utc(start), iso_utc(end)),
            ).fetchall()
        except sqlite3.Error as exc:
            raise LedgerError(msg=f"portfolio_marks failed: {exc}") from exc
        return tuple(_row_to_mark(row) for row in rows)

    def latest_portfolio_mark(self, strategy_id: StrategyId) -> PortfolioMark | None:
        """Return the strategy's newest mark, or ``None`` before the first bar.

        Raises:
            LedgerError: On any SQLite failure or an unreadable row.
        """
        try:
            row = self._conn.execute(
                "SELECT strategy_id, pair_base, pair_quote, interval, bar_close_time, quote, "
                "starting_cash, cash, realized_pnl, peak_equity, positions_json "
                "FROM portfolio_marks WHERE strategy_id = ? "
                "ORDER BY bar_close_time DESC, id DESC LIMIT 1",
                (strategy_id.value,),
            ).fetchone()
        except sqlite3.Error as exc:
            raise LedgerError(msg=f"latest_portfolio_mark failed: {exc}") from exc
        return None if row is None else _row_to_mark(row)

    def record_stop_mark(self, mark: StopMark) -> bool:
        """Store the stop a strategy evaluated on one bar while holding a position.

        Args:
            mark: The stop record and the bar it followed.

        Returns:
            ``False`` when that bar's mark is already stored (a replay after a
            restart); the stored row is kept.

        Raises:
            LedgerError: On any SQLite failure.
        """
        record = mark.record
        with self._write("record_stop_mark"):
            inserted = self._conn.execute(
                "INSERT OR IGNORE INTO stop_marks (strategy_id, pair_base, pair_quote, "
                "entry_time, bar_close_time, stop_price, chandelier_level, atr, "
                "high_since_entry, stop_moved, recorded_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    mark.strategy_id.value,
                    mark.pair.base.value,
                    mark.pair.quote.value,
                    iso_utc(record.entry_time),
                    iso_utc(mark.bar_close_time),
                    str(record.stop_price),
                    str(record.chandelier_level),
                    str(record.atr),
                    None if record.high_since_entry is None else str(record.high_since_entry),
                    None if record.stop_moved is None else int(record.stop_moved),
                    iso_utc(self._now()),
                ),
            ).rowcount
        return bool(inserted)

    def latest_stop_mark(
        self, strategy_id: StrategyId, pair: Pair, entry_time: datetime
    ) -> StopMark | None:
        """Return the newest mark of the position that opened at ``entry_time``.

        Keyed on the entry time, so a new position never inherits the stop of an
        earlier one on the same pair.

        Raises:
            LedgerError: On any SQLite failure or an unreadable row.
        """
        try:
            row = self._conn.execute(
                "SELECT strategy_id, pair_base, pair_quote, entry_time, bar_close_time, "
                "stop_price, chandelier_level, atr, high_since_entry, stop_moved "
                "FROM stop_marks "
                "WHERE strategy_id = ? AND pair_base = ? AND pair_quote = ? AND entry_time = ? "
                "ORDER BY bar_close_time DESC LIMIT 1",
                (strategy_id.value, pair.base.value, pair.quote.value, iso_utc(entry_time)),
            ).fetchone()
        except sqlite3.Error as exc:
            raise LedgerError(msg=f"latest_stop_mark failed: {exc}") from exc
        return None if row is None else _row_to_stop_mark(row)

    def record_spread_sample(self, sample: SpreadSample) -> bool:
        """Store one sampled spread and the 1-hour median it closes.

        Args:
            sample: The sample.

        Returns:
            ``False`` when that book is already stored (a cached book read
            twice); the stored row is kept.

        Raises:
            LedgerError: On any SQLite failure.
        """
        with self._write("record_spread_sample"):
            inserted = self._conn.execute(
                "INSERT OR IGNORE INTO spread_samples (pair_base, pair_quote, book_time, "
                "sampled_at, spread, median_spread_1h, samples_1h, recorded_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    sample.pair.base.value,
                    sample.pair.quote.value,
                    iso_utc(sample.book_time.astimezone(UTC)),
                    iso_utc(sample.sampled_at.astimezone(UTC)),
                    str(sample.spread),
                    str(sample.median_spread_1h),
                    sample.samples_1h,
                    iso_utc(self._now()),
                ),
            ).rowcount
        return bool(inserted)

    def spread_samples(
        self, pair: Pair, start: datetime, end: datetime
    ) -> tuple[SpreadSample, ...]:
        """Return the pair's samples with ``start <= book_time < end``, oldest first.

        Raises:
            LedgerError: On any SQLite failure or an unreadable row.
        """
        try:
            rows = self._conn.execute(
                "SELECT pair_base, pair_quote, book_time, sampled_at, spread, "
                "median_spread_1h, samples_1h FROM spread_samples "
                "WHERE pair_base = ? AND pair_quote = ? AND book_time >= ? AND book_time < ? "
                "ORDER BY book_time",
                (
                    pair.base.value,
                    pair.quote.value,
                    iso_utc(start.astimezone(UTC)),
                    iso_utc(end.astimezone(UTC)),
                ),
            ).fetchall()
        except sqlite3.Error as exc:
            raise LedgerError(msg=f"spread_samples failed: {exc}") from exc
        return tuple(_row_to_spread_sample(row) for row in rows)

    def record_pair_correlation(self, correlation: PairCorrelation) -> bool:
        """Store one pair correlation.

        Args:
            correlation: The value and the window it covers.

        Returns:
            ``False`` when that (pairs, window, as-of) is already stored (a
            1D bar replayed after a restart); the stored row is kept.

        Raises:
            LedgerError: On any SQLite failure.
        """
        with self._write("record_pair_correlation"):
            inserted = self._conn.execute(
                "INSERT OR IGNORE INTO pair_correlations (pair_a, pair_b, window_days, as_of, "
                "correlation, sample_count, computed_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    str(correlation.pair_a),
                    str(correlation.pair_b),
                    correlation.window_days,
                    iso_utc(correlation.as_of.astimezone(UTC)),
                    correlation.correlation,
                    correlation.sample_count,
                    iso_utc(self._now()),
                ),
            ).rowcount
        return bool(inserted)

    def pair_correlations(self, start: datetime, end: datetime) -> tuple[PairCorrelation, ...]:
        """Return the correlations with ``start <= as_of < end``, oldest first.

        Raises:
            LedgerError: On any SQLite failure or an unreadable row.
        """
        try:
            rows = self._conn.execute(
                "SELECT pair_a, pair_b, window_days, as_of, correlation, sample_count "
                "FROM pair_correlations WHERE as_of >= ? AND as_of < ? "
                "ORDER BY as_of, pair_a, pair_b, window_days",
                (iso_utc(start.astimezone(UTC)), iso_utc(end.astimezone(UTC))),
            ).fetchall()
        except sqlite3.Error as exc:
            raise LedgerError(msg=f"pair_correlations failed: {exc}") from exc
        return tuple(_row_to_pair_correlation(row) for row in rows)


def _row_to_mark(row: tuple[Any, ...]) -> PortfolioMark:
    """Rebuild a :class:`PortfolioMark` from a ``portfolio_marks`` row."""
    (
        strategy,
        base,
        quote_ccy,
        interval,
        close_time,
        quote,
        starting,
        cash,
        realized,
        peak,
        positions_json,
    ) = row
    try:
        positions = {}
        for item in json.loads(positions_json):
            pair = Pair(base=Currency(item["base"]), quote=Currency(item["quote"]))
            positions[pair] = Position(
                pair=pair,
                quantity=Decimal(item["quantity"]),
                cost_basis=Decimal(item["cost_basis"]),
                mark_price=Decimal(item["mark_price"]),
                marked_at=datetime.fromisoformat(item["marked_at"]),
            )
        snapshot = PortfolioSnapshot(
            strategy_id=StrategyId(strategy),
            quote=Currency(quote),
            starting_cash=Decimal(starting),
            cash=Decimal(cash),
            positions=positions,
            realized_pnl=Decimal(realized),
            peak_equity=Decimal(peak),
        )
        return PortfolioMark(
            snapshot=snapshot,
            pair=Pair(base=Currency(base), quote=Currency(quote_ccy)),
            interval=KlineInterval(interval),
            bar_close_time=datetime.fromisoformat(close_time),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise LedgerError(msg=f"portfolio mark row is unreadable: {exc}") from exc


def _row_to_stop_mark(row: tuple[Any, ...]) -> StopMark:
    """Rebuild a :class:`StopMark` from a ``stop_marks`` row."""
    strategy, base, quote, entry, close_time, stop, level, atr, high, moved = row
    try:
        return StopMark(
            strategy_id=StrategyId(strategy),
            pair=Pair(base=Currency(base), quote=Currency(quote)),
            bar_close_time=datetime.fromisoformat(close_time),
            record=StopRecord(
                entry_time=datetime.fromisoformat(entry),
                stop_price=Decimal(stop),
                chandelier_level=Decimal(level),
                atr=Decimal(atr),
                high_since_entry=None if high is None else Decimal(high),
                stop_moved=None if moved is None else bool(moved),
            ),
        )
    except (TypeError, ValueError, ArithmeticError) as exc:
        raise LedgerError(msg=f"stop mark row is unreadable: {exc}") from exc


def _row_to_spread_sample(row: tuple[Any, ...]) -> SpreadSample:
    """Rebuild a :class:`SpreadSample` from a ``spread_samples`` row."""
    base, quote, book_time, sampled_at, spread, median, count = row
    try:
        return SpreadSample(
            pair=Pair(base=Currency(base), quote=Currency(quote)),
            sampled_at=datetime.fromisoformat(sampled_at),
            book_time=datetime.fromisoformat(book_time),
            spread=Decimal(spread),
            median_spread_1h=Decimal(median),
            samples_1h=int(count),
        )
    except (TypeError, ValueError, ArithmeticError) as exc:
        raise LedgerError(msg=f"spread sample row is unreadable: {exc}") from exc


def _pair_from_text(text: str) -> Pair:
    """Parse the ``BASE/QUOTE`` form :meth:`Pair.__str__` writes."""
    base, _, quote = text.partition("/")
    return Pair(base=Currency(base), quote=Currency(quote))


def _row_to_pair_correlation(row: tuple[Any, ...]) -> PairCorrelation:
    """Rebuild a :class:`PairCorrelation` from a ``pair_correlations`` row."""
    pair_a, pair_b, window, as_of, value, count = row
    try:
        return PairCorrelation(
            pair_a=_pair_from_text(pair_a),
            pair_b=_pair_from_text(pair_b),
            window_days=int(window),
            as_of=datetime.fromisoformat(as_of),
            correlation=float(value),
            sample_count=int(count),
        )
    except (TypeError, ValueError, ArithmeticError) as exc:
        raise LedgerError(msg=f"pair correlation row is unreadable: {exc}") from exc
