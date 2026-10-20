"""Tests for RO-03 MaxSlippage rule.

Property tests verify both threshold boundaries (pre-reg §5.5):
  - single fill slippage > 0.5% → HALT
  - cumulative slippage > 2.0% → HALT
  - both within limits → OK

Reference: SA-9 §5.5 RO-03; spec-8-risk-overlay change.md R-2.
"""

from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from hermes.domain.risk.max_slippage import (
    MaxSlippage,
)
from hermes.ports.risk_overlay import RiskAction
from tests.unit.domain.risk.conftest import make_market_snapshot, make_portfolio_state

_RULE = MaxSlippage()


class TestMaxSlippageBothOK:
    """When both checks pass, rule returns OK."""

    @given(
        single=st.decimals(
            min_value=Decimal("0"),
            max_value=Decimal("0.005"),
            allow_nan=False,
            allow_infinity=False,
        ),
        cumulative=st.decimals(
            min_value=Decimal("0"), max_value=Decimal("0.02"), allow_nan=False, allow_infinity=False
        ),
    )
    @settings(max_examples=50)
    def test_within_both_limits_returns_ok(self, single: Decimal, cumulative: Decimal) -> None:
        """Both slippage checks within limits must return OK."""
        snapshot = make_market_snapshot(realized_slippage_last=single)
        state = make_portfolio_state(realized_slippage_cumulative=cumulative)
        decision = _RULE.evaluate(portfolio_state=state, market_snapshot=snapshot)
        assert decision.action == RiskAction.OK, (
            f"single={single}, cumulative={cumulative}:"
            f" expected OK (both within limits), got {decision.action} — {decision.reason}"
        )


class TestMaxSlippageSingleExceeded:
    """Single fill slippage above 0.5% triggers HALT."""

    @given(
        single=st.decimals(
            min_value=Decimal("0.005001"),
            max_value=Decimal("0.10"),
            allow_nan=False,
            allow_infinity=False,
        )
    )
    @settings(max_examples=50)
    def test_single_above_limit_returns_halt(self, single: Decimal) -> None:
        """Single fill slippage > 0.5% must return HALT."""
        snapshot = make_market_snapshot(realized_slippage_last=single)
        state = make_portfolio_state(realized_slippage_cumulative=Decimal("0"))
        decision = _RULE.evaluate(portfolio_state=state, market_snapshot=snapshot)
        assert decision.action == RiskAction.HALT, (
            f"single={single} > 0.005: expected HALT, got {decision.action} — {decision.reason}"
        )

    def test_exactly_at_single_limit_returns_ok(self) -> None:
        """Single fill slippage exactly at 0.5% returns OK (exclusive boundary)."""
        snapshot = make_market_snapshot(realized_slippage_last=Decimal("0.005"))
        state = make_portfolio_state(realized_slippage_cumulative=Decimal("0"))
        decision = _RULE.evaluate(portfolio_state=state, market_snapshot=snapshot)
        assert decision.action == RiskAction.OK, (
            "Single slippage exactly at limit 0.005 must return OK (exclusive),"
            f" got {decision.action}"
        )


class TestMaxSlippageCumulativeExceeded:
    """Cumulative slippage above 2% triggers HALT."""

    @given(
        cumulative=st.decimals(
            min_value=Decimal("0.02001"),
            max_value=Decimal("0.10"),
            allow_nan=False,
            allow_infinity=False,
        )
    )
    @settings(max_examples=50)
    def test_cumulative_above_limit_returns_halt(self, cumulative: Decimal) -> None:
        """Cumulative slippage > 2.0% must return HALT."""
        snapshot = make_market_snapshot(realized_slippage_last=Decimal("0"))
        state = make_portfolio_state(realized_slippage_cumulative=cumulative)
        decision = _RULE.evaluate(portfolio_state=state, market_snapshot=snapshot)
        assert decision.action == RiskAction.HALT, (
            f"cumulative={cumulative} > 0.02: expected HALT,"
            f" got {decision.action} — {decision.reason}"
        )

    def test_exactly_at_cumulative_limit_returns_ok(self) -> None:
        """Cumulative slippage exactly at 2.0% returns OK (exclusive boundary)."""
        snapshot = make_market_snapshot(realized_slippage_last=Decimal("0"))
        state = make_portfolio_state(realized_slippage_cumulative=Decimal("0.02"))
        decision = _RULE.evaluate(portfolio_state=state, market_snapshot=snapshot)
        assert decision.action == RiskAction.OK, (
            "Cumulative slippage exactly at limit 0.02 must return OK (exclusive),"
            f" got {decision.action}"
        )
