"""RO-08 PortfolioDrawdown — halts when portfolio drawdown exceeds pre-reg threshold.

When the portfolio drawdown from peak reaches ≥ 15% (pre-reg §10), the rule
returns HALT and sets a signal that surfaces to KillTriggerEvaluator (Spec 9,
#227). The 15% threshold is the hard floor from the pre-registration document.

Threshold (binding — pre-reg §10): drawdown ≥ 0.15 (15%) → HALT.

Reference: SA-9 §5.5 RO-08; pre-reg §10; spec-8-risk-overlay change.md.
"""

from __future__ import annotations

from decimal import Decimal

from hermes.domain.risk.state import MarketSnapshot, PortfolioState
from hermes.domain.risk.types import RiskAction, RiskDecision

__all__ = ["PortfolioDrawdown"]

# Pre-reg §10 — DO NOT change without updating the signed pre-registration document.
_DRAWDOWN_HALT_THRESHOLD: Decimal = Decimal("0.15")  # 15%


class PortfolioDrawdown:
    """RO-08: HALT when portfolio drawdown ≥ 15% from peak.

    Also surfaces the halt requirement through ``portfolio_state.dd_halt_required``
    — the flag is read by KillTriggerEvaluator (Spec 9, #227) to trigger the
    kill switch. This rule does NOT mutate state; it only reads it and reports.
    The portfolio-state calculator (Spec 3) is responsible for setting
    ``dd_halt_required = True`` when it detects the breach.

    Stateless — all state flows through the inputs per the RiskOverlayPort contract.
    """

    def evaluate(
        self,
        portfolio_state: PortfolioState,
        market_snapshot: MarketSnapshot,
    ) -> RiskDecision:
        """Fire HALT when portfolio drawdown ≥ 15% or dd_halt_required is set.

        Checks ``portfolio_state.drawdown`` (computed property from peak/current
        values) AND ``portfolio_state.dd_halt_required`` (flag set by Spec 3
        calculators). Either condition alone triggers HALT.

        Args:
            portfolio_state: Must contain ``peak_value``, ``current_value``,
                and ``dd_halt_required``.
            market_snapshot: Not used by this rule; accepted to satisfy Protocol.

        Returns:
            :class:`~hermes.ports.risk_overlay.RiskDecision` with HALT when
            drawdown threshold is reached or halt flag is set; OK otherwise.
        """
        drawdown = portfolio_state.drawdown

        if portfolio_state.dd_halt_required:
            return RiskDecision(
                action=RiskAction.HALT,
                reason=(
                    f"RO-08: portfolio drawdown halt required — dd_halt_required=True,"
                    f" drawdown={drawdown:.4%} (threshold={_DRAWDOWN_HALT_THRESHOLD:.4%})"
                ),
            )

        if drawdown >= _DRAWDOWN_HALT_THRESHOLD:
            return RiskDecision(
                action=RiskAction.HALT,
                reason=(
                    f"RO-08: portfolio drawdown HALT — drawdown={drawdown:.4%}"
                    f" ≥ threshold={_DRAWDOWN_HALT_THRESHOLD:.4%}"
                    " (escalate to KillTriggerEvaluator)"
                ),
            )

        return RiskDecision(
            action=RiskAction.OK,
            reason=(
                f"RO-08: portfolio drawdown OK — drawdown={drawdown:.4%}"
                f" < {_DRAWDOWN_HALT_THRESHOLD:.4%}"
            ),
        )
