"""Unit tests for OrderRouter.

Tests cover:
- Kill circuit breaker fires before any other check (Spec 9 §10).
- Orphan-position invariant (SA-4 P0) — blocked when open_position_count >= 3.
- Risk overlay HALT blocks dispatch.
- Risk overlay WARN → logs + proceeds.
- Risk overlay OK → proceeds to dispatch.
- Successful fill returned verbatim.
- Fill-model Rejection wrapped as RiskRejected.
- Sizing failure (degenerate inputs) wrapped as RiskRejected.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from hermes.application.order_router.risk_engine import RiskEngine
from hermes.application.order_router.router import (
    KillCircuitBreaker,
    OrderRouter,
    RiskRejected,
    RoutingInput,
)
from hermes.application.order_router.sizing import SizingCalculator, SizingInput
from hermes.application.strategy_protocol.kill_trigger_evaluator import (
    ExperimentState,
    KillTriggerEvaluator,
)
from hermes.application.strategy_protocol.preregistration import PreRegistration
from hermes.domain.fees import FeeSchedule, FeeTier
from hermes.domain.market_data.orderbook import Orderbook, OrderbookLevel
from hermes.domain.risk.state import MarketSnapshot, PortfolioState
from hermes.domain.risk.types import RiskAction, RiskDecision
from hermes.domain.trading.fill import Fill, Rejection
from hermes.domain.trading.order import Order, OrderSide
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.quantity import Quantity
from hermes.domain.value_objects.strategy_id import StrategyId
from hermes.ports.fill_model import FillModelPort
from hermes.ports.risk_overlay import RiskOverlayPort

# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------

_BTC_USDT = Pair(base=Currency.BTC, quote=Currency.USDT)
_STRATEGY = StrategyId("swing")
_NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


@dataclass
class _FixedRule:
    """Stub RiskOverlayPort that returns a fixed decision."""

    action: RiskAction
    reason: str

    def evaluate(
        self,
        portfolio_state: PortfolioState,
        market_snapshot: MarketSnapshot,
    ) -> RiskDecision:
        """Return the fixed decision."""
        return RiskDecision(action=self.action, reason=self.reason)


class _FixedFillModel:
    """Stub FillModelPort that returns a fixed Fill or Rejection."""

    def __init__(self, result: Fill | Rejection) -> None:
        self._result = result

    def fill_order(
        self,
        order: Order,
        orderbook: Orderbook,
        clock: datetime,
        fees: FeeSchedule,
    ) -> Fill | Rejection:
        """Return the configured result regardless of inputs."""
        return self._result


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def pre_reg() -> PreRegistration:
    """Real pre-registration from the signed document."""
    here = Path(__file__).resolve().parent
    for candidate in [here, *here.parents]:
        if (candidate / "pyproject.toml").exists():
            return PreRegistration(project_root=candidate)
    pytest.skip("Could not find project root for PreRegistration")


@pytest.fixture(scope="module")
def kill_evaluator(pre_reg: PreRegistration) -> KillTriggerEvaluator:
    """Real KillTriggerEvaluator from the signed pre-registration."""
    return KillTriggerEvaluator(pre_reg=pre_reg)


def _safe_experiment_state() -> ExperimentState:
    """ExperimentState below all §10 kill thresholds."""
    return ExperimentState(
        max_drawdown=0.05,
        days_since_equity_high=10.0,
        sa9_halt=False,
        rolling_30d_sharpe=1.0,
    )


def _triggered_experiment_state() -> ExperimentState:
    """ExperimentState above the §10 drawdown kill threshold (15%)."""
    return ExperimentState(
        max_drawdown=0.20,
        days_since_equity_high=10.0,
        sa9_halt=False,
        rolling_30d_sharpe=1.0,
    )


def _make_orderbook() -> Orderbook:
    """Minimal valid Orderbook for BTC/USDT."""
    return Orderbook(
        pair=_BTC_USDT,
        bids=(OrderbookLevel(price=Decimal("49900"), quantity=Decimal("1")),),
        asks=(OrderbookLevel(price=Decimal("50100"), quantity=Decimal("1")),),
        snapshot_time=_NOW,
    )


def _make_fee_schedule() -> FeeSchedule:
    """Minimal FeeSchedule for BTC/USDT."""
    return FeeSchedule(
        pair=_BTC_USDT,
        tiers=(FeeTier(volume_threshold_usd=Decimal("0"), maker_bps=10, taker_bps=10),),
    )


def _make_sizing_input() -> SizingInput:
    """Return a ``SizingInput`` below all three caps."""
    return SizingInput(
        equity=Decimal("100000"),
        entry_px=Decimal("50000"),
        atr=Decimal("500"),
        stop_atr_mult=Decimal("1.5"),
        risk_pct=Decimal("0.01"),
        f_star_estimate=Decimal("0.20"),
    )


def _make_portfolio_state() -> PortfolioState:
    """Empty PortfolioState with no open positions."""
    return PortfolioState(
        peak_value=Decimal("100000"),
        current_value=Decimal("100000"),
        realized_slippage_cumulative=Decimal("0"),
    )


def _make_market_snapshot() -> MarketSnapshot:
    """Return a ``MarketSnapshot`` for BTC/USDT."""
    return MarketSnapshot(
        pair=_BTC_USDT,
        current_spread=Decimal("0.001"),
        median_spread_1h=Decimal("0.001"),
        orderbook_age_seconds=0.5,
        orderbook_timestamp=_NOW,
        realized_slippage_last=Decimal("0.001"),
        stable_price=None,
        btc_log_return_1d=None,
        candidate_pair=_BTC_USDT,
    )


def _make_fill(order: Order) -> Fill:
    """Construct a valid Fill for the given order."""
    return Fill(
        order=order,
        executed_qty=order.qty.amount,
        avg_price=Decimal("50000"),
        fee=Money(amount=Decimal("5"), currency=Currency.USDT),
        timestamp=_NOW,
    )


def _make_routing_input(
    fill_model: FillModelPort,
    risk_rules: list[RiskOverlayPort] | None = None,
    experiment_state: ExperimentState | None = None,
    open_position_count: int = 0,
    kill_evaluator: KillTriggerEvaluator | None = None,
) -> tuple[OrderRouter, RoutingInput]:
    """Build an ``OrderRouter`` and ``RoutingInput`` pair for testing.

    Returns:
        Tuple of (router, routing_input).

    """
    here = Path(__file__).resolve().parent
    project_root = next(
        (c for c in [here, *here.parents] if (c / "pyproject.toml").exists()),
        None,
    )
    if project_root is None:
        pytest.skip("Could not find project root")

    pre_reg = PreRegistration(project_root=project_root)
    evaluator = kill_evaluator or KillTriggerEvaluator(pre_reg=pre_reg)
    rules = risk_rules or []
    engine = RiskEngine(rules=rules)
    calculator = SizingCalculator()

    router = OrderRouter(
        fill_model=fill_model,
        risk_engine=engine,
        sizing_calculator=calculator,
        kill_evaluator=evaluator,
    )

    inp = RoutingInput(
        strategy_id=_STRATEGY,
        side=OrderSide.BUY,
        pair=_BTC_USDT,
        entry_px=Decimal("50000"),
        sizing_input=_make_sizing_input(),
        portfolio_state=_make_portfolio_state(),
        market_snapshot=_make_market_snapshot(),
        experiment_state=experiment_state or _safe_experiment_state(),
        orderbook=_make_orderbook(),
        clock=_NOW,
        fees=_make_fee_schedule(),
        open_position_count=open_position_count,
    )

    return router, inp


# ---------------------------------------------------------------------------
# Kill circuit breaker tests
# ---------------------------------------------------------------------------


class TestOrderRouterKillCircuitBreaker:
    """KillCircuitBreaker fires before any other check when §10 criterion triggered."""

    def test_triggered_state_returns_kill_circuit_breaker(self) -> None:
        """When drawdown >= 15%, router must return KillCircuitBreaker immediately."""
        dummy_order = Order(
            side=OrderSide.BUY,
            pair=_BTC_USDT,
            qty=Quantity(amount=Decimal("0.001"), asset=Currency.BTC),
        )
        fill = _make_fill(dummy_order)
        fill_model = _FixedFillModel(result=fill)

        router, inp = _make_routing_input(
            fill_model=fill_model,
            experiment_state=_triggered_experiment_state(),
        )
        result = router.route(inp)
        assert isinstance(result, KillCircuitBreaker), (
            f"Expected KillCircuitBreaker for drawdown=20% >= 15% kill threshold. "
            f"Got {type(result).__name__!r}: {result!r}."
        )

    def test_kill_circuit_breaker_has_triggered_decision(self) -> None:
        """KillCircuitBreaker.decision must have triggered=True."""
        dummy_order = Order(
            side=OrderSide.BUY,
            pair=_BTC_USDT,
            qty=Quantity(amount=Decimal("0.001"), asset=Currency.BTC),
        )
        fill = _make_fill(dummy_order)
        fill_model = _FixedFillModel(result=fill)

        router, inp = _make_routing_input(
            fill_model=fill_model,
            experiment_state=_triggered_experiment_state(),
        )
        result = router.route(inp)
        assert isinstance(result, KillCircuitBreaker), (
            f"Expected KillCircuitBreaker, got {type(result).__name__!r}."
        )
        assert result.decision.triggered is True, (
            f"KillCircuitBreaker.decision.triggered must be True. Got: {result.decision!r}."
        )

    def test_safe_state_does_not_trigger_kill(self) -> None:
        """Safe experiment state (drawdown=5%) must not return KillCircuitBreaker."""
        dummy_order = Order(
            side=OrderSide.BUY,
            pair=_BTC_USDT,
            qty=Quantity(amount=Decimal("0.001"), asset=Currency.BTC),
        )
        fill = _make_fill(dummy_order)
        fill_model = _FixedFillModel(result=fill)

        router, inp = _make_routing_input(
            fill_model=fill_model,
            experiment_state=_safe_experiment_state(),
        )
        result = router.route(inp)
        assert not isinstance(result, KillCircuitBreaker), (
            f"Safe state must not trigger KillCircuitBreaker. "
            f"Got {type(result).__name__!r}: {result!r}."
        )


# ---------------------------------------------------------------------------
# Orphan-position guard (SA-4 P0)
# ---------------------------------------------------------------------------


class TestOrderRouterOrphanPositionGuard:
    """SA-4 P0: router refuses new entries when open_position_count >= 3."""

    def test_at_max_concurrent_returns_risk_rejected(self) -> None:
        """open_position_count == 3 must return RiskRejected (SA-4 orphan-position cap)."""
        dummy_order = Order(
            side=OrderSide.BUY,
            pair=_BTC_USDT,
            qty=Quantity(amount=Decimal("0.001"), asset=Currency.BTC),
        )
        fill = _make_fill(dummy_order)
        fill_model = _FixedFillModel(result=fill)

        router, inp = _make_routing_input(
            fill_model=fill_model,
            open_position_count=3,
        )
        result = router.route(inp)
        assert isinstance(result, RiskRejected), (
            f"Expected RiskRejected when open_position_count=3 (SA-4 P0 cap). "
            f"Got {type(result).__name__!r}: {result!r}."
        )

    def test_at_max_concurrent_rule_is_orphan_position(self) -> None:
        """RiskRejected.rule must mention orphan-position for traceability."""
        dummy_order = Order(
            side=OrderSide.BUY,
            pair=_BTC_USDT,
            qty=Quantity(amount=Decimal("0.001"), asset=Currency.BTC),
        )
        fill = _make_fill(dummy_order)
        fill_model = _FixedFillModel(result=fill)

        router, inp = _make_routing_input(
            fill_model=fill_model,
            open_position_count=3,
        )
        result = router.route(inp)
        assert isinstance(result, RiskRejected), (
            f"Expected RiskRejected for orphan-position cap, got {type(result).__name__!r}."
        )
        assert "orphan" in result.rule.lower() or "position" in result.rule.lower(), (
            f"RiskRejected.rule must mention 'orphan' or 'position' for SA-4 traceability. "
            f"Got rule={result.rule!r}."
        )

    def test_above_max_concurrent_also_rejected(self) -> None:
        """open_position_count == 4 also returns RiskRejected."""
        dummy_order = Order(
            side=OrderSide.BUY,
            pair=_BTC_USDT,
            qty=Quantity(amount=Decimal("0.001"), asset=Currency.BTC),
        )
        fill = _make_fill(dummy_order)
        fill_model = _FixedFillModel(result=fill)

        router, inp = _make_routing_input(
            fill_model=fill_model,
            open_position_count=4,
        )
        result = router.route(inp)
        assert isinstance(result, RiskRejected), (
            f"Expected RiskRejected for open_position_count=4, "
            f"got {type(result).__name__!r}: {result!r}."
        )

    def test_below_max_concurrent_allows_routing(self) -> None:
        """open_position_count == 2 must not trigger the orphan-position guard."""
        dummy_order = Order(
            side=OrderSide.BUY,
            pair=_BTC_USDT,
            qty=Quantity(amount=Decimal("0.001"), asset=Currency.BTC),
        )
        fill = _make_fill(dummy_order)
        fill_model = _FixedFillModel(result=fill)

        router, inp = _make_routing_input(
            fill_model=fill_model,
            open_position_count=2,
        )
        result = router.route(inp)
        assert (
            not isinstance(result, RiskRejected)
            or "orphan" not in getattr(result, "rule", "").lower()
        ), (
            f"open_position_count=2 must not trigger orphan-position guard. "
            f"Got {type(result).__name__!r}: {result!r}."
        )


# ---------------------------------------------------------------------------
# Risk overlay integration
# ---------------------------------------------------------------------------


class TestOrderRouterRiskOverlay:
    """Risk overlay HALT / WARN / OK outcomes propagate correctly."""

    def test_halt_rule_returns_risk_rejected(self) -> None:
        """A HALT rule must cause the router to return RiskRejected without dispatching."""
        dummy_order = Order(
            side=OrderSide.BUY,
            pair=_BTC_USDT,
            qty=Quantity(amount=Decimal("0.001"), asset=Currency.BTC),
        )
        fill = _make_fill(dummy_order)
        fill_model = _FixedFillModel(result=fill)

        halt_rule = _FixedRule(action=RiskAction.HALT, reason="test HALT rule fired")
        router, inp = _make_routing_input(
            fill_model=fill_model,
            risk_rules=[halt_rule],
        )
        result = router.route(inp)
        assert isinstance(result, RiskRejected), (
            f"Expected RiskRejected when a HALT rule fires. "
            f"Got {type(result).__name__!r}: {result!r}."
        )

    def test_halt_rule_result_mentions_halt(self) -> None:
        """RiskRejected.rule must be 'HALT' when overlay fires HALT."""
        dummy_order = Order(
            side=OrderSide.BUY,
            pair=_BTC_USDT,
            qty=Quantity(amount=Decimal("0.001"), asset=Currency.BTC),
        )
        fill = _make_fill(dummy_order)
        fill_model = _FixedFillModel(result=fill)

        halt_rule = _FixedRule(action=RiskAction.HALT, reason="halt stub")
        router, inp = _make_routing_input(
            fill_model=fill_model,
            risk_rules=[halt_rule],
        )
        result = router.route(inp)
        assert isinstance(result, RiskRejected), f"Expected RiskRejected, got {result!r}."
        assert result.rule == "HALT", f"Expected rule='HALT', got {result.rule!r}."

    def test_ok_rule_allows_dispatch(self) -> None:
        """An OK rule allows the router to proceed to fill dispatch."""
        dummy_order = Order(
            side=OrderSide.BUY,
            pair=_BTC_USDT,
            qty=Quantity(amount=Decimal("0.001"), asset=Currency.BTC),
        )
        fill = _make_fill(dummy_order)
        fill_model = _FixedFillModel(result=fill)

        ok_rule = _FixedRule(action=RiskAction.OK, reason="ok stub")
        router, inp = _make_routing_input(
            fill_model=fill_model,
            risk_rules=[ok_rule],
        )
        result = router.route(inp)
        assert isinstance(result, Fill), (
            f"Expected Fill after OK risk overlay. Got {type(result).__name__!r}: {result!r}."
        )

    def test_warn_rule_allows_dispatch(self) -> None:
        """A WARN rule allows the router to proceed (log + continue)."""
        dummy_order = Order(
            side=OrderSide.BUY,
            pair=_BTC_USDT,
            qty=Quantity(amount=Decimal("0.001"), asset=Currency.BTC),
        )
        fill = _make_fill(dummy_order)
        fill_model = _FixedFillModel(result=fill)

        warn_rule = _FixedRule(action=RiskAction.WARN, reason="warn stub — proceed")
        router, inp = _make_routing_input(
            fill_model=fill_model,
            risk_rules=[warn_rule],
        )
        result = router.route(inp)
        assert isinstance(result, Fill), (
            f"Expected Fill after WARN risk overlay (WARN log + proceed). "
            f"Got {type(result).__name__!r}: {result!r}."
        )


# ---------------------------------------------------------------------------
# Fill dispatch
# ---------------------------------------------------------------------------


class TestOrderRouterFillDispatch:
    """Fill dispatch — successful fill and Rejection wrapping."""

    def test_successful_fill_returned_verbatim(self) -> None:
        """Router returns the Fill from FillModelPort verbatim."""
        dummy_order = Order(
            side=OrderSide.BUY,
            pair=_BTC_USDT,
            qty=Quantity(amount=Decimal("0.001"), asset=Currency.BTC),
        )
        expected_fill = _make_fill(dummy_order)
        fill_model = _FixedFillModel(result=expected_fill)

        router, inp = _make_routing_input(fill_model=fill_model)
        result = router.route(inp)
        assert isinstance(result, Fill), (
            f"Expected Fill from successful dispatch, got {type(result).__name__!r}."
        )

    def test_fill_rejection_wrapped_as_risk_rejected(self) -> None:
        """FillModelPort Rejection is wrapped as RiskRejected with rule='fill_model'."""
        dummy_order = Order(
            side=OrderSide.BUY,
            pair=_BTC_USDT,
            qty=Quantity(amount=Decimal("0.001"), asset=Currency.BTC),
        )
        rejection = Rejection(order=dummy_order, reason="insufficient liquidity in book")
        fill_model = _FixedFillModel(result=rejection)

        router, inp = _make_routing_input(fill_model=fill_model)
        result = router.route(inp)
        assert isinstance(result, RiskRejected), (
            f"Expected RiskRejected wrapping fill Rejection, "
            f"got {type(result).__name__!r}: {result!r}."
        )
        assert result.rule == "fill_model", (
            f"Expected rule='fill_model' for fill Rejection, got {result.rule!r}."
        )
