"""RO-05 DepegWarn — warns when stablecoin deviates from peg (soft threshold).

When ``|stable_price - $1.00| / $1.00 ≥ 1.5%`` the rule returns WARN.
This is the soft threshold; the hard halt lives in RO-06 (depeg_halt.py).

If the snapshot has no stablecoin price (``stable_price is None``), the
rule returns OK — it is not a stablecoin pair.

Threshold (binding — pre-reg §5.5): deviation ≥ 0.015 → WARN.

Reference: SA-9 §5.5 RO-05; spec-8-risk-overlay change.md.
"""

from __future__ import annotations

from decimal import Decimal

from hermes.domain.risk.state import MarketSnapshot, PortfolioState
from hermes.domain.risk.types import RiskAction, RiskDecision

__all__ = ["DepegWarn"]

_PEG: Decimal = Decimal("1.00")

# Pre-reg §5.5 RO-05 — DO NOT change without updating the signed document.
_WARN_THRESHOLD: Decimal = Decimal("0.015")  # 1.5%


class DepegWarn:
    """RO-05: WARN when stablecoin deviates ≥ 1.5% from $1 peg.

    Stateless — all state flows through the inputs per the RiskOverlayPort contract.
    """

    def evaluate(
        self,
        portfolio_state: PortfolioState,
        market_snapshot: MarketSnapshot,
    ) -> RiskDecision:
        """Fire WARN when stablecoin deviation ≥ 1.5%.

        Skips evaluation (OK) when ``stable_price`` is ``None``.

        Args:
            portfolio_state: Not used by this rule; accepted to satisfy the Protocol.
            market_snapshot: ``stable_price`` field — current stablecoin price
                in USD, or ``None`` if not applicable.

        Returns:
            :class:`~hermes.ports.risk_overlay.RiskDecision` with WARN when
            the soft depeg threshold is crossed; OK otherwise.
        """
        if market_snapshot.stable_price is None:
            return RiskDecision(
                action=RiskAction.OK,
                reason="RO-05: not a stablecoin pair — depeg warn skipped",
            )

        price = market_snapshot.stable_price
        deviation = abs(price - _PEG) / _PEG

        if deviation >= _WARN_THRESHOLD:
            return RiskDecision(
                action=RiskAction.WARN,
                reason=(
                    f"RO-05: stablecoin depeg warning — price={price}"
                    f" deviation={deviation:.4%} ≥ {_WARN_THRESHOLD:.4%}"
                ),
            )

        return RiskDecision(
            action=RiskAction.OK,
            reason=(
                f"RO-05: stablecoin peg OK — price={price}"
                f" deviation={deviation:.4%} < {_WARN_THRESHOLD:.4%}"
            ),
        )
