"""ShadowExecutor: the live-shadow side of the shared fill kernel.

``hermes serve`` hands every strategy signal to :meth:`ShadowExecutor.execute`.
The executor does what the backtest replay does around the
:class:`~hermes.application.execution.kernel.FillKernel`, with live inputs:

- **Book** (D-1, D-2): a live depth snapshot when the signal's bar closed at
  most :data:`LIVE_BOOK_WINDOW` ago and the snapshot succeeds; otherwise the
  pre-registration §6.2 synthetic book of that bar. A live fill is stamped with
  the snapshot time, a synthetic one with the signal time (as in the backtest),
  but never before the strategy's capital seed (``not_before``): a bar that
  closed just before a first boot would otherwise spend capital the ledger
  records only from the boot on (change ``405-usdt-fifo`` D-1).
- **Risk and kill inputs** (D-3): computed from the book, the BTC/USDT 1d
  closes the loop has seen, the last recorded fill slippage and the portfolio.
- **Records** (D-4): the fill goes to the ledger through the trade recorder
  (``TradeExecuted`` v2 with ``strategy_id``), then one
  :class:`~hermes.domain.events.fill_quality_recorded.FillQualityRecorded`
  with the drift against the §6.2 model price of the same order.
- **Idempotency** (D-5): a signal whose pair and side already has a trade at or
  after the signal time is not executed again (a bar re-evaluated after a crash).

The executor performs no logging and reads no wall clock of its own; the
caller logs from the returned :class:`ShadowExecution`.

Reference: openspec/changes/353-fill-kernel-in-serve/change.md D-1..D-5.
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Final, Protocol

from hermes.application.execution.kernel import (
    Executed,
    ExecutionOutcome,
    FillKernel,
    MarketContext,
)
from hermes.application.execution.prereg_book import ExecutionModel
from hermes.application.order_router.risk_engine import RiskEngine
from hermes.application.strategy_protocol.kill_trigger_evaluator import ExperimentState
from hermes.domain.errors import InfraError
from hermes.domain.events.fill_quality_recorded import BookSource, FillQualityRecorded
from hermes.domain.events.trade_executed import TradeExecuted, TradeSide
from hermes.domain.fees.fee_schedule import FeeSchedule
from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.market_data.orderbook import Orderbook
from hermes.domain.risk.depeg_halt import DepegHalt
from hermes.domain.risk.depeg_warn import DepegWarn
from hermes.domain.risk.macro_atr_halt import MacroATRHalt
from hermes.domain.risk.max_slippage import MaxSlippage
from hermes.domain.risk.portfolio_drawdown import PortfolioDrawdown
from hermes.domain.risk.stale_book import StaleBook
from hermes.domain.risk.state import MarketSnapshot
from hermes.domain.strategy.signal import SignalKind, StrategySignal
from hermes.domain.trading.execution_quality import (
    book_mid,
    book_spread,
    drift_bps,
    slippage_vs_mid,
)
from hermes.domain.trading.fill import Fill
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.strategy_id import StrategyId
from hermes.ports.clock import ClockPort
from hermes.ports.fill_model import FillModelPort
from hermes.ports.ledger import ExecutionLedgerPort
from hermes.ports.orderbook_snapshot import OrderbookSnapshotPort

__all__ = [
    "LIVE_BOOK_WINDOW",
    "MACRO_PAIR",
    "AlreadyFilled",
    "FillRecorder",
    "ShadowExecution",
    "ShadowExecutor",
    "ShadowOutcome",
    "shadow_risk_engine",
]

LIVE_BOOK_WINDOW: Final = timedelta(minutes=5)
"""Oldest bar close a live book may stand in for (D-2)."""

MACRO_PAIR: Final = Pair(base=Currency.BTC, quote=Currency.USDT)
"""Pair whose 1d log-return feeds risk rule RO-07."""

_ZERO = Decimal("0")
_MACRO_WINDOW: Final = 2


def shadow_risk_engine() -> RiskEngine:
    """Return the risk overlay serve applies to entries (D-3).

    RO-02 (spread spike) and RO-01 (correlation lock) are left out: serve has
    no 1-hour spread median and the portfolio state carries no correlations,
    and a rule fed an invented input would change trading without evidence.

    Returns:
        An engine with RO-03, RO-04, RO-05, RO-06, RO-07 and RO-08.
    """
    return RiskEngine(
        rules=[
            MaxSlippage(),
            StaleBook(),
            DepegWarn(),
            DepegHalt(),
            MacroATRHalt(),
            PortfolioDrawdown(),
        ]
    )


class FillRecorder(Protocol):
    """Persists one fill as a ledger trade (``TradeRecorder`` in production)."""

    def record(self, fill: Fill, *, correlation_id: str) -> int:
        """Store ``fill`` and return its trade row id."""
        ...


@dataclass(frozen=True, slots=True)
class AlreadyFilled:
    """The signal's pair and side already traded at or after the signal time.

    Attributes:
        trade: The recorded trade that made this signal a duplicate.
    """

    trade: TradeExecuted


type ShadowOutcome = ExecutionOutcome | AlreadyFilled


@dataclass(frozen=True, slots=True)
class ShadowExecution:
    """What one :meth:`ShadowExecutor.execute` call did.

    Attributes:
        outcome: The kernel's outcome, or :class:`AlreadyFilled`.
        book_source: The book handed to the kernel; ``None`` for
            :class:`AlreadyFilled`.
        quality: The quality row written for an executed fill.
        snapshot_error: Why the live snapshot failed, when it was tried and
            the synthetic book stood in.
    """

    outcome: ShadowOutcome
    book_source: BookSource | None = None
    quality: FillQualityRecorded | None = None
    snapshot_error: str | None = None


class ShadowExecutor:
    """Executes live-shadow signals through the shared fill kernel.

    Args:
        kernel: The fill kernel (owns the portfolio tracker).
        fill_model: The fill model the kernel was built with; reused to price
            the same order on the §6.2 book for the drift.
        books: Live depth snapshots.
        recorder: Writes each fill as a ledger trade.
        ledger: Recorded trades, and the fill-quality rows.
        clock: Time source.
        fees_at: Fee schedule in force for a pair at a time (pre-reg §6.1).
        strategy_id: The strategy bucket the trades belong to.
        model: The §6.2 execution book.
        live_window: Oldest bar close a live book may stand in for.
        not_before: Earliest time a fill may be stamped with: the capital
            seed's date, so the quote-asset FIFO always has the seed lot.
    """

    def __init__(
        self,
        *,
        kernel: FillKernel,
        fill_model: FillModelPort,
        books: OrderbookSnapshotPort,
        recorder: FillRecorder,
        ledger: ExecutionLedgerPort,
        clock: ClockPort,
        fees_at: Callable[[datetime, Pair], FeeSchedule],
        strategy_id: StrategyId,
        model: ExecutionModel | None = None,
        live_window: timedelta = LIVE_BOOK_WINDOW,
        not_before: datetime | None = None,
    ) -> None:
        """Bind the collaborators and load the last recorded fill slippage.

        Raises:
            LedgerError: The fill-quality rows cannot be read.
        """
        self._kernel = kernel
        self._fill_model = fill_model
        self._books = books
        self._recorder = recorder
        self._ledger = ledger
        self._clock = clock
        self._fees_at = fees_at
        self._strategy_id = strategy_id
        self._model = model if model is not None else ExecutionModel()
        self._live_window = live_window
        self._not_before = not_before
        self._macro_closes: deque[tuple[datetime, Decimal]] = deque(maxlen=_MACRO_WINDOW)
        recorded = ledger.fill_quality_events(strategy_id)
        last = recorded[-1].slippage if recorded else None
        self._last_slippage = last if last is not None else _ZERO

    @property
    def model(self) -> ExecutionModel:
        """The §6.2 execution book this executor falls back to."""
        return self._model

    def observe(self, bar: Bar) -> None:
        """Remember the closes of the BTC/USDT 1d bars, for RO-07.

        Args:
            bar: Any bar the loop has seen, warm-up included, in delivery order.
        """
        if bar.pair != MACRO_PAIR or bar.interval is not KlineInterval.D1:
            return
        if self._macro_closes and bar.close_time <= self._macro_closes[-1][0]:
            return
        self._macro_closes.append((bar.close_time, bar.close))

    async def execute(
        self, signal: StrategySignal, bar: Bar, *, correlation_id: str
    ) -> ShadowExecution:
        """Execute one signal of ``bar`` and record its fill.

        Args:
            signal: The strategy signal.
            bar: The bar that produced it.
            correlation_id: Stamped on the trade and its quality row.

        Returns:
            What happened; see :class:`ShadowExecution`.

        Raises:
            LedgerError: The trade or quality row could not be written.
        """
        pair = bar.pair
        side = TradeSide.BUY if signal.kind is SignalKind.ENTRY else TradeSide.SELL
        prior = self._traded_since(pair, side, signal.signal_time)
        if prior is not None:
            return ShadowExecution(outcome=AlreadyFilled(trade=prior))
        now = self._clock.now()
        book, source, error = await self._book(signal, bar, now)
        at = (
            book.snapshot_time
            if book is not None and source is BookSource.LIVE
            else signal.signal_time
        )
        if self._not_before is not None and at < self._not_before:
            at = self._not_before
        fees = self._fees_at(at, pair)
        market = MarketContext(
            orderbook=book,
            fees=fees,
            at=at,
            market_snapshot=self._market_snapshot(pair, book, bar, now),
            experiment_state=self._experiment_state(),
        )
        outcome = self._kernel.execute(signal, pair, market, correlation_id=correlation_id)
        quality = None
        if isinstance(outcome, Executed):
            if book is None:  # pragma: no cover — the kernel declines a signal without a book
                raise RuntimeError("the kernel filled an order without a book")
            self._recorder.record(outcome.fill, correlation_id=correlation_id)
            quality = self._quality(
                outcome.fill,
                signal=signal,
                bar=bar,
                book=book,
                source=source,
                fees=fees,
                correlation_id=correlation_id,
            )
            self._ledger.append_event(quality)
            if quality.slippage is not None:
                self._last_slippage = quality.slippage
        return ShadowExecution(
            outcome=outcome, book_source=source, quality=quality, snapshot_error=error
        )

    def _traded_since(self, pair: Pair, side: TradeSide, since: datetime) -> TradeExecuted | None:
        """Return a recorded trade of ``pair`` and ``side`` at or after ``since``."""
        for trade in self._ledger.trade_events(self._strategy_id):
            if trade.pair == pair and trade.side is side and trade.occurred_at >= since:
                return trade
        return None

    async def _book(
        self, signal: StrategySignal, bar: Bar, now: datetime
    ) -> tuple[Orderbook | None, BookSource, str | None]:
        """Return the book to fill against, where it came from and any snapshot error."""
        error = None
        if now - bar.close_time <= self._live_window:
            try:
                return await self._books.snapshot(bar.pair), BookSource.LIVE, None
            except InfraError as exc:
                error = str(exc)
        return self._model.book(signal, bar), BookSource.SYNTHETIC, error

    def _market_snapshot(
        self, pair: Pair, book: Orderbook | None, bar: Bar, now: datetime
    ) -> MarketSnapshot:
        """Build the risk overlay inputs (D-3)."""
        spread = book_spread(book) if book is not None else _ZERO
        book_time = book.snapshot_time if book is not None else bar.close_time
        return MarketSnapshot(
            pair=pair,
            current_spread=spread,
            median_spread_1h=spread,
            orderbook_age_seconds=max(0.0, (now - book_time).total_seconds()),
            orderbook_timestamp=book_time,
            realized_slippage_last=self._last_slippage,
            stable_price=None,
            btc_log_return_1d=self._btc_log_return_1d(),
            candidate_pair=pair,
        )

    def _btc_log_return_1d(self) -> float | None:
        """Return ``ln(close_t / close_t-1)`` of the last two BTC/USDT 1d bars."""
        if len(self._macro_closes) < _MACRO_WINDOW:
            return None
        (_, previous), (_, latest) = self._macro_closes
        if previous <= 0 or latest <= 0:
            return None
        return math.log(latest / previous)

    def _experiment_state(self) -> ExperimentState:
        """Build the kill inputs (D-3): drawdown from the tracker, the rest not yet supplied."""
        snapshot = self._kernel.tracker.snapshot
        peak = snapshot.peak_equity
        drawdown = _ZERO if peak <= 0 else max(_ZERO, (peak - snapshot.equity) / peak)
        return ExperimentState(
            max_drawdown=float(drawdown), days_since_equity_high=0.0, sa9_halt=False
        )

    def _quality(
        self,
        fill: Fill,
        *,
        signal: StrategySignal,
        bar: Bar,
        book: Orderbook,
        source: BookSource,
        fees: FeeSchedule,
        correlation_id: str,
    ) -> FillQualityRecorded:
        """Measure ``fill`` against the book it walked and the §6.2 model (D-4)."""
        side = fill.order.side
        model_price = None
        model_book = self._model.book(signal, bar)
        if model_book is not None:
            model_fill = self._fill_model.fill_order(fill.order, model_book, fill.timestamp, fees)
            if isinstance(model_fill, Fill):
                model_price = model_fill.avg_price
        mid = book_mid(book)
        return FillQualityRecorded(
            occurred_at=fill.timestamp,
            correlation_id=correlation_id,
            strategy_id=fill.strategy_id,
            pair=fill.order.pair,
            side=TradeSide(side.value),
            qty=fill.executed_qty,
            book_source=source,
            book_time=book.snapshot_time,
            fill_price=fill.avg_price,
            model_price=model_price,
            drift_bps=None if model_price is None else drift_bps(side, fill.avg_price, model_price),
            book_mid=mid,
            slippage=None if mid is None else slippage_vs_mid(side, fill.avg_price, mid),
            arrival_price=bar.close,
            delta_ms=(fill.timestamp - signal.signal_time) // timedelta(milliseconds=1),
        )
