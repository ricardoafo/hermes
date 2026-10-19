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

from collections.abc import MutableMapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest
import structlog
from hypothesis import given
from hypothesis import strategies as st

from hermes.application.order_router.risk_engine import RiskEngine
from hermes.application.order_router.router import (
    KillCircuitBreaker,
    OrderRouter,
    RiskRejected,
    RoutingInput,
)
from hermes.application.order_router.sizing import SizingCalculator, SizingInput, SizingPolicy
from hermes.application.strategy_protocol.kill_trigger_evaluator import (
    ExperimentState,
    KillDecision,
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
from hermes.infrastructure.project_root import load_pre_registration
from hermes.ports.fill_model import FillModelPort
from hermes.ports.risk_overlay import RiskOverlayPort
from tests.support.paths import REPO_ROOT

_STRATEGY_ID = StrategyId("test-strategy")

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
        self.orders: list[Order] = []

    def fill_order(
        self,
        order: Order,
        orderbook: Orderbook,
        clock: datetime,
        fees: FeeSchedule,
    ) -> Fill | Rejection:
        """Record the order and return the configured result regardless of inputs."""
        self.orders.append(order)
        return self._result


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def pre_reg() -> PreRegistration:
    """Real pre-registration from the signed document."""
    return load_pre_registration(REPO_ROOT)


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


def _make_sizing_policy() -> SizingPolicy:
    """Return the pre-registered limits (§2 capital, §5.4/§5.5 shares)."""
    return SizingPolicy(
        capital=Decimal("5000"),
        risk_pct=Decimal("0.01"),
        max_position_pct=Decimal("0.05"),
        max_total_exposure_pct=Decimal("0.15"),
    )


def _make_sizing_input() -> SizingInput:
    """Return a ``SizingInput`` for a flat portfolio with the stop 3% below entry."""
    return SizingInput(
        entry_px=Decimal("50000"),
        stop_px=Decimal("48500"),
        open_notional=Decimal("0"),
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
    project_root = REPO_ROOT

    pre_reg = load_pre_registration(project_root)
    evaluator = kill_evaluator or KillTriggerEvaluator(pre_reg=pre_reg)
    rules = risk_rules or []
    engine = RiskEngine(rules=rules)
    calculator = SizingCalculator(_make_sizing_policy())

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
            strategy_id=_STRATEGY_ID,
        )
        fill = _make_fill(dummy_order)
        fill_model = _FixedFillModel(result=fill)

        router, inp = _make_routing_input(
            fill_model=fill_model,
            experiment_state=_triggered_experiment_state(),
        )
        result = router.route(inp)
        assert result == KillCircuitBreaker(
            decision=KillDecision(
                triggered=True,
                reason="Max drawdown 20.0% >= kill threshold 15.0% (pre-reg §10).",
                rule="max_drawdown",
            )
        )
        assert fill_model.orders == []

    def test_kill_circuit_breaker_has_triggered_decision(self) -> None:
        """KillCircuitBreaker.decision must have triggered=True."""
        dummy_order = Order(
            side=OrderSide.BUY,
            pair=_BTC_USDT,
            qty=Quantity(amount=Decimal("0.001"), asset=Currency.BTC),
            strategy_id=_STRATEGY_ID,
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
            strategy_id=_STRATEGY_ID,
        )
        fill = _make_fill(dummy_order)
        fill_model = _FixedFillModel(result=fill)

        router, inp = _make_routing_input(
            fill_model=fill_model,
            experiment_state=_safe_experiment_state(),
        )
        result = router.route(inp)
        assert result == fill


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
            strategy_id=_STRATEGY_ID,
        )
        fill = _make_fill(dummy_order)
        fill_model = _FixedFillModel(result=fill)

        router, inp = _make_routing_input(
            fill_model=fill_model,
            open_position_count=3,
        )
        result = router.route(inp)
        assert result == RiskRejected(
            reason=(
                "SA-4 orphan-position cap: open positions 3 >= max 3 (pre-reg concurrent limit)."
            ),
            rule="orphan-position",
        )
        assert fill_model.orders == []

    def test_at_max_concurrent_rule_is_orphan_position(self) -> None:
        """RiskRejected.rule must mention orphan-position for traceability."""
        dummy_order = Order(
            side=OrderSide.BUY,
            pair=_BTC_USDT,
            qty=Quantity(amount=Decimal("0.001"), asset=Currency.BTC),
            strategy_id=_STRATEGY_ID,
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
        assert result.rule == "orphan-position", (
            f"RiskRejected.rule must be 'orphan-position' for SA-4 traceability. "
            f"Got rule={result.rule!r}."
        )

    def test_above_max_concurrent_also_rejected(self) -> None:
        """open_position_count == 4 also returns RiskRejected."""
        dummy_order = Order(
            side=OrderSide.BUY,
            pair=_BTC_USDT,
            qty=Quantity(amount=Decimal("0.001"), asset=Currency.BTC),
            strategy_id=_STRATEGY_ID,
        )
        fill = _make_fill(dummy_order)
        fill_model = _FixedFillModel(result=fill)

        router, inp = _make_routing_input(
            fill_model=fill_model,
            open_position_count=4,
        )
        result = router.route(inp)
        assert result == RiskRejected(
            reason=(
                "SA-4 orphan-position cap: open positions 4 >= max 3 (pre-reg concurrent limit)."
            ),
            rule="orphan-position",
        )
        assert fill_model.orders == []

    def test_below_max_concurrent_allows_routing(self) -> None:
        """open_position_count == 2 must not trigger the orphan-position guard."""
        dummy_order = Order(
            side=OrderSide.BUY,
            pair=_BTC_USDT,
            qty=Quantity(amount=Decimal("0.001"), asset=Currency.BTC),
            strategy_id=_STRATEGY_ID,
        )
        fill = _make_fill(dummy_order)
        fill_model = _FixedFillModel(result=fill)

        router, inp = _make_routing_input(
            fill_model=fill_model,
            open_position_count=2,
        )
        result = router.route(inp)
        assert result == fill, (
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
            strategy_id=_STRATEGY_ID,
        )
        fill = _make_fill(dummy_order)
        fill_model = _FixedFillModel(result=fill)

        halt_rule = _FixedRule(action=RiskAction.HALT, reason="test HALT rule fired")
        router, inp = _make_routing_input(
            fill_model=fill_model,
            risk_rules=[halt_rule],
        )
        result = router.route(inp)
        assert result == RiskRejected(reason="test HALT rule fired", rule="HALT")
        assert fill_model.orders == []

    def test_halt_rule_result_mentions_halt(self) -> None:
        """RiskRejected.rule must be 'HALT' when overlay fires HALT."""
        dummy_order = Order(
            side=OrderSide.BUY,
            pair=_BTC_USDT,
            qty=Quantity(amount=Decimal("0.001"), asset=Currency.BTC),
            strategy_id=_STRATEGY_ID,
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
            strategy_id=_STRATEGY_ID,
        )
        fill = _make_fill(dummy_order)
        fill_model = _FixedFillModel(result=fill)

        ok_rule = _FixedRule(action=RiskAction.OK, reason="ok stub")
        router, inp = _make_routing_input(
            fill_model=fill_model,
            risk_rules=[ok_rule],
        )
        result = router.route(inp)
        assert result == fill
        # 1% of 5000 risked over a 1500 stop distance is 0.0333 BTC; the 5% position cap
        # (250 USDT / 50000) binds first, so the dispatched order is 0.005 BTC.
        assert fill_model.orders == [
            Order(
                side=OrderSide.BUY,
                pair=_BTC_USDT,
                qty=Quantity(amount=Decimal("0.005"), asset=Currency.BTC),
                strategy_id=_STRATEGY,
                limit_price=None,
            )
        ]

    def test_warn_rule_allows_dispatch(self) -> None:
        """A WARN rule allows the router to proceed (log + continue)."""
        dummy_order = Order(
            side=OrderSide.BUY,
            pair=_BTC_USDT,
            qty=Quantity(amount=Decimal("0.001"), asset=Currency.BTC),
            strategy_id=_STRATEGY_ID,
        )
        fill = _make_fill(dummy_order)
        fill_model = _FixedFillModel(result=fill)

        warn_rule = _FixedRule(action=RiskAction.WARN, reason="warn stub — proceed")
        router, inp = _make_routing_input(
            fill_model=fill_model,
            risk_rules=[warn_rule],
        )
        result = router.route(inp)
        assert result == fill
        # 1% of 5000 risked over a 1500 stop distance is 0.0333 BTC; the 5% position cap
        # (250 USDT / 50000) binds first, so the dispatched order is 0.005 BTC.
        assert fill_model.orders == [
            Order(
                side=OrderSide.BUY,
                pair=_BTC_USDT,
                qty=Quantity(amount=Decimal("0.005"), asset=Currency.BTC),
                strategy_id=_STRATEGY,
                limit_price=None,
            )
        ]


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
            strategy_id=_STRATEGY_ID,
        )
        expected_fill = _make_fill(dummy_order)
        fill_model = _FixedFillModel(result=expected_fill)

        router, inp = _make_routing_input(fill_model=fill_model)
        result = router.route(inp)
        assert result is expected_fill

    def test_fill_rejection_wrapped_as_risk_rejected(self) -> None:
        """FillModelPort Rejection is wrapped as RiskRejected with rule='fill_model'."""
        dummy_order = Order(
            side=OrderSide.BUY,
            pair=_BTC_USDT,
            qty=Quantity(amount=Decimal("0.001"), asset=Currency.BTC),
            strategy_id=_STRATEGY_ID,
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


class _RecordingFillModel:
    """FillModelPort stub that records every order it is asked to fill."""

    def __init__(self) -> None:
        self.orders: list[Order] = []

    def fill_order(
        self,
        order: Order,
        orderbook: Orderbook,
        clock: datetime,
        fees: FeeSchedule,
    ) -> Fill | Rejection:
        """Record ``order`` and fill it in full."""
        self.orders.append(order)
        return _make_fill(order)


class TestOrderRouterSizingCaps:
    """The router dispatches the pre-reg §5.4 size and never an entry past the §5.5 cap."""

    # SEV-1: no rubric row matches; pre-reg §5.4 per-position cap stops over-exposure
    def test_dispatched_qty_is_the_capped_position(self) -> None:
        """Entry 50000, stop 48500 at 5,000 USDT: 250 USDT hard cap → 0.005 BTC sent to fill."""
        fill_model = _RecordingFillModel()
        router, inp = _make_routing_input(fill_model=fill_model)
        router.route(inp)
        assert [o.qty.amount for o in fill_model.orders] == [Decimal("0.005")]

    # SEV-1: no rubric row matches; pre-reg §5.5 total exposure cap stops over-exposure
    def test_entry_past_the_exposure_cap_is_rejected_before_dispatch(self) -> None:
        """With 750 USDT open the router returns RiskRejected(rule='sizing') and fills nothing."""
        fill_model = _RecordingFillModel()
        router, inp = _make_routing_input(fill_model=fill_model)
        blocked = replace(inp, sizing_input=replace(inp.sizing_input, open_notional=Decimal("750")))
        result = router.route(blocked)
        assert isinstance(result, RiskRejected), f"expected RiskRejected, got {result!r}"
        assert (result.rule, fill_model.orders) == ("sizing", [])


class TestOrderRouterStrategyAttribution:
    """The order the router builds is owned by the requesting strategy."""

    def test_order_sent_to_fill_model_carries_the_requesting_strategy_id(self) -> None:
        """Order dispatched to the fill model has strategy_id == RoutingInput.strategy_id."""
        seen: list[Order] = []

        class _RecordingFillModel:
            def fill_order(
                self,
                order: Order,
                orderbook: Orderbook,
                clock: datetime,
                fees: FeeSchedule,
            ) -> Fill | Rejection:
                seen.append(order)
                return _make_fill(order)

        router, inp = _make_routing_input(fill_model=_RecordingFillModel())
        result = router.route(inp)
        assert isinstance(result, Fill)
        assert [o.strategy_id for o in seen] == [inp.strategy_id]
        assert result.strategy_id == inp.strategy_id


# ---------------------------------------------------------------------------
# Risk-overlay log lines stay value-free (#513)
# ---------------------------------------------------------------------------

_DRAWDOWN_HALT = "RO-08: portfolio drawdown HALT — drawdown=17.2500% >= threshold=15.0000%"


def _route_with_rules(
    rules: list[RiskOverlayPort],
) -> tuple[Fill | RiskRejected | KillCircuitBreaker, list[MutableMapping[str, object]]]:
    order = Order(
        side=OrderSide.BUY,
        pair=_BTC_USDT,
        qty=Quantity(amount=Decimal("0.001"), asset=Currency.BTC),
        strategy_id=_STRATEGY_ID,
    )
    router, inp = _make_routing_input(
        fill_model=_FixedFillModel(result=_make_fill(order)),
        risk_rules=rules,
    )
    with structlog.testing.capture_logs() as logs:
        result = router.route(inp)
    overlay = [entry for entry in logs if str(entry["event"]).startswith("risk_overlay_")]
    return result, overlay


class TestRiskOverlayLogsAreValueFree:
    """Risk-overlay log lines carry the rule id and a fixed reason, never a figure."""

    def test_drawdown_halt_logs_the_rule_and_a_fixed_reason(self) -> None:
        """An RO-08 halt logs rule=RO-08 reason=drawdown_limit and no drawdown figure."""
        _, overlay = _route_with_rules([_FixedRule(RiskAction.HALT, _DRAWDOWN_HALT)])
        halt = next(entry for entry in overlay if entry["event"] == "risk_overlay_halt")
        assert (halt["rule"], halt["reason"], halt["log_level"]) == (
            "RO-08",
            "drawdown_limit",
            "warning",
        )

    def test_drawdown_figure_stays_on_the_rejection_for_operators(self) -> None:
        """The RiskRejected result keeps the full reason, figure included."""
        result, _ = _route_with_rules([_FixedRule(RiskAction.HALT, _DRAWDOWN_HALT)])
        assert isinstance(result, RiskRejected)
        assert result.reason == _DRAWDOWN_HALT

    def test_reason_without_a_known_rule_is_unclassified(self) -> None:
        """A reason that names no RO rule logs rule=unknown reason=unclassified."""
        _, overlay = _route_with_rules([_FixedRule(RiskAction.HALT, "halt at 12.5% equity")])
        halt = next(entry for entry in overlay if entry["event"] == "risk_overlay_halt")
        assert (halt["rule"], halt["reason"]) == ("unknown", "unclassified")

    @given(
        rule=st.sampled_from([f"RO-0{n}" for n in range(1, 9)]),
        action=st.sampled_from(list(RiskAction)),
        figure=st.decimals(min_value=0, max_value=1_000_000, places=4),
    )
    def test_no_overlay_line_carries_a_figure(
        self, rule: str, action: RiskAction, figure: Decimal
    ) -> None:
        """Whatever figure a rule quotes, no overlay line holds a digit beyond the rule id."""
        reason = f"{rule}: value={figure}% equity={figure} pnl={figure}"
        _, overlay = _route_with_rules([_FixedRule(action, reason)])
        values = [str(value) for entry in overlay for key, value in entry.items() if key != "rule"]
        assert (bool(overlay), [v for v in values if any(c.isdigit() for c in v)]) == (True, [])
