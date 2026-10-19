"""RiskEngine — composes multiple RiskOverlayPort rules.

Takes a list of :class:`~hermes.ports.risk_overlay.RiskOverlayPort` rule
implementations, evaluates them all against the current portfolio state and
market snapshot, and returns the worst outcome (HALT > WARN > OK). All
individual rule decisions are preserved for logging.

Capital limits are not enforced here: the pre-registered §5.5 total-exposure
cap lives in :class:`~hermes.application.order_router.sizing.SizingCalculator`.

Reference: spec-3-order-router change.md D-3.
"""

from dataclasses import dataclass

from hermes.domain.risk.state import MarketSnapshot, PortfolioState
from hermes.domain.risk.types import RiskAction, RiskDecision
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

    Args:
        rules: Ordered list of :class:`~hermes.ports.risk_overlay.RiskOverlayPort`
            rule implementations. May be empty — an empty list always returns OK.

    Reference: spec-3-order-router change.md D-3.
    """

    def __init__(self, rules: list[RiskOverlayPort]) -> None:
        """Initialise the engine with a rule list.

        Args:
            rules: List of risk overlay rule implementations.
        """
        self._rules = rules

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
