"""RO-01 CorrelationLock — blocks new entries in highly correlated pairs.

If the 30-day rolling Pearson correlation between the candidate pair and
any existing position's pair is ≥ 0.80, the rule returns HALT. This
prevents doubling exposure through correlated instruments.

Threshold (binding — pre-reg §5.5): correlation ≥ 0.80.

Reference: SA-9 §5.5 RO-01; spec-8-risk-overlay change.md.
"""

from __future__ import annotations

from hermes.domain.risk.state import MarketSnapshot, PortfolioState
from hermes.domain.risk.types import RiskAction, RiskDecision

__all__ = ["CorrelationLock"]

# Pre-reg §5.5 RO-01 — DO NOT change without updating the signed document.
_CORRELATION_THRESHOLD: float = 0.80


class CorrelationLock:
    """RO-01: Block new entry when candidate pair is highly correlated with any position.

    Stateless — all state flows through the inputs per the RiskOverlayPort contract.
    """

    def evaluate(
        self,
        portfolio_state: PortfolioState,
        market_snapshot: MarketSnapshot,
    ) -> RiskDecision:
        """Fire HALT when candidate pair correlation ≥ 0.80 with any held pair.

        When no candidate pair is specified in the snapshot, the rule returns OK —
        correlation is only meaningful when a new entry is being considered.

        Args:
            portfolio_state: Must contain ``correlations`` dict with any
                relevant pair-pair coefficients.
            market_snapshot: ``candidate_pair`` field indicates the pair the
                strategy intends to enter. If ``None``, rule is skipped (OK).

        Returns:
            :class:`~hermes.ports.risk_overlay.RiskDecision` with HALT when
            a correlation ≥ threshold is found; OK otherwise.
        """
        candidate = market_snapshot.candidate_pair
        if candidate is None:
            return RiskDecision(
                action=RiskAction.OK,
                reason="RO-01: no candidate pair — correlation check skipped",
            )

        for (pair_a, pair_b), coeff in portfolio_state.correlations.items():
            if candidate not in (pair_a, pair_b):
                continue
            other = pair_b if pair_a == candidate else pair_a
            if coeff >= _CORRELATION_THRESHOLD:
                return RiskDecision(
                    action=RiskAction.HALT,
                    reason=(
                        f"RO-01: candidate {candidate} is correlated with position"
                        f" {other} (coeff={coeff:.4f} ≥ {_CORRELATION_THRESHOLD})"
                    ),
                )

        return RiskDecision(
            action=RiskAction.OK,
            reason=f"RO-01: no correlation ≥ {_CORRELATION_THRESHOLD} found for {candidate}",
        )
