"""RO-06 DepegHalt — halts when stablecoin deviates severely from peg (hard threshold).

When ``|stable_price - $1.00| / $1.00 ≥ 3.0%`` the rule returns HALT.
This is the hard threshold; the soft warning lives in RO-05 (depeg_warn.py).

If the snapshot has no stablecoin price (``stable_price is None``), the
rule returns OK — it is not a stablecoin pair.

Threshold (binding — pre-reg §5.5): deviation ≥ 0.030 → HALT.

Reference: SA-9 §5.5 RO-06; spec-8-risk-overlay change.md.
"""

from decimal import Decimal

from hermes.domain.risk.state import MarketSnapshot, PortfolioState
from hermes.domain.risk.types import RiskAction, RiskDecision

__all__ = ["DepegHalt"]

_PEG: Decimal = Decimal("1.00")

# Pre-reg §5.5 RO-06 — DO NOT change without updating the signed document.
_HALT_THRESHOLD: Decimal = Decimal("0.030")  # 3.0%


class DepegHalt:
    """RO-06: HALT when stablecoin deviates ≥ 3.0% from $1 peg.

    Stateless — all state flows through the inputs per the RiskOverlayPort contract.
    """

    def evaluate(
        self,
        portfolio_state: PortfolioState,
        market_snapshot: MarketSnapshot,
    ) -> RiskDecision:
        """Fire HALT when stablecoin deviation ≥ 3.0%.

        Skips evaluation (OK) when ``stable_price`` is ``None``.

        Args:
            portfolio_state: Not used by this rule; accepted to satisfy the Protocol.
            market_snapshot: ``stable_price`` field — current stablecoin price
                in USD, or ``None`` if not applicable.

        Returns:
            :class:`~hermes.ports.risk_overlay.RiskDecision` with HALT when
            the hard depeg threshold is crossed; OK otherwise.
        """
        if market_snapshot.stable_price is None:
            return RiskDecision(
                action=RiskAction.OK,
                reason="RO-06: not a stablecoin pair — depeg halt skipped",
            )

        price = market_snapshot.stable_price
        deviation = abs(price - _PEG) / _PEG

        if deviation >= _HALT_THRESHOLD:
            return RiskDecision(
                action=RiskAction.HALT,
                reason=(
                    f"RO-06: stablecoin depeg HALT — price={price}"
                    f" deviation={deviation:.4%} ≥ {_HALT_THRESHOLD:.4%}"
                ),
            )

        return RiskDecision(
            action=RiskAction.OK,
            reason=(
                f"RO-06: stablecoin peg OK — price={price}"
                f" deviation={deviation:.4%} < {_HALT_THRESHOLD:.4%}"
            ),
        )
