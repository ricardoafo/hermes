"""Tests for RO-07 MacroATRHalt rule.

Property tests verify the threshold boundary at -10% BTC 1D log-return (pre-reg §5.5):
  - log_return ≥ -0.10 → OK
  - log_return < -0.10 → HALT
  - log_return is None → conservative HALT

Reference: SA-9 §5.5 RO-07; spec-8-risk-overlay change.md R-2.
"""

from hypothesis import given, settings
from hypothesis import strategies as st

from hermes.domain.risk.macro_atr_halt import _BTC_LOG_RETURN_LIMIT, MacroATRHalt
from hermes.ports.risk_overlay import RiskAction, RiskOverlayPort
from tests.unit.domain.risk.conftest import make_market_snapshot, make_portfolio_state

_RULE = MacroATRHalt()


class TestMacroATRHaltThresholdConstants:
    """Verify the pre-reg §5.5 RO-07 constant is exactly -10%."""

    def test_btc_log_return_limit_is_binding(self) -> None:
        """BTC log-return limit must be exactly -0.10 per pre-reg §5.5 RO-07."""
        assert _BTC_LOG_RETURN_LIMIT == -0.10, (
            f"RO-07 limit must be exactly -0.10 (pre-reg §5.5), got {_BTC_LOG_RETURN_LIMIT}"
        )


class TestMacroATRHaltNoData:
    """None log-return triggers conservative HALT."""

    def test_none_log_return_returns_halt(self) -> None:
        """BTC log-return=None (no data) must conservatively return HALT."""
        snapshot = make_market_snapshot(btc_log_return_1d=None)
        decision = _RULE.evaluate(
            portfolio_state=make_portfolio_state(),
            market_snapshot=snapshot,
        )
        assert decision.action == RiskAction.HALT, (
            f"btc_log_return_1d=None must return HALT (conservative), got {decision.action}"
        )


class TestMacroATRHaltAboveLimit:
    """Log-return ≥ -0.10 returns OK."""

    @given(
        log_return=st.floats(min_value=-0.10, max_value=1.0, allow_nan=False, allow_infinity=False)
    )
    @settings(max_examples=50)
    def test_at_or_above_limit_returns_ok(self, log_return: float) -> None:
        """BTC log-return ≥ -0.10 must return OK."""
        snapshot = make_market_snapshot(btc_log_return_1d=log_return)
        decision = _RULE.evaluate(
            portfolio_state=make_portfolio_state(),
            market_snapshot=snapshot,
        )
        assert decision.action == RiskAction.OK, (
            f"log_return={log_return:.4f} ≥ {_BTC_LOG_RETURN_LIMIT}:"
            f" expected OK, got {decision.action} — {decision.reason}"
        )

    def test_exactly_at_limit_returns_ok(self) -> None:
        """BTC log-return exactly at -0.10 must return OK (exclusive lower bound)."""
        snapshot = make_market_snapshot(btc_log_return_1d=-0.10)
        decision = _RULE.evaluate(
            portfolio_state=make_portfolio_state(),
            market_snapshot=snapshot,
        )
        assert decision.action == RiskAction.OK, (
            f"log_return=-0.10 (exactly at limit) must return OK (exclusive), got {decision.action}"
        )


class TestMacroATRHaltBelowLimit:
    """Log-return < -0.10 returns HALT."""

    @given(
        log_return=st.floats(
            min_value=-1.0, max_value=-0.1001, allow_nan=False, allow_infinity=False
        )
    )
    @settings(max_examples=50)
    def test_below_limit_returns_halt(self, log_return: float) -> None:
        """BTC log-return < -0.10 must return HALT."""
        snapshot = make_market_snapshot(btc_log_return_1d=log_return)
        decision = _RULE.evaluate(
            portfolio_state=make_portfolio_state(),
            market_snapshot=snapshot,
        )
        assert decision.action == RiskAction.HALT, (
            f"log_return={log_return:.4f} < {_BTC_LOG_RETURN_LIMIT}:"
            f" expected HALT, got {decision.action} — {decision.reason}"
        )

    def test_just_below_limit_returns_halt(self) -> None:
        """BTC log-return just below -0.10 (e.g. -0.101) must return HALT."""
        snapshot = make_market_snapshot(btc_log_return_1d=-0.101)
        decision = _RULE.evaluate(
            portfolio_state=make_portfolio_state(),
            market_snapshot=snapshot,
        )
        assert decision.action == RiskAction.HALT, (
            f"log_return=-0.101 (just below limit) must return HALT, got {decision.action}"
        )


class TestMacroATRHaltProtocolConformance:
    """Rule instance satisfies RiskOverlayPort at runtime."""

    def test_is_risk_overlay_port(self) -> None:
        """MacroATRHalt must be an instance of RiskOverlayPort (runtime_checkable)."""
        assert isinstance(_RULE, RiskOverlayPort), (
            "MacroATRHalt must satisfy RiskOverlayPort Protocol"
        )
