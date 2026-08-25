"""Tests for RO-01 CorrelationLock rule.

Property tests verify the threshold boundary at 0.80 (pre-reg §5.5):
  - correlation < 0.80 → OK
  - correlation ≥ 0.80 → HALT
  - no candidate pair → OK (rule skipped)
  - candidate pair not in correlations → OK (uncorrelated)

Reference: SA-9 §5.5 RO-01; spec-8-risk-overlay change.md R-2.
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from hermes.domain.risk.correlation_lock import _CORRELATION_THRESHOLD, CorrelationLock
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair
from hermes.ports.risk_overlay import RiskAction, RiskOverlayPort
from tests.unit.domain.risk.conftest import make_market_snapshot, make_portfolio_state

_RULE = CorrelationLock()

_BTC_USDT = Pair(base=Currency.BTC, quote=Currency.USDT)
_ETH_USDT = Pair(base=Currency.ETH, quote=Currency.USDT)


class TestCorrelationLockThresholdConstants:
    """Verify the pre-reg §5.5 RO-01 constant is exactly 0.80."""

    def test_correlation_threshold_is_binding(self) -> None:
        """Correlation threshold must be exactly 0.80 per pre-reg §5.5 RO-01."""
        assert _CORRELATION_THRESHOLD == 0.80, (
            f"RO-01 threshold must be exactly 0.80 (pre-reg §5.5), got {_CORRELATION_THRESHOLD}"
        )


class TestCorrelationLockNoCandidatePair:
    """Rule returns OK when no candidate pair is specified."""

    def test_no_candidate_pair_returns_ok(self) -> None:
        """Rule skips evaluation and returns OK when candidate_pair is None."""
        decision = _RULE.evaluate(
            portfolio_state=make_portfolio_state(),
            market_snapshot=make_market_snapshot(candidate_pair=None),
        )
        assert decision.action == RiskAction.OK, (
            f"Expected OK when candidate_pair=None, got {decision.action}: {decision.reason}"
        )


class TestCorrelationLockBelowThreshold:
    """Correlations below 0.80 return OK."""

    @given(coeff=st.floats(min_value=0.0, max_value=0.7999, allow_nan=False))
    @settings(max_examples=50)
    def test_below_threshold_returns_ok(self, coeff: float) -> None:
        """Correlation below 0.80 must return OK — entry is permitted."""
        state = make_portfolio_state(correlations={(_BTC_USDT, _ETH_USDT): coeff})
        snapshot = make_market_snapshot(candidate_pair=_BTC_USDT)
        decision = _RULE.evaluate(portfolio_state=state, market_snapshot=snapshot)
        assert decision.action == RiskAction.OK, (
            f"coeff={coeff:.4f} < threshold={_CORRELATION_THRESHOLD}:"
            f" expected OK, got {decision.action} — {decision.reason}"
        )


class TestCorrelationLockAtOrAboveThreshold:
    """Correlations at or above 0.80 return HALT."""

    @given(coeff=st.floats(min_value=0.80, max_value=1.0, allow_nan=False))
    @settings(max_examples=50)
    def test_at_or_above_threshold_returns_halt(self, coeff: float) -> None:
        """Correlation ≥ 0.80 must return HALT — new entry is blocked."""
        state = make_portfolio_state(correlations={(_BTC_USDT, _ETH_USDT): coeff})
        snapshot = make_market_snapshot(candidate_pair=_BTC_USDT)
        decision = _RULE.evaluate(portfolio_state=state, market_snapshot=snapshot)
        assert decision.action == RiskAction.HALT, (
            f"coeff={coeff:.4f} ≥ threshold={_CORRELATION_THRESHOLD}:"
            f" expected HALT, got {decision.action} — {decision.reason}"
        )

    def test_exactly_at_threshold_returns_halt(self) -> None:
        """Correlation exactly at 0.80 must return HALT (boundary, inclusive)."""
        state = make_portfolio_state(correlations={(_BTC_USDT, _ETH_USDT): 0.80})
        snapshot = make_market_snapshot(candidate_pair=_BTC_USDT)
        decision = _RULE.evaluate(portfolio_state=state, market_snapshot=snapshot)
        assert decision.action == RiskAction.HALT, (
            f"Correlation exactly at threshold 0.80 must HALT, got {decision.action}"
        )


class TestCorrelationLockCandidateNotInCorrelations:
    """Candidate pair absent from correlations dict returns OK."""

    def test_uncorrelated_candidate_returns_ok(self) -> None:
        """Candidate pair not in correlations dict is treated as uncorrelated — OK."""
        state = make_portfolio_state(correlations={})  # empty matrix
        snapshot = make_market_snapshot(candidate_pair=_BTC_USDT)
        decision = _RULE.evaluate(portfolio_state=state, market_snapshot=snapshot)
        assert decision.action == RiskAction.OK, (
            f"Candidate with no correlation data must return OK, got {decision.action}"
        )


class TestCorrelationLockProtocolConformance:
    """Rule instance satisfies RiskOverlayPort at runtime."""

    def test_is_risk_overlay_port(self) -> None:
        """CorrelationLock must be an instance of RiskOverlayPort (runtime_checkable)."""
        assert isinstance(_RULE, RiskOverlayPort), (
            "CorrelationLock must satisfy RiskOverlayPort Protocol"
        )
