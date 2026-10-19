"""RiskEngine — composes multiple RiskOverlayPort rules and enforces capital budgets.

Takes a list of :class:`~hermes.ports.risk_overlay.RiskOverlayPort` rule
implementations, evaluates them all against the current portfolio state and
market snapshot, and returns the worst outcome (HALT > WARN > OK). All
individual rule decisions are preserved for logging.

Per-strategy capital budget enforcement (SA-6) is also housed here. The
``capital_budget`` dict maps :class:`~hermes.domain.value_objects.strategy_id.StrategyId`
to a :class:`~hermes.domain.value_objects.money.Money` maximum notional. When the
strategy's open notional meets or exceeds its budget, the engine returns HALT.

MVP1 note: multi-strategy balancing is deferred to SA-6 MVP2. For MVP1 the
``capital_budget`` is typically a single entry for the ``"swing-donchian-4h"`` strategy.

Reference: spec-3-order-router change.md D-3, SA-6, SA-9 §5.5.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from hermes.domain.risk.state import MarketSnapshot, PortfolioState
from hermes.domain.risk.types import RiskAction, RiskDecision
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.strategy_id import StrategyId
from hermes.ports.risk_overlay import RiskOverlayPort

__all__ = [
    "RiskEngine",
    "RiskEngineResult",
]


@dataclass(frozen=True, slots=True)
class RiskEngineResult:
    """Aggregated result of all risk rule evaluations.

    Args:
        worst: The highest-severity :class:`~hermes.domain.risk.types.RiskDecision`
            across all evaluated rules (HALT > WARN > OK).
        decisions: All individual rule decisions in evaluation order.
            Callers should log all entries — even when ``worst.action`` is
            HALT, the WARN decisions are informational and should not be
            silently dropped.

    Reference: spec-3-order-router change.md D-3.
    """

    worst: RiskDecision
    decisions: list[RiskDecision]


class RiskEngine:
    """Stateless risk overlay engine for Swing MVP1.

    Composes a list of :class:`~hermes.ports.risk_overlay.RiskOverlayPort`
    rule implementations. Each rule is evaluated in order; the worst outcome
    (HALT > WARN > OK) is surfaced to the caller.

    Optionally enforces per-strategy capital budgets (SA-6). When
    ``capital_budget`` is provided, the engine compares the requested
    notional for ``strategy_id`` against the budget and returns HALT if the
    budget is exhausted.

    Args:
        rules: Ordered list of :class:`~hermes.ports.risk_overlay.RiskOverlayPort`
            rule implementations. May be empty — an empty list always returns OK.
        capital_budget: Optional mapping of
            :class:`~hermes.domain.value_objects.strategy_id.StrategyId` →
            :class:`~hermes.domain.value_objects.money.Money` maximum open
            notional per strategy. When absent, no budget enforcement is applied.

    Reference: spec-3-order-router change.md D-3, SA-6.
    """

    def __init__(
        self,
        rules: list[RiskOverlayPort],
        capital_budget: dict[StrategyId, Money] | None = None,
    ) -> None:
        """Initialise the engine with a rule list and optional capital budgets.

        Args:
            rules: List of risk overlay rule implementations.
            capital_budget: Per-strategy open-notional caps (SA-6). Optional;
                when ``None`` no budget enforcement is applied.
        """
        self._rules = rules
        self._capital_budget: dict[StrategyId, Money] = capital_budget or {}

    def evaluate(
        self,
        portfolio_state: PortfolioState,
        market_snapshot: MarketSnapshot,
    ) -> RiskEngineResult:
        """Evaluate all rules and return the worst combined outcome.

        Evaluates every rule in ``self._rules`` regardless of early HALT
        detection — all WARN decisions are preserved for logging even when
        a HALT is found. The caller is responsible for logging
        ``result.decisions`` before acting on ``result.worst``.

        Args:
            portfolio_state: Current portfolio snapshot.
            market_snapshot: Current market-microstructure snapshot.

        Returns:
            :class:`RiskEngineResult` with ``worst`` set to the highest-severity
            decision and ``decisions`` containing every individual rule's verdict.
        """
        decisions: list[RiskDecision] = []

        for rule in self._rules:
            decision = rule.evaluate(portfolio_state, market_snapshot)
            decisions.append(decision)

        if not decisions:
            ok = RiskDecision(action=RiskAction.OK, reason="RiskEngine: no rules configured — OK")
            return RiskEngineResult(worst=ok, decisions=[ok])

        worst = max(decisions, key=lambda d: d.action.severity)
        return RiskEngineResult(worst=worst, decisions=decisions)

    def check_budget(
        self,
        strategy_id: StrategyId,
        requested_notional: Decimal,
        current_open_notional: Decimal,
    ) -> RiskDecision:
        """Check whether a new order would breach the strategy's capital budget.

        Args:
            strategy_id: The strategy requesting the new position.
            requested_notional: The quote-currency notional of the new order.
            current_open_notional: The strategy's existing open notional
                (sum of mark-to-market notional of all open positions).

        Returns:
            :class:`~hermes.domain.risk.types.RiskDecision` with
            :attr:`~hermes.domain.risk.types.RiskAction.HALT` when
            ``current_open_notional + requested_notional`` exceeds the
            strategy's budget, or
            :attr:`~hermes.domain.risk.types.RiskAction.OK` otherwise.
            When no budget is configured for the strategy, returns OK.

        Reference: SA-6.
        """
        if strategy_id not in self._capital_budget:
            return RiskDecision(
                action=RiskAction.OK,
                reason=f"SA-6: no capital budget configured for {strategy_id} — OK",
            )

        budget = self._capital_budget[strategy_id]
        total = current_open_notional + requested_notional

        if total > budget.amount:
            return RiskDecision(
                action=RiskAction.HALT,
                reason=(
                    f"SA-6: capital budget exhausted for strategy '{strategy_id}'. "
                    f"Open notional {current_open_notional!r} + "
                    f"requested {requested_notional!r} = {total!r} "
                    f"exceeds budget {budget.amount!r} {budget.currency}."
                ),
            )

        return RiskDecision(
            action=RiskAction.OK,
            reason=(
                f"SA-6: capital budget OK for strategy '{strategy_id}': "
                f"{total!r} ≤ {budget.amount!r} {budget.currency}."
            ),
        )
