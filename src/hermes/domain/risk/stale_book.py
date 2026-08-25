"""RO-04 StaleBook — halts when the orderbook snapshot is too old.

When the orderbook age exceeds 2 seconds, the market microstructure data is
considered stale and new entries are blocked to prevent trading on outdated
liquidity information.

Threshold (binding — pre-reg §5.5): orderbook_age_seconds > 2.

Reference: SA-9 §5.5 RO-04; spec-8-risk-overlay change.md.
"""

from __future__ import annotations

from hermes.domain.risk.state import MarketSnapshot, PortfolioState
from hermes.domain.risk.types import RiskAction, RiskDecision

__all__ = ["StaleBook"]

# Pre-reg §5.5 RO-04 — DO NOT change without updating the signed document.
_MAX_ORDERBOOK_AGE_SECONDS: float = 2.0


class StaleBook:
    """RO-04: HALT when orderbook age > 2 seconds.

    Stateless — all state flows through the inputs per the RiskOverlayPort contract.
    """

    def evaluate(
        self,
        portfolio_state: PortfolioState,
        market_snapshot: MarketSnapshot,
    ) -> RiskDecision:
        """Fire HALT when orderbook snapshot is older than 2 seconds.

        Args:
            portfolio_state: Not used by this rule; accepted to satisfy the Protocol.
            market_snapshot: Must contain ``orderbook_age_seconds`` (float, seconds).

        Returns:
            :class:`~hermes.ports.risk_overlay.RiskDecision` with HALT when
            the book is stale; OK otherwise.
        """
        age = market_snapshot.orderbook_age_seconds

        if age > _MAX_ORDERBOOK_AGE_SECONDS:
            return RiskDecision(
                action=RiskAction.HALT,
                reason=(
                    f"RO-04: stale orderbook — age={age:.3f}s > limit={_MAX_ORDERBOOK_AGE_SECONDS}s"
                ),
            )

        return RiskDecision(
            action=RiskAction.OK,
            reason=f"RO-04: orderbook fresh — age={age:.3f}s ≤ {_MAX_ORDERBOOK_AGE_SECONDS}s",
        )
