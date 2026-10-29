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
  closes the loop has seen, the 30-day correlations of every pair's 1d
  closes (RO-01, the same :class:`~hermes.domain.portfolio.correlation.DailyCloses`
  the backtest replay uses), the recorded fill slippage of the last
  :data:`SLIPPAGE_WINDOW` and the portfolio. The live-phase §10 recovery,
  single SA-9 and 30-trade Sharpe kills get neutral inputs, as in the
  backtest (#514, the shadow set): the drawdown kill and the shadow-phase
  kills are the ones that act. With real money serve passes ``live_inputs``
  (#1158): RO-02 then reads the 1-hour median spread, RO-05 and RO-06 the
  stablecoin price, and the recovery kill the days since the equity high
  (:func:`live_risk_engine` adds RO-02).
- **Records** (D-4): the fill goes to the ledger through the trade recorder
  (``TradeExecuted`` v2 with ``strategy_id``), with one
  :class:`~hermes.domain.events.fill_quality_recorded.FillQualityRecorded`
  carrying the drift against the §6.2 model price of the same order, in the
  same transaction.
- **Idempotency** (D-5): a signal whose pair and side already has a trade at or
  after the signal time is not executed again (a bar re-evaluated after a crash).

The executor performs no logging and reads no wall clock of its own; the
caller logs from the returned :class:`ShadowExecution`.

Reference: openspec/archive/353-fill-kernel-in-serve/change.md D-1..D-5.
"""

import math
from collections import deque
from collections.abc import Callable, Sequence
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
from hermes.domain.events.base import DomainEvent
from hermes.domain.events.fill_quality_recorded import BookSource, FillQualityRecorded
from hermes.domain.events.trade_executed import TradeExecuted, TradeSide
from hermes.domain.fees.fee_schedule import FeeSchedule
from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.market_data.orderbook import Orderbook
from hermes.domain.portfolio.correlation import DailyCloses
from hermes.domain.risk.correlation_lock import CorrelationLock
from hermes.domain.risk.depeg_halt import DepegHalt
from hermes.domain.risk.depeg_warn import DepegWarn
from hermes.domain.risk.macro_atr_halt import MacroATRHalt
from hermes.domain.risk.max_slippage import MaxSlippage
from hermes.domain.risk.portfolio_drawdown import PortfolioDrawdown
from hermes.domain.risk.spread_spike import SpreadSpike
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
from hermes.ports.live_risk_inputs import LiveRiskInputsPort
from hermes.ports.orderbook_snapshot import OrderbookSnapshotPort
from hermes.ports.risk_overlay import RiskOverlayPort

__all__ = [
    "LIVE_BOOK_WINDOW",
    "MACRO_PAIR",
    "SLIPPAGE_WINDOW",
    "AlreadyFilled",
    "FillRecorder",
    "ShadowExecution",
    "ShadowExecutor",
    "ShadowOutcome",
    "live_risk_engine",
    "shadow_risk_engine",
]

LIVE_BOOK_WINDOW: Final = timedelta(minutes=5)
"""Oldest bar close a live book may stand in for (D-2)."""

MACRO_PAIR: Final = Pair(base=Currency.BTC, quote=Currency.USDT)
"""Pair whose 1d log-return feeds risk rule RO-07."""

SLIPPAGE_WINDOW: Final = timedelta(hours=24)
"""How long a fill's slippage feeds RO-03 (#857).

Without it a slipping exit with nothing held left entries halted for good:
only a fill could replace the last slippage, and no entry could fill.
"""

_ZERO = Decimal("0")
_MACRO_WINDOW: Final = 2


def shadow_risk_engine() -> RiskEngine:
    """Return the risk overlay serve applies to entries (D-3).

    RO-01 (correlation lock) runs as the backtest that gated the strategy runs
    it (#822), fed the same 30-day correlations (#514). RO-02 (spread spike) is
    left out: the backtest's synthetic book never spikes, so a shadow that
    acted on it would trade differently from the backtest it is checked
    against (#514, the shadow set); its 1-hour median is recorded for review
    (#835).

    Returns:
        An engine with RO-01, RO-03, RO-04, RO-05, RO-06, RO-07 and RO-08.
    """
    return RiskEngine(rules=_shadow_rules())


def live_risk_engine() -> RiskEngine:
    """Return the risk overlay serve applies to entries with real money (#1158).

    The shadow set plus RO-02 (spread spike), which reads the 1-hour median
    spread the executor's ``live_inputs`` supply.

    Returns:
        An engine with RO-01 through RO-08.
    """
    return RiskEngine(rules=[*_shadow_rules(), SpreadSpike()])


def _shadow_rules() -> list[RiskOverlayPort]:
    """Return a fresh instance of each shadow-set rule (D-3)."""
    return [
        CorrelationLock(),
        MaxSlippage(),
        StaleBook(),
        DepegWarn(),
        DepegHalt(),
        MacroATRHalt(),
        PortfolioDrawdown(),
    ]


class FillRecorder(Protocol):
    """Persists one fill as a ledger trade (``TradeRecorder`` in production)."""

    def record(self, fill: Fill, *, correlation_id: str, also: Sequence[DomainEvent] = ()) -> int:
        """Store ``fill`` and the ``also`` events in one transaction; return its trade row id."""
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
        live_inputs: The live-phase RO-02, depeg and recovery inputs; ``None``
            in shadow, where they stay neutral as in the backtest.
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
        live_inputs: LiveRiskInputsPort | None = None,
    ) -> None:
        """Bind the collaborators and load the recorded fill slippages.

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
        self._daily_closes = DailyCloses()
        self._live_window = live_window
        self._not_before = not_before
        self._live_inputs = live_inputs
        self._macro_closes: deque[tuple[datetime, Decimal]] = deque(maxlen=_MACRO_WINDOW)
        self._killed_by = ""
        self._slippages: deque[tuple[datetime, Decimal]] = deque(
            (row.occurred_at, row.slippage)
            for row in ledger.fill_quality_events(strategy_id)
            if row.slippage is not None
        )

    @property
    def model(self) -> ExecutionModel:
        """The §6.2 execution book this executor falls back to."""
        return self._model

    @property
    def killed_by(self) -> str:
        """Rule id of the recorded shadow kill that stops entries, or empty."""
        return self._killed_by

    def hold_kill(self, rule: str) -> None:
        """Stop every later entry under the recorded shadow kill ``rule`` (#796).

        Exits keep filling: they skip the kill check, as for any kill.

        Args:
            rule: The ``KillDecision.rule`` of the kill serve recorded.
        """
        self._killed_by = rule

    def observe(self, bar: Bar) -> None:
        """Remember the 1d closes: every pair's for RO-01, BTC/USDT's for RO-07.

        Args:
            bar: Any bar the loop has seen, warm-up included, in delivery order.
        """
        if bar.interval is not KlineInterval.D1:
            return
        self._daily_closes.add(bar)
        if bar.pair != MACRO_PAIR:
            return
        if self._macro_closes and bar.close_time <= self._macro_closes[-1][0]:
            return
        self._macro_closes.append((bar.close_time, bar.close))

    async def execute(
        self,
        signal: StrategySignal,
        bar: Bar,
        *,
        correlation_id: str,
        price_bar: Bar | None = None,
    ) -> ShadowExecution:
        """Execute one signal of ``bar`` and record its fill.

        A ``price_bar`` marks ``bar`` as missed: it closed while serve was down
        (issue #1078). The order then fills at the price of now, never at the
        old bar's: on a live book taken now, or on the §6.2 book of
        ``price_bar`` (the pair's newest closed bar) stamped now when no live
        snapshot can be had. Serve passes only exits here; an entry on a
        missed bar is never filled.

        Args:
            signal: The strategy signal.
            bar: The bar that produced it.
            correlation_id: Stamped on the trade and its quality row.
            price_bar: The pair's newest closed bar when ``bar`` was missed;
                ``None`` for a bar handled in its live window.

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
        priced = bar if price_bar is None else price_bar
        book, source, error = await self._book(signal, priced, now, missed=price_bar is not None)
        if book is not None and source is BookSource.LIVE:
            at = book.snapshot_time
        elif price_bar is not None:
            at = now
        else:
            at = signal.signal_time
        if self._not_before is not None and at < self._not_before:
            at = self._not_before
        fees = self._fees_at(at, pair)
        while self._slippages and now - self._slippages[0][0] > SLIPPAGE_WINDOW:
            self._slippages.popleft()
        market = MarketContext(
            orderbook=book,
            fees=fees,
            at=at,
            market_snapshot=self._market_snapshot(pair, book, priced, now),
            experiment_state=self._experiment_state(now),
            correlations=(
                self._daily_closes.correlations() if signal.kind is SignalKind.ENTRY else {}
            ),
            realized_slippage_cumulative=sum((s for _, s in self._slippages), _ZERO),
        )
        outcome = self._kernel.execute(signal, pair, market, correlation_id=correlation_id)
        quality = None
        if isinstance(outcome, Executed):
            if book is None:  # pragma: no cover — the kernel declines a signal without a book
                raise RuntimeError("the kernel filled an order without a book")
            quality = self._quality(
                outcome.fill,
                signal=signal,
                bar=priced,
                book=book,
                source=source,
                fees=fees,
                correlation_id=correlation_id,
            )
            # One transaction: a crash between two writes would keep the trade
            # and lose the quality row RO-03 reloads on boot (#857).
            self._recorder.record(outcome.fill, correlation_id=correlation_id, also=(quality,))
            if quality.slippage is not None:
                self._slippages.append((quality.occurred_at, quality.slippage))
        return ShadowExecution(
            outcome=outcome, book_source=source, quality=quality, snapshot_error=error
        )

    def prior_fill(self, signal: StrategySignal, pair: Pair) -> TradeExecuted | None:
        """Return the trade that already filled ``signal``, or ``None``.

        Serve asks before passing over an entry on a missed bar (#1078): a bar
        re-evaluated after a crash may have filled before the crash, and the
        strategy must then stay in the position it holds.

        Args:
            signal: The strategy's signal.
            pair: The pair it is for.

        Returns:
            A recorded trade of the signal's pair and side at or after its time.
        """
        side = TradeSide.BUY if signal.kind is SignalKind.ENTRY else TradeSide.SELL
        return self._traded_since(pair, side, signal.signal_time)

    def _traded_since(self, pair: Pair, side: TradeSide, since: datetime) -> TradeExecuted | None:
        """Return a recorded trade of ``pair`` and ``side`` at or after ``since``."""
        for trade in self._ledger.trade_events(self._strategy_id):
            if trade.pair == pair and trade.side is side and trade.occurred_at >= since:
                return trade
        return None

    async def _book(
        self, signal: StrategySignal, bar: Bar, now: datetime, *, missed: bool
    ) -> tuple[Orderbook | None, BookSource, str | None]:
        """Return the book to fill against, where it came from and any snapshot error.

        A missed bar's order fills now, so a live book is always worth trying.
        """
        error = None
        if missed or now - bar.close_time <= self._live_window:
            try:
                return await self._books.snapshot(bar.pair), BookSource.LIVE, None
            except InfraError as exc:
                error = str(exc)
        return self._model.book(signal, bar), BookSource.SYNTHETIC, error

    def _market_snapshot(
        self, pair: Pair, book: Orderbook | None, bar: Bar, now: datetime
    ) -> MarketSnapshot:
        """Build the risk overlay inputs (D-3); live ones only with ``live_inputs`` (#1158)."""
        spread = book_spread(book) if book is not None else _ZERO
        book_time = book.snapshot_time if book is not None else bar.close_time
        median = spread
        stable_price = None
        if self._live_inputs is not None:
            # No recent sample reads as a zero median, which RO-02 halts on.
            median = self._live_inputs.median_spread_1h(pair, now) or _ZERO
            stable_price = self._live_inputs.stable_price(now)
        return MarketSnapshot(
            pair=pair,
            current_spread=spread,
            median_spread_1h=median,
            orderbook_age_seconds=max(0.0, (now - book_time).total_seconds()),
            orderbook_timestamp=book_time,
            realized_slippage_last=self._slippages[-1][1] if self._slippages else _ZERO,
            stable_price=stable_price,
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

    def _experiment_state(self, now: datetime) -> ExperimentState:
        """Build the kill inputs (D-3): drawdown from the tracker, any held shadow kill (#796).

        The recovery days are real only with ``live_inputs`` (#1158).
        """
        snapshot = self._kernel.tracker.snapshot
        peak = snapshot.peak_equity
        drawdown = _ZERO if peak <= 0 else max(_ZERO, (peak - snapshot.equity) / peak)
        return ExperimentState(
            max_drawdown=float(drawdown),
            days_since_equity_high=(
                0.0 if self._live_inputs is None else self._live_inputs.days_since_equity_high(now)
            ),
            sa9_halt=False,
            killed_by=self._killed_by,
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
        latency_ms = (fill.timestamp - signal.signal_time) // timedelta(milliseconds=1)
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
            # Record-only (#30): a clock that puts the fill before the signal
            # must never stop the fill from being recorded.
            delta_ms=latency_ms if latency_ms >= 0 else None,
        )
