"""Tests for RO-04 StaleBook rule.

Property tests verify the threshold boundary at 2 seconds (pre-reg §5.5):
  - orderbook age ≤ 2s → OK
  - orderbook age > 2s → HALT

Reference: SA-9 §5.5 RO-04; spec-8-risk-overlay change.md R-2.
"""

from hypothesis import given, settings
from hypothesis import strategies as st

from hermes.domain.risk.stale_book import _MAX_ORDERBOOK_AGE_SECONDS, StaleBook
from hermes.ports.risk_overlay import RiskAction, RiskOverlayPort
from tests.unit.domain.risk.conftest import make_market_snapshot, make_portfolio_state

_RULE = StaleBook()


class TestStaleBookThresholdConstants:
    """Verify the pre-reg §5.5 RO-04 constant is exactly 2.0 seconds."""

    def test_max_age_is_binding(self) -> None:
        """Max orderbook age must be exactly 2.0 seconds per pre-reg §5.5 RO-04."""
        assert _MAX_ORDERBOOK_AGE_SECONDS == 2.0, (
            f"RO-04 threshold must be exactly 2.0s (pre-reg §5.5), got {_MAX_ORDERBOOK_AGE_SECONDS}"
        )


class TestStaleBookFreshBook:
    """Orderbook age ≤ 2s returns OK."""

    @given(age=st.floats(min_value=0.0, max_value=2.0, allow_nan=False, allow_infinity=False))
    @settings(max_examples=50)
    def test_age_at_or_below_limit_returns_ok(self, age: float) -> None:
        """Orderbook age ≤ 2.0s must return OK."""
        snapshot = make_market_snapshot(orderbook_age_seconds=age)
        decision = _RULE.evaluate(
            portfolio_state=make_portfolio_state(),
            market_snapshot=snapshot,
        )
        assert decision.action == RiskAction.OK, (
            f"age={age}s ≤ {_MAX_ORDERBOOK_AGE_SECONDS}s:"
            f" expected OK, got {decision.action} — {decision.reason}"
        )


class TestStaleBookStaleBook:
    """Orderbook age > 2s returns HALT."""

    @given(age=st.floats(min_value=2.0001, max_value=3600.0, allow_nan=False, allow_infinity=False))
    @settings(max_examples=50)
    def test_age_above_limit_returns_halt(self, age: float) -> None:
        """Orderbook age > 2.0s must return HALT."""
        snapshot = make_market_snapshot(orderbook_age_seconds=age)
        decision = _RULE.evaluate(
            portfolio_state=make_portfolio_state(),
            market_snapshot=snapshot,
        )
        assert decision.action == RiskAction.HALT, (
            f"age={age}s > {_MAX_ORDERBOOK_AGE_SECONDS}s:"
            f" expected HALT, got {decision.action} — {decision.reason}"
        )

    def test_exactly_at_boundary_returns_ok(self) -> None:
        """Orderbook age exactly at 2.0s must return OK (exclusive upper bound)."""
        snapshot = make_market_snapshot(orderbook_age_seconds=2.0)
        decision = _RULE.evaluate(
            portfolio_state=make_portfolio_state(),
            market_snapshot=snapshot,
        )
        assert decision.action == RiskAction.OK, (
            f"Age exactly at 2.0s must return OK (exclusive), got {decision.action}"
        )

    def test_just_above_boundary_returns_halt(self) -> None:
        """Orderbook age just above 2.0s (e.g. 2.001s) must return HALT."""
        snapshot = make_market_snapshot(orderbook_age_seconds=2.001)
        decision = _RULE.evaluate(
            portfolio_state=make_portfolio_state(),
            market_snapshot=snapshot,
        )
        assert decision.action == RiskAction.HALT, (
            f"Age 2.001s must return HALT, got {decision.action}"
        )


class TestStaleBookProtocolConformance:
    """Rule instance satisfies RiskOverlayPort at runtime."""

    def test_is_risk_overlay_port(self) -> None:
        """StaleBook must be an instance of RiskOverlayPort (runtime_checkable)."""
        assert isinstance(_RULE, RiskOverlayPort), "StaleBook must satisfy RiskOverlayPort Protocol"
