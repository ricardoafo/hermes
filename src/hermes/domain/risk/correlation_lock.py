"""RO-01 CorrelationLock — blocks a third entry into a correlated cluster.

Pre-registration §5.5 (signed): "if 30-day rolling correlation between two
open pairs exceeds 0.80, block new entry in the third correlated pair
(existing positions unaffected)". So one or two correlated positions are
allowed; the rule fires only when two held pairs already correlate above 0.80
and the candidate correlates above 0.80 with both of them. An earlier version
blocked any entry correlated with any one held pair, which already refused the
second position and is not the signed rule.

Threshold (binding — pre-reg §5.5): correlation strictly above 0.80.

Reference: pre-registration-v1.md §5.5; SA-9 RO-01;
openspec/archive/822-portfolio-backtest D-2.
"""

from __future__ import annotations

from collections.abc import Mapping
from itertools import combinations

from hermes.domain.risk.state import MarketSnapshot, PortfolioState
from hermes.domain.risk.types import RiskAction, RiskDecision
from hermes.domain.value_objects.pair import Pair

__all__ = ["CorrelationLock"]

# Pre-reg §5.5 RO-01 — DO NOT change without updating the signed document.
_CORRELATION_THRESHOLD: float = 0.80


class CorrelationLock:
    """RO-01: Block a new entry that would be the third pair of a correlated cluster.

    Stateless — all state flows through the inputs per the RiskOverlayPort contract.
    """

    def evaluate(
        self,
        portfolio_state: PortfolioState,
        market_snapshot: MarketSnapshot,
    ) -> RiskDecision:
        """Fire HALT when two held pairs and the candidate all correlate above 0.80.

        A pair missing from ``correlations`` counts as uncorrelated. When no
        candidate pair is specified in the snapshot the rule returns OK:
        correlation only matters when a new entry is being considered.

        Args:
            portfolio_state: ``held_pairs`` names the open pairs and
                ``correlations`` their 30-day coefficients, keyed in either order.
            market_snapshot: ``candidate_pair`` is the pair the strategy
                intends to enter. If ``None``, the rule is skipped (OK).

        Returns:
            :class:`~hermes.domain.risk.types.RiskDecision` with HALT when the
            candidate would be the third correlated pair; OK otherwise.
        """
        candidate = market_snapshot.candidate_pair
        if candidate is None:
            return RiskDecision(
                action=RiskAction.OK,
                reason="RO-01: no candidate pair — correlation check skipped",
            )

        corr = portfolio_state.correlations
        held = sorted(portfolio_state.held_pairs - {candidate}, key=str)
        for first, second in combinations(held, 2):
            if all(
                _coefficient(corr, a, b) > _CORRELATION_THRESHOLD
                for a, b in ((first, second), (candidate, first), (candidate, second))
            ):
                return RiskDecision(
                    action=RiskAction.HALT,
                    reason=(
                        f"RO-01: candidate {candidate} would be the third pair correlated"
                        f" above {_CORRELATION_THRESHOLD} with open {first} and {second}"
                    ),
                )

        return RiskDecision(
            action=RiskAction.OK,
            reason=f"RO-01: {candidate} is not a third pair correlated above"
            f" {_CORRELATION_THRESHOLD}",
        )


def _coefficient(correlations: Mapping[tuple[Pair, Pair], float], a: Pair, b: Pair) -> float:
    """Return the coefficient of ``a`` and ``b`` in either key order, 0 when absent."""
    return correlations.get((a, b), correlations.get((b, a), 0.0))
