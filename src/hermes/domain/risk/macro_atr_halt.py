"""RO-07 MacroATRHalt — halts all new entries after a severe BTC daily drop.

When BTC's 1-day log-return falls below -10%, macro volatility is considered
systemic and all new position entries are blocked for 24 hours. The 24h
timer is managed by the caller (this rule evaluates whether the condition
is currently active based on the snapshot value).

If ``btc_log_return_1d`` is ``None`` (data unavailable), the rule
conservatively returns HALT.

Threshold (binding — pre-reg §5.5): log-return < -0.10 → HALT.

Reference: SA-9 §5.5 RO-07; spec-8-risk-overlay change.md.
"""

from __future__ import annotations

from hermes.domain.risk.state import MarketSnapshot, PortfolioState
from hermes.domain.risk.types import RiskAction, RiskDecision

__all__ = ["MacroATRHalt"]

# Pre-reg §5.5 RO-07 — DO NOT change without updating the signed document.
_BTC_LOG_RETURN_LIMIT: float = -0.10  # -10%


class MacroATRHalt:
    """RO-07: HALT when BTC 1D log-return < -10%.

    Stateless — all state flows through the inputs per the RiskOverlayPort contract.
    The 24h cooldown window is enforced by the caller (OrderRouter / Spec 3),
    not by this rule.
    """

    def evaluate(
        self,
        portfolio_state: PortfolioState,
        market_snapshot: MarketSnapshot,
    ) -> RiskDecision:
        """Fire HALT when BTC daily log-return is below -10%.

        Conservatively halts when ``btc_log_return_1d`` is ``None``.

        Args:
            portfolio_state: Not used by this rule; accepted to satisfy the Protocol.
            market_snapshot: ``btc_log_return_1d`` field — BTC 1-day log-return
                as a float (e.g. ``-0.12`` for -12%). ``None`` means no data.

        Returns:
            :class:`~hermes.ports.risk_overlay.RiskDecision` with HALT when
            macro ATR threshold is crossed or data is absent; OK otherwise.
        """
        log_return = market_snapshot.btc_log_return_1d

        if log_return is None:
            return RiskDecision(
                action=RiskAction.HALT,
                reason="RO-07: BTC 1D log-return unavailable — halting conservatively",
            )

        if log_return < _BTC_LOG_RETURN_LIMIT:
            return RiskDecision(
                action=RiskAction.HALT,
                reason=(
                    f"RO-07: macro ATR halt — BTC 1D log-return={log_return:.4f}"
                    f" < limit={_BTC_LOG_RETURN_LIMIT}"
                ),
            )

        return RiskDecision(
            action=RiskAction.OK,
            reason=(
                f"RO-07: macro OK — BTC 1D log-return={log_return:.4f} ≥ {_BTC_LOG_RETURN_LIMIT}"
            ),
        )
