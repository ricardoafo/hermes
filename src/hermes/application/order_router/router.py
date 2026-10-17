"""OrderRouter — application-layer orchestrator for signal → order dispatch.

Composes the full T-1 pipeline for Swing MVP1:

1. **Kill circuit breaker** — evaluates ``ExperimentState``
   against Spec 9 §10 kill criteria. Returns
   :class:`KillCircuitBreaker` immediately if triggered.

2. **Orphan-position guard (SA-4 P0)** — rejects new entries when
   ``open_position_count >= max_concurrent_positions`` (3 per pre-reg).

3. **Risk overlay** — evaluates all registered
   :class:`~hermes.ports.risk_overlay.RiskOverlayPort` rules plus the
   optional correlation-lock check (RO-01). HALT → :class:`RiskRejected`;
   WARN → log + proceed.

4. **Sizing** — computes position size via ``SizingCalculator``
   per pre-reg §5.4 (risk ÷ stop distance, hard cap) and §5.5 (exposure cap).

5. **Dispatch** — calls :meth:`~hermes.ports.fill_model.FillModelPort.fill_order`
   with the computed order. Returns :class:`~hermes.domain.trading.fill.Fill`
   on success or :class:`RiskRejected` for a fill-model
   :class:`~hermes.domain.trading.fill.Rejection`.

The router is stateless — all inputs arrive at call time. The composition
root wires dependencies at startup.

Reference: spec-3-order-router change.md, pre-registration-v1.md §5.4 and §10.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Union

import structlog
from returns.result import Failure

from hermes.application.order_router.risk_engine import RiskEngine
from hermes.application.order_router.sizing import SizingCalculator, SizingInput
from hermes.application.strategy_protocol.kill_trigger_evaluator import (
    ExperimentState,
    KillDecision,
    KillTriggerEvaluator,
)
from hermes.domain.fees import FeeSchedule
from hermes.domain.market_data.orderbook import Orderbook
from hermes.domain.risk.state import MarketSnapshot, PortfolioState
from hermes.domain.trading.fill import Fill
from hermes.domain.trading.order import Order, OrderSide
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.quantity import Quantity
from hermes.domain.value_objects.strategy_id import StrategyId
from hermes.ports.fill_model import FillModelPort

__all__ = [
    "KillCircuitBreaker",
    "OrderRouter",
    "OrderRouterResult",
    "RiskRejected",
    "RoutingInput",
]

_log = structlog.get_logger(__name__)

# Pre-reg binding constants — DO NOT change without updating the signed document.
_MAX_CONCURRENT_POSITIONS: int = 3  # pre-reg concurrent position cap

# Blind surfaces read the journal (decision record 2026-10-03, ruling 1), and some
# rule reasons quote P&L-derived figures such as the RO-08 drawdown, so risk-overlay
# log lines carry only the rule id and a fixed reason; the full text stays on the
# RiskRejected result for operator surfaces (#513).
_RULE_ID = re.compile(r"^(RO-\d{2})\b")
_RULE_LOG_REASONS: dict[str, str] = {
    "RO-01": "correlation_lock",
    "RO-02": "spread_spike",
    "RO-03": "slippage_limit",
    "RO-04": "stale_orderbook",
    "RO-05": "stablecoin_depeg_warn",
    "RO-06": "stablecoin_depeg_halt",
    "RO-07": "macro_volatility",
    "RO-08": "drawdown_limit",
}

# OrderRouterResult discriminated union — callers pattern-match on isinstance.
# Forward references (strings) are used because the classes are defined below.
OrderRouterResult = Union[Fill, "RiskRejected", "KillCircuitBreaker"]


@dataclass(frozen=True, slots=True)
class RiskRejected:
    """Order rejected by a risk rule or guard.

    This is an expected business outcome (not an error). The caller should
    log ``reason`` and ``rule`` for audit.

    Args:
        reason: Human-readable explanation of why the order was rejected.
        rule: Short identifier of the rule or guard that fired
            (e.g. ``"RO-01"``, ``"orphan-position"``, ``"HALT"``).

    Reference: spec-3-order-router change.md D-4.
    """

    reason: str
    rule: str

    def __post_init__(self) -> None:
        """Validate that reason and rule are non-empty.

        Raises:
            ValueError: On empty ``reason`` or ``rule``.
        """
        if not self.reason.strip():
            raise ValueError(
                "RiskRejected.reason must be non-empty — provide a human-readable explanation."
            )
        if not self.rule.strip():
            raise ValueError("RiskRejected.rule must be non-empty — provide the rule identifier.")


@dataclass(frozen=True, slots=True)
class KillCircuitBreaker:
    """Order routing halted because a §10 kill criterion fired.

    This is an expected business outcome (not an error). The caller must
    halt all new entries and surface the ``decision.reason`` to the operator.

    Args:
        decision: The ``KillDecision`` that triggered the circuit breaker.
            Always has ``triggered=True``.

    Reference: pre-registration-v1.md §10; spec-3-order-router change.md D-4.
    """

    decision: KillDecision

    def __post_init__(self) -> None:
        """Validate that the decision is actually triggered.

        Raises:
            ValueError: When ``decision.triggered`` is ``False`` — constructing
                a :class:`KillCircuitBreaker` with an untriggered decision is a
                programming error.
        """
        if not self.decision.triggered:
            raise ValueError(
                "KillCircuitBreaker.decision.triggered must be True. "
                "Do not construct KillCircuitBreaker for non-triggered decisions."
            )


@dataclass(frozen=True, slots=True)
class RoutingInput:
    """Immutable input bundle for a single :meth:`OrderRouter.route` call.

    Args:
        strategy_id: Bucket identifier of the requesting strategy.
        side: Order direction (:attr:`~hermes.domain.trading.order.OrderSide.BUY`
            or :attr:`~hermes.domain.trading.order.OrderSide.SELL`).
        pair: Trading pair.
        entry_px: Intended entry price (quote per base unit).
        sizing_input: Inputs to the T-1 sizing formula.
        portfolio_state: Current portfolio snapshot (positions, correlations, drawdown).
        market_snapshot: Current market-microstructure snapshot.
        experiment_state: Running experiment state for kill-trigger evaluation.
        orderbook: Depth snapshot for ``pair`` at routing time.
        clock: UTC-aware timestamp for the fill model.
        fees: Fee schedule for ``pair``.
        open_position_count: Number of currently open positions across all strategies.
            Used for the SA-4 orphan-position guard.

    Reference: spec-3-order-router change.md.
    """

    strategy_id: StrategyId
    side: OrderSide
    pair: Pair
    entry_px: Decimal
    sizing_input: SizingInput
    portfolio_state: PortfolioState
    market_snapshot: MarketSnapshot
    experiment_state: ExperimentState
    orderbook: Orderbook
    clock: datetime
    fees: FeeSchedule
    open_position_count: int


class OrderRouter:
    """Application-layer orchestrator: signal → sizing → risk → dispatch.

    Stateless — holds references to injected collaborators but no mutable
    fields. All business state flows through :class:`RoutingInput`.

    Args:
        fill_model: :class:`~hermes.ports.fill_model.FillModelPort` implementation
            (backtest, shadow, or live) wired at the composition root.
        risk_engine: :class:`~hermes.application.order_router.risk_engine.RiskEngine`
            with a pre-configured list of risk rules.
        sizing_calculator: :class:`~hermes.application.order_router.sizing.SizingCalculator`
            instance (stateless, so a single shared instance is fine).
        kill_evaluator: ``KillTriggerEvaluator`` instance initialised with
            the signed pre-registration thresholds.

    Reference: spec-3-order-router change.md.
    """

    def __init__(
        self,
        fill_model: FillModelPort,
        risk_engine: RiskEngine,
        sizing_calculator: SizingCalculator,
        kill_evaluator: KillTriggerEvaluator,
    ) -> None:
        """Initialise the router with injected collaborators.

        Args:
            fill_model: Fill model implementation.
            risk_engine: Composed risk overlay engine.
            sizing_calculator: Stateless sizing calculator.
            kill_evaluator: Kill-trigger evaluator with §10 thresholds.
        """
        self._fill_model = fill_model
        self._risk_engine = risk_engine
        self._sizing = sizing_calculator
        self._kill_eval = kill_evaluator

    def route(self, inp: RoutingInput) -> OrderRouterResult:
        """Route a single trading signal through the full T-1 pipeline.

        Evaluation order (fail-fast):

        1. Kill circuit breaker — :class:`KillCircuitBreaker` if triggered.
        2. Orphan-position guard (SA-4 P0) — :class:`RiskRejected` if
           ``open_position_count >= _MAX_CONCURRENT_POSITIONS``. Then a BUY of a
           pair already held — :class:`RiskRejected` ``already-held`` (§5.4,
           one position per pair; issue #902).
        3. Risk overlay (all rules, worst wins) — :class:`RiskRejected` on HALT;
           WARN rules are logged and execution continues.
        4. Position sizing — :class:`RiskRejected` if sizing fails (degenerate inputs).
        5. Fill model dispatch — :class:`~hermes.domain.trading.fill.Fill` on success;
           :class:`RiskRejected` wrapping a fill-model
           :class:`~hermes.domain.trading.fill.Rejection`.

        Args:
            inp: Immutable routing input bundle.

        Returns:
            One of:
            - :class:`~hermes.domain.trading.fill.Fill` — order executed successfully.
            - :class:`RiskRejected` — blocked by a risk rule, guard, or fill rejection.
            - :class:`KillCircuitBreaker` — experiment kill criterion fired.

        Reference: spec-3-order-router change.md, pre-registration-v1.md §5.4, §10.
        """
        # ── 1. Kill circuit breaker ─────────────────────────────────────────
        kill_decision = self._kill_eval.evaluate(inp.experiment_state)
        if kill_decision.triggered:
            _log.warning(
                "kill_circuit_breaker_triggered",
                reason=kill_decision.reason,
                strategy_id=str(inp.strategy_id),
            )
            return KillCircuitBreaker(decision=kill_decision)

        # ── 2. Orphan-position guard (SA-4 P0) ─────────────────────────────
        if inp.open_position_count >= _MAX_CONCURRENT_POSITIONS:
            reason = (
                f"SA-4 orphan-position cap: open positions {inp.open_position_count} "
                f">= max {_MAX_CONCURRENT_POSITIONS} (pre-reg concurrent limit)."
            )
            _log.warning(
                "orphan_position_cap_hit",
                open_positions=inp.open_position_count,
                max_concurrent=_MAX_CONCURRENT_POSITIONS,
                strategy_id=str(inp.strategy_id),
            )
            return RiskRejected(reason=reason, rule="orphan-position")
        if inp.side is OrderSide.BUY and inp.pair in inp.portfolio_state.held_pairs:
            # The cap above counts positions, not pairs; a strategy seeded flat
            # on a held coin would otherwise buy it again (#902).
            _log.warning("already_held_entry_refused", pair=str(inp.pair))
            return RiskRejected(reason=f"{inp.pair} is already held", rule="already-held")

        # ── 3. Risk overlay ─────────────────────────────────────────────────
        engine_result = self._risk_engine.evaluate(
            inp.portfolio_state,
            inp.market_snapshot,
        )

        # Log all individual rule decisions for audit.
        for decision in engine_result.decisions:
            rule, rule_reason = _value_free(decision.reason)
            if decision.action.value == "warn":
                _log.warning(
                    "risk_overlay_warn",
                    rule=rule,
                    reason=rule_reason,
                    strategy_id=str(inp.strategy_id),
                )
            else:
                _log.debug(
                    "risk_overlay_decision",
                    action=decision.action.value,
                    rule=rule,
                    reason=rule_reason,
                )

        worst = engine_result.worst
        if worst.action.value == "halt":
            rule, rule_reason = _value_free(worst.reason)
            _log.warning(
                "risk_overlay_halt",
                rule=rule,
                reason=rule_reason,
                strategy_id=str(inp.strategy_id),
            )
            return RiskRejected(reason=worst.reason, rule="HALT")

        # ── 4. Position sizing ──────────────────────────────────────────────
        sizing_result = self._sizing.compute(inp.sizing_input)
        if isinstance(sizing_result, Failure):
            error = sizing_result.failure()
            _log.error(
                "sizing_failed",
                error=str(error),
                strategy_id=str(inp.strategy_id),
            )
            return RiskRejected(reason=str(error), rule="sizing")

        sizing = sizing_result.unwrap()
        _log.debug(
            "sizing_computed",
            qty=str(sizing.qty),
            notional=str(sizing.notional),
            binding_cap=sizing.binding_cap,
            strategy_id=str(inp.strategy_id),
        )

        # ── 5. Build order and dispatch ─────────────────────────────────────
        return self._dispatch(inp=inp, qty=sizing.qty)

    def _dispatch(self, *, inp: RoutingInput, qty: Decimal) -> Fill | RiskRejected:
        """Build an order and call the fill model.

        Args:
            inp: Routing input bundle.
            qty: Computed base-asset quantity from the sizing step.

        Returns:
            :class:`~hermes.domain.trading.fill.Fill` on a successful execution,
            or :class:`RiskRejected` wrapping a fill-model rejection or a
            base-currency validation error.
        """
        try:
            asset = Currency(inp.pair.base)
        except ValueError:
            return RiskRejected(
                reason=f"Unknown base currency for pair {inp.pair}: {inp.pair.base!r}",
                rule="routing",
            )

        order = Order(
            side=inp.side,
            pair=inp.pair,
            qty=Quantity(amount=qty, asset=asset),
            strategy_id=inp.strategy_id,
            limit_price=None,  # market order for MVP1
        )

        fill_result = self._fill_model.fill_order(
            order,
            inp.orderbook,
            inp.clock,
            inp.fees,
        )

        if isinstance(fill_result, Fill):
            _log.info(
                "order_filled",
                pair=str(inp.pair),
                qty=str(qty),
                avg_price=str(fill_result.avg_price),
                strategy_id=str(inp.strategy_id),
            )
            return fill_result

        # fill_result is a Rejection — wrap as RiskRejected.
        _log.warning(
            "fill_rejected",
            reason=fill_result.reason,
            strategy_id=str(inp.strategy_id),
        )
        return RiskRejected(reason=fill_result.reason, rule="fill_model")


def _value_free(reason: str) -> tuple[str, str]:
    """Return the rule id and a fixed reason for a risk-overlay log line.

    Args:
        reason: The rule's human-readable reason, which may quote figures.

    Returns:
        ``(rule_id, reason)``, such as ``("RO-08", "drawdown_limit")``, or
        ``("unknown", "unclassified")`` when the reason names no known rule.
    """
    match = _RULE_ID.match(reason)
    rule = match.group(1) if match else "unknown"
    return (
        (rule, _RULE_LOG_REASONS[rule])
        if rule in _RULE_LOG_REASONS
        else ("unknown", "unclassified")
    )
