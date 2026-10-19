"""Tests for RO-08 PortfolioDrawdown rule.

Property tests verify the threshold boundary at 15% drawdown (pre-reg §10):
  - drawdown < 15% and dd_halt_required=False → OK
  - drawdown ≥ 15% → HALT
  - dd_halt_required=True → HALT regardless of drawdown

Reference: SA-9 §5.5 RO-08; pre-reg §10; spec-8-risk-overlay change.md R-2.
"""

from __future__ import annotations

from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from hermes.domain.risk.portfolio_drawdown import _DRAWDOWN_HALT_THRESHOLD, PortfolioDrawdown
from hermes.ports.risk_overlay import RiskAction
from tests.unit.domain.risk.conftest import make_market_snapshot, make_portfolio_state

_RULE = PortfolioDrawdown()
_PEAK = Decimal("10000")


class TestPortfolioDrawdownOK:
    """Drawdown below threshold with halt flag False returns OK."""

    @given(
        drawdown_fraction=st.decimals(
            min_value=Decimal("0"),
            max_value=Decimal("0.1499"),
            allow_nan=False,
            allow_infinity=False,
        )
    )
    @settings(max_examples=50)
    def test_below_threshold_returns_ok(self, drawdown_fraction: Decimal) -> None:
        """Portfolio drawdown < 15% with halt flag False must return OK."""
        current = _PEAK * (Decimal("1") - drawdown_fraction)
        state = make_portfolio_state(
            peak_value=_PEAK,
            current_value=current,
            dd_halt_required=False,
        )
        decision = _RULE.evaluate(
            portfolio_state=state,
            market_snapshot=make_market_snapshot(),
        )
        assert decision.action == RiskAction.OK, (
            f"drawdown={drawdown_fraction:.4%} < {_DRAWDOWN_HALT_THRESHOLD:.4%}:"
            f" expected OK, got {decision.action} — {decision.reason}"
        )


class TestPortfolioDrawdownHalt:
    """Drawdown at or above threshold returns HALT."""

    @given(
        drawdown_fraction=st.decimals(
            min_value=Decimal("0.15"),
            max_value=Decimal("1.0"),
            allow_nan=False,
            allow_infinity=False,
        )
    )
    @settings(max_examples=50)
    def test_at_or_above_threshold_returns_halt(self, drawdown_fraction: Decimal) -> None:
        """Portfolio drawdown ≥ 15% must return HALT."""
        current = _PEAK * (Decimal("1") - drawdown_fraction)
        # Clamp current to 0 to avoid negative portfolio values in edge cases.
        current = max(Decimal("0"), current)
        state = make_portfolio_state(
            peak_value=_PEAK,
            current_value=current,
            dd_halt_required=False,
        )
        decision = _RULE.evaluate(
            portfolio_state=state,
            market_snapshot=make_market_snapshot(),
        )
        assert decision.action == RiskAction.HALT, (
            f"drawdown={drawdown_fraction:.4%} ≥ {_DRAWDOWN_HALT_THRESHOLD:.4%}:"
            f" expected HALT, got {decision.action} — {decision.reason}"
        )

    def test_exactly_at_threshold_returns_halt(self) -> None:
        """Portfolio drawdown exactly at 15% must return HALT (boundary, inclusive)."""
        current = _PEAK * Decimal("0.85")  # 15% drawdown
        state = make_portfolio_state(peak_value=_PEAK, current_value=current)
        decision = _RULE.evaluate(
            portfolio_state=state,
            market_snapshot=make_market_snapshot(),
        )
        assert decision.action == RiskAction.HALT, (
            f"Drawdown exactly at 15% must return HALT, got {decision.action}"
        )


class TestPortfolioDrawdownHaltFlag:
    """dd_halt_required=True triggers HALT regardless of drawdown level."""

    def test_halt_flag_true_with_small_drawdown_returns_halt(self) -> None:
        """dd_halt_required=True must return HALT even when drawdown is small."""
        state = make_portfolio_state(
            peak_value=_PEAK,
            current_value=_PEAK * Decimal("0.99"),  # only 1% drawdown
            dd_halt_required=True,
        )
        decision = _RULE.evaluate(
            portfolio_state=state,
            market_snapshot=make_market_snapshot(),
        )
        assert decision.action == RiskAction.HALT, (
            f"dd_halt_required=True must return HALT even with 1% drawdown, got {decision.action}"
        )

    def test_halt_flag_false_with_small_drawdown_returns_ok(self) -> None:
        """dd_halt_required=False with small drawdown must return OK."""
        state = make_portfolio_state(
            peak_value=_PEAK,
            current_value=_PEAK * Decimal("0.99"),
            dd_halt_required=False,
        )
        decision = _RULE.evaluate(
            portfolio_state=state,
            market_snapshot=make_market_snapshot(),
        )
        assert decision.action == RiskAction.OK, (
            f"dd_halt_required=False with 1% drawdown must return OK, got {decision.action}"
        )
