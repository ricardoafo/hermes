"""RO-03 MaxSlippage — halts when realized slippage exceeds limits.

Two checks:
  - Single fill: realized_slippage_last > 0.5% → HALT
  - Cumulative: realized_slippage_cumulative > 2% → HALT

Both checks must pass (OK) for the rule to return OK.

Thresholds (binding — pre-reg §5.5):
  single > 0.005 (0.5%), cumulative > 0.02 (2%).

Reference: SA-9 §5.5 RO-03; spec-8-risk-overlay change.md.
"""

from __future__ import annotations

from decimal import Decimal

from hermes.domain.risk.state import MarketSnapshot, PortfolioState
from hermes.domain.risk.types import RiskAction, RiskDecision

__all__ = ["MaxSlippage"]

# Pre-reg §5.5 RO-03 — DO NOT change without updating the signed document.
_SINGLE_SLIPPAGE_LIMIT: Decimal = Decimal("0.005")  # 0.5%
_CUMULATIVE_SLIPPAGE_LIMIT: Decimal = Decimal("0.02")  # 2.0%


class MaxSlippage:
    """RO-03: HALT when single fill or cumulative slippage exceeds limits.

    Stateless — all state flows through the inputs per the RiskOverlayPort contract.
    """

    def evaluate(
        self,
        portfolio_state: PortfolioState,
        market_snapshot: MarketSnapshot,
    ) -> RiskDecision:
        """Fire HALT when any slippage threshold is exceeded.

        Single-fill check uses ``market_snapshot.realized_slippage_last``.
        Cumulative check uses ``portfolio_state.realized_slippage_cumulative``.

        Args:
            portfolio_state: Must contain ``realized_slippage_cumulative``
                as a Decimal fraction (e.g. ``Decimal("0.015")`` for 1.5%).
            market_snapshot: Must contain ``realized_slippage_last``
                as a Decimal fraction.

        Returns:
            :class:`~hermes.ports.risk_overlay.RiskDecision` with HALT if
            either threshold is exceeded; OK otherwise.
        """
        single = market_snapshot.realized_slippage_last
        cumulative = portfolio_state.realized_slippage_cumulative

        if single > _SINGLE_SLIPPAGE_LIMIT:
            return RiskDecision(
                action=RiskAction.HALT,
                reason=(
                    f"RO-03: single-fill slippage {single:.4%} > limit {_SINGLE_SLIPPAGE_LIMIT:.4%}"
                ),
            )

        if cumulative > _CUMULATIVE_SLIPPAGE_LIMIT:
            return RiskDecision(
                action=RiskAction.HALT,
                reason=(
                    f"RO-03: cumulative slippage {cumulative:.4%}"
                    f" > limit {_CUMULATIVE_SLIPPAGE_LIMIT:.4%}"
                ),
            )

        return RiskDecision(
            action=RiskAction.OK,
            reason=(
                f"RO-03: slippage OK — single={single:.4%} ≤"
                f" {_SINGLE_SLIPPAGE_LIMIT:.4%},"
                f" cumulative={cumulative:.4%} ≤ {_CUMULATIVE_SLIPPAGE_LIMIT:.4%}"
            ),
        )
