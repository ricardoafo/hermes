"""FillKernel — the single path from a strategy signal to a recorded fill.

ADR-011 D-01 allows one fill implementation for every mode. The kernel is that
path above the fill model: the backtest replay calls it with a synthetic book,
and ``hermes serve`` (#353) calls it with a live book. Nothing in it knows
which mode it runs in; the caller supplies the book, fees, time and risk
inputs per call.

- ENTRY signals go through :meth:`OrderRouter.route` unchanged (kill breaker,
  orphan-position guard, risk overlay, sizing, fill model).
- EXIT signals sell the full held quantity through the same fill model. They
  skip the router: its sizing would size an exit from equity, and its
  orphan-position guard would refuse to close a position when the cap is
  reached.
- Every fill is folded into the :class:`PortfolioTracker` and returned with
  its ``TradeExecuted`` v2 event, which carries ``strategy_id`` so the
  startup rebuild (#392) can replay it once the caller persists it.

Reference: openspec/changes/backtest-driver-mvp1-phase2/change.md D-2, D-3, R-4.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import Decimal

from returns.result import Failure

from hermes.application.order_router.risk_engine import RiskEngine
from hermes.application.order_router.router import (
    KillCircuitBreaker,
    OrderRouter,
    RoutingInput,
)
from hermes.application.order_router.sizing import SizingCalculator, SizingInput, SizingPolicy
from hermes.application.portfolio.tracker import PortfolioTracker
from hermes.application.strategy_protocol.kill_trigger_evaluator import (
    ExperimentState,
    KillTriggerEvaluator,
)
from hermes.domain.events.trade_executed import TradeExecuted, TradeSide
from hermes.domain.fees.fee_schedule import FeeSchedule
from hermes.domain.market_data.orderbook import Orderbook
from hermes.domain.risk.state import MarketSnapshot
from hermes.domain.strategy.signal import SignalKind, StrategySignal
from hermes.domain.trading.fill import Fill, Rejection
from hermes.domain.trading.order import Order, OrderSide
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.price import Price
from hermes.domain.value_objects.quantity import Quantity
from hermes.domain.value_objects.strategy_id import StrategyId
from hermes.ports.fill_model import FillModelPort

__all__ = [
    "Declined",
    "Executed",
    "ExecutionOutcome",
    "FillKernel",
    "Halted",
    "MarketContext",
]


@dataclass(frozen=True, slots=True)
class MarketContext:
    """Market inputs for one :meth:`FillKernel.execute` call.

    Attributes:
        orderbook: Depth to fill against, or ``None`` when no book exists
            (a zero-volume bar, a failed live snapshot).
        fees: Fee schedule for the pair at ``at``.
        at: UTC-aware time stamped on the fill.
        market_snapshot: Microstructure inputs for the risk overlay.
        experiment_state: Inputs for the kill-trigger evaluation.
        correlations: 30-day return correlation of pair pairs, for the RO-01
            correlation lock (pre-registration §5.5). The portfolio has no
            market data, so the caller supplies them; empty means none known.
        realized_slippage_cumulative: Sum of the recorded fill slippages
            the RO-03 cumulative limit counts. The portfolio keeps no fill
            quality, so the caller supplies it; zero means none known.
    """

    orderbook: Orderbook | None
    fees: FeeSchedule
    at: datetime
    market_snapshot: MarketSnapshot
    experiment_state: ExperimentState
    correlations: Mapping[tuple[Pair, Pair], float] = field(default_factory=dict)
    realized_slippage_cumulative: Decimal = field(default_factory=Decimal)


@dataclass(frozen=True, slots=True)
class Executed:
    """The order filled and the portfolio took it.

    Attributes:
        fill: The fill from the fill model.
        trade: The event to persist; replaying it rebuilds the same portfolio.
    """

    fill: Fill
    trade: TradeExecuted


@dataclass(frozen=True, slots=True)
class Declined:
    """No fill: a guard, the fill model or the portfolio refused the signal.

    Attributes:
        reason: Human-readable explanation, for operators only.
        rule: Short identifier of what refused (``no-book``, ``no-position``,
            ``fill_model``, ``portfolio`` or a router rule).
        rejection: The fill model's rejection when ``rule == "fill_model"``;
            these are the M-04 rejections. ``None`` otherwise.
    """

    reason: str
    rule: str
    rejection: Rejection | None = None


@dataclass(frozen=True, slots=True)
class Halted:
    """A pre-registration §10 kill criterion fired; no new entries.

    Attributes:
        breaker: The router's circuit-breaker outcome.
    """

    breaker: KillCircuitBreaker


type ExecutionOutcome = Executed | Declined | Halted


@dataclass(slots=True)
class _RecordingFillModel:
    """Passes orders to the wrapped fill model and keeps its last rejection.

    The router wraps a fill-model rejection into ``RiskRejected`` without the
    order; the kernel needs the original ``Rejection`` for M-04.
    """

    inner: FillModelPort
    last_rejection: Rejection | None = field(default=None)

    def fill_order(
        self, order: Order, orderbook: Orderbook, clock: datetime, fees: FeeSchedule
    ) -> Fill | Rejection:
        """Fill through the wrapped model, remembering a rejection."""
        result = self.inner.fill_order(order, orderbook, clock, fees)
        self.last_rejection = result if isinstance(result, Rejection) else None
        return result


class FillKernel:
    """Turns strategy signals into fills, portfolio updates and trade events.

    Args:
        fill_model: The fill implementation (``OrderbookWalkFillModel`` in
            every MVP1 mode).
        risk_engine: Risk overlay rules the router evaluates on entries.
        kill_evaluator: Pre-registration §10 kill evaluator.
        tracker: The strategy's portfolio; the kernel folds fills into it.
        strategy_id: Bucket stamped on every order and trade.
        sizing: Pre-registered sizing limits built from the strategy config
            (``StrategyConfig.sizing_policy()``).
    """

    def __init__(
        self,
        *,
        fill_model: FillModelPort,
        risk_engine: RiskEngine,
        kill_evaluator: KillTriggerEvaluator,
        tracker: PortfolioTracker,
        strategy_id: StrategyId,
        sizing: SizingPolicy,
    ) -> None:
        """Compose the production router around a recording fill model."""
        self._fill_model = _RecordingFillModel(fill_model)
        self._router = OrderRouter(
            fill_model=self._fill_model,
            risk_engine=risk_engine,
            sizing_calculator=SizingCalculator(sizing),
            kill_evaluator=kill_evaluator,
        )
        self._tracker = tracker
        self._strategy_id = strategy_id
        self._exposure_cap = sizing.exposure_cap

    @property
    def tracker(self) -> PortfolioTracker:
        """The portfolio the kernel folds fills into."""
        return self._tracker

    def execute(
        self,
        signal: StrategySignal,
        pair: Pair,
        market: MarketContext,
        *,
        correlation_id: str,
    ) -> ExecutionOutcome:
        """Execute one signal for ``pair``.

        Args:
            signal: The strategy signal.
            pair: The pair the signal is for.
            market: Book, fees, time and risk inputs for this call.
            correlation_id: Stamped on the ``TradeExecuted`` event.

        Returns:
            :class:`Executed`, :class:`Declined` or :class:`Halted`.
        """
        if market.orderbook is None:
            return Declined(reason=f"no order book for {pair} at {market.at}", rule="no-book")
        if signal.kind is SignalKind.ENTRY:
            return self._enter(signal, pair, market, market.orderbook, correlation_id)
        return self._exit(pair, market, market.orderbook, correlation_id)

    def _enter(
        self,
        signal: StrategySignal,
        pair: Pair,
        market: MarketContext,
        book: Orderbook,
        correlation_id: str,
    ) -> ExecutionOutcome:
        """Route an entry through the production router."""
        entry_px = signal.entry_price
        if entry_px is None:  # pragma: no cover — StrategySignal forbids it for ENTRY
            return Declined(reason="entry signal without an entry price", rule="signal")
        self._fill_model.last_rejection = None
        result = self._router.route(
            RoutingInput(
                strategy_id=self._strategy_id,
                side=OrderSide.BUY,
                pair=pair,
                entry_px=entry_px,
                sizing_input=SizingInput(
                    entry_px=entry_px,
                    stop_px=signal.stop_price,
                    open_notional=self._tracker.open_notional(),
                ),
                portfolio_state=replace(
                    self._tracker.state(),
                    correlations=dict(market.correlations),
                    realized_slippage_cumulative=market.realized_slippage_cumulative,
                ),
                market_snapshot=market.market_snapshot,
                experiment_state=market.experiment_state,
                orderbook=book,
                clock=market.at,
                fees=market.fees,
                open_position_count=self._tracker.open_position_count(),
            )
        )
        if isinstance(result, Fill):
            over_cap = self._over_exposure_cap(result)
            if over_cap is not None:
                return over_cap
            return self._record(result, correlation_id)
        if isinstance(result, KillCircuitBreaker):
            return Halted(breaker=result)
        rejection = self._fill_model.last_rejection if result.rule == "fill_model" else None
        return Declined(reason=result.reason, rule=result.rule, rejection=rejection)

    def _over_exposure_cap(self, fill: Fill) -> Declined | None:
        """Refuse a fill that would put exposure at the marks past the §5.5 cap (#864).

        Sizing checks the cap at the signal price, but the fill walks the ask
        side of the book (plus slippage in a backtest), so it can cost more.
        After the fill the new position is marked at its fill price, so the
        cap is checked again at that price, the price actually paid. Sizing
        is unchanged; an entry that only fits at the signal price is refused,
        as one that does not fit there already is.
        """
        exposure = self._tracker.open_notional() + fill.executed_qty * fill.avg_price
        if exposure <= self._exposure_cap:
            return None
        return Declined(
            reason=(
                f"total exposure cap: {exposure} at the fill price exceeds "
                f"{self._exposure_cap} (pre-reg §5.5)"
            ),
            rule="sizing",
        )

    def _exit(
        self, pair: Pair, market: MarketContext, book: Orderbook, correlation_id: str
    ) -> ExecutionOutcome:
        """Sell the whole held position through the same fill model."""
        held = self._tracker.snapshot.positions.get(pair)
        if held is None:
            return Declined(reason=f"exit signal for {pair} without a position", rule="no-position")
        order = Order(
            side=OrderSide.SELL,
            pair=pair,
            qty=Quantity(amount=held.quantity, asset=pair.base),
            strategy_id=self._strategy_id,
        )
        result = self._fill_model.fill_order(order, book, market.at, market.fees)
        if isinstance(result, Rejection):
            return Declined(reason=result.reason, rule="fill_model", rejection=result)
        return self._record(result, correlation_id)

    def _record(self, fill: Fill, correlation_id: str) -> ExecutionOutcome:
        """Fold the fill into the portfolio and build its trade event."""
        applied = self._tracker.apply_fill(fill)
        if isinstance(applied, Failure):
            return Declined(reason=str(applied.failure()), rule="portfolio")
        pair = fill.order.pair
        trade = TradeExecuted(
            occurred_at=fill.timestamp,
            correlation_id=correlation_id,
            pair=pair,
            side=TradeSide(fill.order.side.value),
            qty=Quantity(amount=fill.executed_qty, asset=pair.base),
            price=Price(amount=fill.avg_price, pair=pair),
            fee=fill.fee,
            strategy_id=fill.strategy_id,
        )
        return Executed(fill=fill, trade=trade)
