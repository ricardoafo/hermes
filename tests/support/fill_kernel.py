"""Builders shared by the fill-kernel, replay and backtest integration tests."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from hermes.adapters.fills.orderbook_walk import OrderbookWalkFillModel
from hermes.application.execution.kernel import FillKernel, MarketContext
from hermes.application.order_router.risk_engine import RiskEngine
from hermes.application.order_router.sizing import SizingPolicy
from hermes.application.portfolio.tracker import PortfolioTracker
from hermes.application.strategy_protocol.kill_trigger_evaluator import (
    ExperimentState,
    KillTriggerEvaluator,
)
from hermes.application.strategy_protocol.preregistration import PreRegistration
from hermes.domain.fees.fee_schedule import FeeSchedule, FeeTier
from hermes.domain.market_data.orderbook import Orderbook
from hermes.domain.portfolio.snapshot import PortfolioSnapshot
from hermes.domain.risk.state import MarketSnapshot
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.strategy_id import StrategyId
from hermes.ports.fill_model import FillModelPort
from tests.support.paths import REPO_ROOT

BTC_USDT = Pair(base=Currency.BTC, quote=Currency.USDT)
STRATEGY = StrategyId("swing-donchian-4h")
STARTING_CASH = Decimal("5000")
TAKER_BPS = 10


def fees(pair: Pair = BTC_USDT, taker_bps: int = TAKER_BPS) -> FeeSchedule:
    """Return a one-tier schedule with ``taker_bps`` taker fees."""
    return FeeSchedule.from_tiers(
        pair=pair,
        tiers=(
            FeeTier(volume_threshold_usd=Decimal("0"), maker_bps=taker_bps, taker_bps=taker_bps),
        ),
    )


def fees_at(at: datetime, pair: Pair) -> FeeSchedule:
    """Return :func:`fees` for ``pair`` at any time: a schedule that never changes."""
    _ = at
    return fees(pair)


SIZING = SizingPolicy(
    capital=STARTING_CASH,
    risk_pct=Decimal("0.01"),
    max_position_pct=Decimal("0.05"),
    max_total_exposure_pct=Decimal("0.15"),
)
"""Pre-registration §5.4/§5.5 limits on the shared starting cash."""


def seed(cash: Decimal = STARTING_CASH) -> PortfolioSnapshot:
    """Return the USDT seed snapshot of the swing strategy."""
    return PortfolioSnapshot.seed(STRATEGY, Money(cash, Currency.USDT))


def kill_evaluator() -> KillTriggerEvaluator:
    """Return the evaluator built from the signed pre-registration."""
    return KillTriggerEvaluator(pre_reg=PreRegistration(project_root=REPO_ROOT))


def kernel(
    tracker: PortfolioTracker | None = None, fill_model: FillModelPort | None = None
) -> FillKernel:
    """Return a kernel over the real walk fill model, no risk rules, and a fresh tracker."""
    return FillKernel(
        fill_model=fill_model if fill_model is not None else OrderbookWalkFillModel(),
        risk_engine=RiskEngine(rules=[]),
        kill_evaluator=kill_evaluator(),
        tracker=tracker if tracker is not None else PortfolioTracker(seed()),
        strategy_id=STRATEGY,
        sizing=SIZING,
    )


def calm_state(max_drawdown: float = 0.0) -> ExperimentState:
    """Return an experiment state below every kill threshold unless overridden."""
    return ExperimentState(max_drawdown=max_drawdown, days_since_equity_high=0.0, sa9_halt=False)


def market(
    book: Orderbook | None, at: datetime, *, state: ExperimentState | None = None
) -> MarketContext:
    """Return a market context with a neutral snapshot around ``book``."""
    return MarketContext(
        orderbook=book,
        fees=fees(),
        at=at,
        market_snapshot=MarketSnapshot(
            pair=BTC_USDT,
            current_spread=Decimal("0.001"),
            median_spread_1h=Decimal("0.001"),
            orderbook_age_seconds=0.0,
            orderbook_timestamp=at,
            realized_slippage_last=Decimal("0"),
            stable_price=None,
            btc_log_return_1d=None,
        ),
        experiment_state=state if state is not None else calm_state(),
    )
