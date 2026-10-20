"""RO-02 SpreadSpike — halts entries when bid-ask spread spikes.

When the current bid-ask spread is ≥ 10x the 1-hour rolling median spread,
execution cost risk is too high and new entries are blocked.

Threshold (binding — pre-reg §5.5): current_spread ≥ 10 x median_spread_1h.

Reference: SA-9 §5.5 RO-02; spec-8-risk-overlay change.md.
"""

from decimal import Decimal

from hermes.domain.risk.state import MarketSnapshot, PortfolioState
from hermes.domain.risk.types import RiskAction, RiskDecision

__all__ = ["SpreadSpike"]

# Pre-reg §5.5 RO-02 — DO NOT change without updating the signed document.
_SPREAD_MULTIPLIER: Decimal = Decimal("10")


class SpreadSpike:
    """RO-02: HALT when current spread ≥ 10x 1H median spread.

    Stateless — all state flows through the inputs per the RiskOverlayPort contract.
    """

    def evaluate(
        self,
        portfolio_state: PortfolioState,
        market_snapshot: MarketSnapshot,
    ) -> RiskDecision:
        """Fire HALT when current spread is at least 10x the 1H median.

        When the median spread is zero (no data), the rule conservatively returns
        HALT to avoid division semantics — a zero median is abnormal.

        Args:
            portfolio_state: Not used by this rule; accepted to satisfy the Protocol.
            market_snapshot: Must contain ``current_spread`` and
                ``median_spread_1h`` (both as Decimal fractions).

        Returns:
            :class:`~hermes.ports.risk_overlay.RiskDecision` with HALT when
            the spread spike threshold is crossed; OK otherwise.
        """
        current = market_snapshot.current_spread
        median = market_snapshot.median_spread_1h

        if median <= Decimal("0"):
            return RiskDecision(
                action=RiskAction.HALT,
                reason=(
                    f"RO-02: 1H median spread is {median} (≤ 0) —"
                    " data absent, halting conservatively"
                ),
            )

        threshold = _SPREAD_MULTIPLIER * median
        if current >= threshold:
            return RiskDecision(
                action=RiskAction.HALT,
                reason=(
                    f"RO-02: spread spike detected — current={current:.6f}"
                    f" ≥ {_SPREAD_MULTIPLIER}x median={median:.6f}"
                    f" (threshold={threshold:.6f})"
                ),
            )

        return RiskDecision(
            action=RiskAction.OK,
            reason=(
                f"RO-02: spread OK — current={current:.6f}"
                f" < {_SPREAD_MULTIPLIER}x median={median:.6f}"
            ),
        )
