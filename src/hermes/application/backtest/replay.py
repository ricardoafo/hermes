"""Backtest replay — closed bars through the strategy and the shared fill kernel.

The loop does what the serve loop does per bar (mark the portfolio, ask the
strategy) and hands every in-window signal to the
:class:`~hermes.application.execution.kernel.FillKernel`. The only
backtest-specific part is the order book: Binance Vision has no depth, so each
signal gets a synthetic book whose touch sits at the pre-registered execution
price (pre-registration §5.3, §6.2):

- ENTRY: 5 bps above the signal bar close.
- EXIT on the chandelier stop: 15 bps below the stop trigger price.
- Any other EXIT (time stop, regime break): 5 bps below the signal bar close.

Depth per side is the bar's volume times ``depth_frac``. The loop performs no
I/O; the composition root loads bars, fees and writes the results.

Reference: openspec/changes/backtest-driver-mvp1-phase2/change.md D-1, D-4, D-5.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from hermes.application.execution.kernel import (
    Declined,
    Executed,
    FillKernel,
    Halted,
    MarketContext,
)
from hermes.application.execution.prereg_book import ExecutionModel
from hermes.application.strategy_protocol.kill_trigger_evaluator import ExperimentState
from hermes.domain.events.trade_executed import TradeExecuted
from hermes.domain.fees.fee_schedule import FeeSchedule
from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.market_data.orderbook import Orderbook
from hermes.domain.risk.state import MarketSnapshot
from hermes.domain.strategy.signal import StrategySignal
from hermes.domain.trading.fill import Fill, Rejection
from hermes.domain.value_objects.pair import Pair
from hermes.ports.strategy import StrategyPort

__all__ = ["BacktestRun", "ExecutionModel", "replay"]


@dataclass(slots=True)
class BacktestRun:
    """Everything one replay produced.

    Attributes:
        signals_seen: In-window signals handed to the kernel.
        fills: Fills, in order.
        reference_prices: The §6.2 reference price behind each fill, aligned
            with ``fills`` (the M-02 mid).
        rejections: Fill-model rejections (the M-04 numerator).
        declined: Declined signals counted by rule, plus ``kill`` for halts.
        trades: ``TradeExecuted`` v2 events, aligned with ``fills``.
        daily_equity: ``(close_time, equity)`` at each in-window 1D close.
    """

    signals_seen: int = 0
    fills: list[Fill] = field(default_factory=list)
    reference_prices: list[Decimal] = field(default_factory=list)
    rejections: list[Rejection] = field(default_factory=list)
    declined: Counter[str] = field(default_factory=Counter)
    trades: list[TradeExecuted] = field(default_factory=list)
    daily_equity: list[tuple[datetime, Decimal]] = field(default_factory=list)


def replay(
    bars: Iterable[Bar],
    *,
    strategy: StrategyPort,
    kernel: FillKernel,
    window_start: datetime,
    window_end: datetime,
    fees_at: Callable[[datetime, Pair], FeeSchedule],
    correlation_id: Callable[[], str],
    model: ExecutionModel | None = None,
) -> BacktestRun:
    """Replay ``bars`` (already in delivery order) through the strategy and kernel.

    Bars before ``window_start`` warm the strategy up; their signals are not
    executed. Every bar marks the portfolio before the strategy sees it, as
    the serve loop does.

    Args:
        bars: Closed bars ordered by close time, 1D first on ties (#311).
        strategy: The strategy instance.
        kernel: The fill kernel; its tracker is the run's portfolio.
        window_start: First signal time that is executed.
        window_end: Last 1D close recorded in the equity curve.
        fees_at: Fee schedule for a pair at a time.
        correlation_id: Source of a fresh correlation id per signal.
        model: Execution prices and depth; the pre-registered defaults when
            ``None``.

    Returns:
        The :class:`BacktestRun`.
    """
    execution = model if model is not None else ExecutionModel()
    run = BacktestRun()
    tracker = kernel.tracker
    for bar in bars:
        tracker.mark(bar)
        signal = strategy.on_bar(bar, tracker.state())
        if signal is not None and signal.signal_time >= window_start:
            run.signals_seen += 1
            _execute(
                run,
                kernel,
                execution=execution,
                signal=signal,
                bar=bar,
                fees_at=fees_at,
                correlation_id=correlation_id(),
            )
        if bar.interval is KlineInterval.D1 and window_start <= bar.close_time <= window_end:
            run.daily_equity.append((bar.close_time, tracker.snapshot.equity))
    return run


def _execute(
    run: BacktestRun,
    kernel: FillKernel,
    *,
    execution: ExecutionModel,
    signal: StrategySignal,
    bar: Bar,
    fees_at: Callable[[datetime, Pair], FeeSchedule],
    correlation_id: str,
) -> None:
    """Execute one signal and record its outcome in ``run``."""
    book = execution.book(signal, bar)
    market = MarketContext(
        orderbook=book,
        fees=fees_at(signal.signal_time, bar.pair),
        at=signal.signal_time,
        market_snapshot=_snapshot(bar, book),
        experiment_state=ExperimentState(
            max_drawdown=0.0, days_since_equity_high=0.0, sa9_halt=False
        ),
    )
    outcome = kernel.execute(signal, bar.pair, market, correlation_id=correlation_id)
    if isinstance(outcome, Executed):
        run.fills.append(outcome.fill)
        run.trades.append(outcome.trade)
        run.reference_prices.append(execution.reference(signal, bar)[0])
    elif isinstance(outcome, Declined):
        run.declined[outcome.rule] += 1
        if outcome.rejection is not None:
            run.rejections.append(outcome.rejection)
    elif isinstance(outcome, Halted):
        run.declined["kill"] += 1


def _snapshot(bar: Bar, book: Orderbook | None) -> MarketSnapshot:
    """Return the stubbed microstructure snapshot (D-5): the book's own spread, age zero."""
    spread = Decimal("0")
    if book is not None and book.bids and book.asks:
        mid = (book.bids[0].price + book.asks[0].price) / 2
        spread = (book.asks[0].price - book.bids[0].price) / mid
    return MarketSnapshot(
        pair=bar.pair,
        current_spread=spread,
        median_spread_1h=spread,
        orderbook_age_seconds=0.0,
        orderbook_timestamp=bar.close_time,
        realized_slippage_last=Decimal("0"),
        stable_price=None,
        btc_log_return_1d=None,
    )
