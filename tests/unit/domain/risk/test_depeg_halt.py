"""Tests for RO-06 DepegHalt rule.

Property tests verify the hard-threshold boundary at 3.0% deviation (pre-reg §5.5):
  - deviation < 3.0% → OK
  - deviation ≥ 3.0% → HALT
  - stable_price is None → OK (not a stablecoin pair)

Reference: SA-9 §5.5 RO-06; spec-8-risk-overlay change.md R-2.
"""

from __future__ import annotations

from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from hermes.domain.risk.depeg_halt import _HALT_THRESHOLD, DepegHalt
from hermes.ports.risk_overlay import RiskAction, RiskOverlayPort
from tests.unit.domain.risk.conftest import make_market_snapshot, make_portfolio_state

_RULE = DepegHalt()
_PEG = Decimal("1.00")


class TestDepegHaltThresholdConstants:
    """Verify the pre-reg §5.5 RO-06 constant is exactly 3.0%."""

    def test_halt_threshold_is_binding(self) -> None:
        """Halt threshold must be exactly 0.030 (3.0%) per pre-reg §5.5 RO-06."""
        assert Decimal("0.030") == _HALT_THRESHOLD, (
            f"RO-06 threshold must be exactly 0.030 (pre-reg §5.5), got {_HALT_THRESHOLD}"
        )


class TestDepegHaltNoStablePrice:
    """Rule skips evaluation when stable_price is None."""

    def test_none_stable_price_returns_ok(self) -> None:
        """stable_price=None means non-stablecoin pair — rule must return OK."""
        snapshot = make_market_snapshot(stable_price=None)
        decision = _RULE.evaluate(
            portfolio_state=make_portfolio_state(),
            market_snapshot=snapshot,
        )
        assert decision.action == RiskAction.OK, (
            f"stable_price=None must return OK (not a stablecoin), got {decision.action}"
        )


class TestDepegHaltBelowThreshold:
    """Price deviation < 3.0% returns OK."""

    @given(
        deviation=st.decimals(
            min_value=Decimal("0"),
            max_value=Decimal("0.0299"),
            allow_nan=False,
            allow_infinity=False,
        )
    )
    @settings(max_examples=50)
    def test_below_threshold_returns_ok(self, deviation: Decimal) -> None:
        """Stablecoin deviation < 3.0% must return OK from the halt rule."""
        price = _PEG + deviation
        snapshot = make_market_snapshot(stable_price=price)
        decision = _RULE.evaluate(
            portfolio_state=make_portfolio_state(),
            market_snapshot=snapshot,
        )
        actual_dev = abs(price - _PEG) / _PEG
        assert decision.action == RiskAction.OK, (
            f"price={price}, deviation={actual_dev:.4%} < {_HALT_THRESHOLD:.4%}:"
            f" expected OK, got {decision.action} — {decision.reason}"
        )


class TestDepegHaltAtOrAboveThreshold:
    """Price deviation ≥ 3.0% returns HALT."""

    @given(
        deviation=st.decimals(
            min_value=Decimal("0.030"),
            max_value=Decimal("0.20"),
            allow_nan=False,
            allow_infinity=False,
        )
    )
    @settings(max_examples=50)
    def test_at_or_above_threshold_returns_halt(self, deviation: Decimal) -> None:
        """Stablecoin deviation ≥ 3.0% must return HALT."""
        price = _PEG + deviation
        snapshot = make_market_snapshot(stable_price=price)
        decision = _RULE.evaluate(
            portfolio_state=make_portfolio_state(),
            market_snapshot=snapshot,
        )
        actual_dev = abs(price - _PEG) / _PEG
        assert decision.action == RiskAction.HALT, (
            f"price={price}, deviation={actual_dev:.4%} ≥ {_HALT_THRESHOLD:.4%}:"
            f" expected HALT, got {decision.action} — {decision.reason}"
        )

    def test_exactly_at_threshold_returns_halt(self) -> None:
        """Stablecoin deviation exactly at 3.0% must return HALT (boundary, inclusive)."""
        price = _PEG * (Decimal("1") + _HALT_THRESHOLD)
        snapshot = make_market_snapshot(stable_price=price)
        decision = _RULE.evaluate(
            portfolio_state=make_portfolio_state(),
            market_snapshot=snapshot,
        )
        assert decision.action == RiskAction.HALT, (
            f"Deviation exactly at 3.0% must return HALT, got {decision.action}"
        )

    def test_below_peg_deviation_triggers_halt(self) -> None:
        """Stablecoin price below peg by ≥ 3.0% must return HALT (absolute deviation)."""
        price = _PEG - Decimal("0.04")  # 4% below peg
        snapshot = make_market_snapshot(stable_price=price)
        decision = _RULE.evaluate(
            portfolio_state=make_portfolio_state(),
            market_snapshot=snapshot,
        )
        assert decision.action == RiskAction.HALT, (
            f"Price {price} (below peg by 4%) must return HALT, got {decision.action}"
        )


class TestDepegHaltProtocolConformance:
    """Rule instance satisfies RiskOverlayPort at runtime."""

    def test_is_risk_overlay_port(self) -> None:
        """DepegHalt must be an instance of RiskOverlayPort (runtime_checkable)."""
        assert isinstance(_RULE, RiskOverlayPort), "DepegHalt must satisfy RiskOverlayPort Protocol"
