"""Tests for RO-02 SpreadSpike rule.

Property tests verify the threshold boundary at 10x 1H median (pre-reg §5.5):
  - current_spread < 10x median → OK
  - current_spread ≥ 10x median → HALT
  - zero median → conservative HALT

Reference: SA-9 §5.5 RO-02; spec-8-risk-overlay change.md R-2.
"""

from __future__ import annotations

from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from hermes.domain.risk.spread_spike import SpreadSpike
from hermes.ports.risk_overlay import RiskAction
from tests.unit.domain.risk.conftest import make_market_snapshot, make_portfolio_state

_RULE = SpreadSpike()


class TestSpreadSpikeBelowThreshold:
    """Spreads below 10x median return OK."""

    @given(
        median=st.decimals(
            min_value=Decimal("0.0001"),
            max_value=Decimal("0.01"),
            allow_nan=False,
            allow_infinity=False,
        ),
        factor=st.decimals(
            min_value=Decimal("0.01"),
            max_value=Decimal("9.9999"),
            allow_nan=False,
            allow_infinity=False,
        ),
    )
    @settings(max_examples=50)
    def test_below_10x_median_returns_ok(self, median: Decimal, factor: Decimal) -> None:
        """Current spread below 10x median must return OK."""
        current = median * factor
        snapshot = make_market_snapshot(
            current_spread=current,
            median_spread_1h=median,
        )
        decision = _RULE.evaluate(
            portfolio_state=make_portfolio_state(),
            market_snapshot=snapshot,
        )
        assert decision.action == RiskAction.OK, (
            f"current={current}, median={median}, factor={factor}:"
            f" expected OK (< 10x), got {decision.action} — {decision.reason}"
        )


class TestSpreadSpikeAtOrAboveThreshold:
    """Spreads at or above 10x median return HALT."""

    @given(
        median=st.decimals(
            min_value=Decimal("0.0001"),
            max_value=Decimal("0.01"),
            allow_nan=False,
            allow_infinity=False,
        ),
        factor=st.decimals(
            min_value=Decimal("10"), max_value=Decimal("100"), allow_nan=False, allow_infinity=False
        ),
    )
    @settings(max_examples=50)
    def test_at_or_above_10x_median_returns_halt(self, median: Decimal, factor: Decimal) -> None:
        """Current spread ≥ 10x median must return HALT."""
        current = median * factor
        snapshot = make_market_snapshot(
            current_spread=current,
            median_spread_1h=median,
        )
        decision = _RULE.evaluate(
            portfolio_state=make_portfolio_state(),
            market_snapshot=snapshot,
        )
        assert decision.action == RiskAction.HALT, (
            f"current={current}, median={median}, factor={factor}:"
            f" expected HALT (≥ 10x), got {decision.action} — {decision.reason}"
        )

    def test_exactly_at_10x_returns_halt(self) -> None:
        """Spread exactly at 10x median must return HALT (boundary, inclusive)."""
        median = Decimal("0.001")
        snapshot = make_market_snapshot(
            current_spread=median * Decimal("10"),
            median_spread_1h=median,
        )
        decision = _RULE.evaluate(
            portfolio_state=make_portfolio_state(),
            market_snapshot=snapshot,
        )
        assert decision.action == RiskAction.HALT, (
            f"Spread at exactly 10x median must HALT, got {decision.action}"
        )


class TestSpreadSpikeZeroMedian:
    """Zero median spread triggers conservative HALT."""

    def test_zero_median_returns_halt(self) -> None:
        """Zero 1H median spread (no data) must conservatively return HALT."""
        snapshot = make_market_snapshot(
            current_spread=Decimal("0.001"),
            median_spread_1h=Decimal("0"),
        )
        decision = _RULE.evaluate(
            portfolio_state=make_portfolio_state(),
            market_snapshot=snapshot,
        )
        assert decision.action == RiskAction.HALT, (
            f"Zero median spread must return HALT (conservative), got {decision.action}"
        )
