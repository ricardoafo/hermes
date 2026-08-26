"""Unit and property tests for CertificationLifecycle state machine.

Tests cover:
  - Valid forward transitions: UNTRUSTED → BACKTEST_CERTIFIED → ... → CERTIFIED
  - Precondition failures: UNTRUSTED → BACKTEST_CERTIFIED without fills
  - DEAD is absorbing: any advance from DEAD returns error
  - CERTIFIED is terminal: advance from CERTIFIED returns error
  - Illegal skips: UNTRUSTED → SHADOW_CERTIFIED returns error
  - Kill conditions: m05_orphan_count > 0 blocks transition; m04_rejection_rate >= 1 blocks
  - force_dead: always returns DEAD; empty reason raises
  - Property: every (state, report-with-fills) pair has a determined outcome
  - Property: any state with orphan > 0 cannot advance

Reference: spec-6-calibration-harness/change.md R-3, D-3.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from hermes.application.calibration.harness import CalibrationReport
from hermes.application.calibration.lifecycle import CertificationLifecycle, CertificationState
from hermes.domain.calibration.metrics import FillPriceDeltaBps, RegimeReturns

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

_LIFECYCLE = CertificationLifecycle()
_WIN_START = datetime(2024, 1, 1, tzinfo=UTC)
_WIN_END = datetime(2024, 1, 31, tzinfo=UTC)
_ZERO_BPS = FillPriceDeltaBps(
    avg_bps=Decimal("0"),
    p50_bps=Decimal("0"),
    p95_bps=Decimal("0"),
    p99_bps=Decimal("0"),
)
_ZERO_REGIME = RegimeReturns(
    bull_quiet=None,
    bull_vol=None,
    bear_quiet=None,
    bear_vol=None,
)


def _make_report(
    *,
    fill_count: int = 1,
    rejection_rate: str = "0",
    orphan_count: int = 0,
) -> CalibrationReport:
    """Build a minimal CalibrationReport with configurable key fields."""
    return CalibrationReport(
        strategy_id="swing-mvp1-v1",
        window_start=_WIN_START,
        window_end=_WIN_END,
        m01_fill_count=fill_count,
        m02_fill_price_delta_bps=_ZERO_BPS,
        m03_latency_ms=Decimal("0"),
        m04_rejection_rate=Decimal(rejection_rate),
        m05_orphan_position_count=orphan_count,
        m06_regime_returns=_ZERO_REGIME,
        m07_queue_quality=None,
    )


# ---------------------------------------------------------------------------
# Valid forward transitions
# ---------------------------------------------------------------------------


class TestCertificationLifecycleForwardTransitions:
    """Valid single-step forward transitions per pre-reg §7."""

    def test_untrusted_to_backtest_certified_with_fills(self) -> None:
        """UNTRUSTED → BACKTEST_CERTIFIED when m01_fill_count >= 1 (R-3.1)."""
        report = _make_report(fill_count=1)
        result = _LIFECYCLE.advance(CertificationState.UNTRUSTED, report)
        assert result == CertificationState.BACKTEST_CERTIFIED, (
            f"UNTRUSTED → BACKTEST_CERTIFIED must succeed with fill_count=1, "
            f"got {result!r}. R-3.1: at least 1 fill required."
        )

    def test_backtest_certified_to_shadow_certified(self) -> None:
        """BACKTEST_CERTIFIED → SHADOW_CERTIFIED (operator-triggered)."""
        report = _make_report(fill_count=5)
        result = _LIFECYCLE.advance(CertificationState.BACKTEST_CERTIFIED, report)
        assert result == CertificationState.SHADOW_CERTIFIED, (
            f"BACKTEST_CERTIFIED → SHADOW_CERTIFIED must succeed, got {result!r}."
        )

    def test_shadow_certified_to_oos_certified(self) -> None:
        """SHADOW_CERTIFIED → OOS_CERTIFIED (operator-triggered)."""
        report = _make_report(fill_count=5)
        result = _LIFECYCLE.advance(CertificationState.SHADOW_CERTIFIED, report)
        assert result == CertificationState.OOS_CERTIFIED, (
            f"SHADOW_CERTIFIED → OOS_CERTIFIED must succeed, got {result!r}."
        )

    def test_oos_certified_to_live_approved(self) -> None:
        """OOS_CERTIFIED → LIVE_APPROVED (operator manual gate)."""
        report = _make_report(fill_count=5)
        result = _LIFECYCLE.advance(CertificationState.OOS_CERTIFIED, report)
        assert result == CertificationState.LIVE_APPROVED, (
            f"OOS_CERTIFIED → LIVE_APPROVED must succeed, got {result!r}."
        )

    def test_live_approved_to_certified(self) -> None:
        """LIVE_APPROVED → CERTIFIED (operator-triggered after 90+ days live)."""
        report = _make_report(fill_count=5)
        result = _LIFECYCLE.advance(CertificationState.LIVE_APPROVED, report)
        assert result == CertificationState.CERTIFIED, (
            f"LIVE_APPROVED → CERTIFIED must succeed, got {result!r}."
        )


# ---------------------------------------------------------------------------
# Precondition failures
# ---------------------------------------------------------------------------


class TestCertificationLifecyclePreconditions:
    """Precondition failures return error strings, not states."""

    def test_untrusted_zero_fills_returns_error(self) -> None:
        """UNTRUSTED → BACKTEST_CERTIFIED with zero fills must return error string."""
        report = _make_report(fill_count=0)
        result = _LIFECYCLE.advance(CertificationState.UNTRUSTED, report)
        assert isinstance(result, str), (
            f"UNTRUSTED with 0 fills must return error string, got {result!r}."
        )
        assert "fill" in result.lower() or "BACKTEST" in result, (
            f"Error must mention fill count requirement, got '{result}'."
        )

    def test_certified_advance_returns_error(self) -> None:
        """CERTIFIED → advance must return error — terminal success state (R-3.2 related)."""
        report = _make_report(fill_count=10)
        result = _LIFECYCLE.advance(CertificationState.CERTIFIED, report)
        assert isinstance(result, str), (
            f"CERTIFIED is terminal — advance must return error, got {result!r}."
        )

    def test_dead_advance_returns_error(self) -> None:
        """DEAD → any returns error — absorbing state (R-3.4)."""
        report = _make_report(fill_count=5)
        result = _LIFECYCLE.advance(CertificationState.DEAD, report)
        assert isinstance(result, str), (
            f"DEAD advance must return error, got {result!r}. R-3.4: DEAD is absorbing."
        )
        assert "DEAD" in result or "absorbing" in result, (
            f"Error must mention DEAD/absorbing state, got '{result}'."
        )


# ---------------------------------------------------------------------------
# Kill conditions
# ---------------------------------------------------------------------------


class TestCertificationLifecycleKillConditions:
    """Kill conditions block transition and return error string."""

    def test_orphan_position_blocks_any_transition(self) -> None:
        """m05_orphan_position_count > 0 must block any transition — SA-4 P0 (R-3.2)."""
        report = _make_report(fill_count=5, orphan_count=1)
        for state in [
            CertificationState.UNTRUSTED,
            CertificationState.BACKTEST_CERTIFIED,
            CertificationState.SHADOW_CERTIFIED,
            CertificationState.OOS_CERTIFIED,
            CertificationState.LIVE_APPROVED,
        ]:
            result = _LIFECYCLE.advance(state, report)
            assert isinstance(result, str), (
                f"Orphan position must block transition from {state!r}, got {result!r}. "
                "SA-4 P0: every position must trace to a fill."
            )
            assert "orphan" in result.lower() or "SA-4" in result, (
                f"Error must mention orphan/SA-4 for state {state!r}, got '{result}'."
            )

    def test_full_rejection_rate_blocks_transition(self) -> None:
        """m04_rejection_rate == 1.0 must block transition — fill model broken."""
        report = _make_report(fill_count=0, rejection_rate="1")
        result = _LIFECYCLE.advance(CertificationState.UNTRUSTED, report)
        assert isinstance(result, str), (
            f"100% rejection rate must block transition, got {result!r}. "
            "Fill model is completely broken."
        )

    def test_zero_orphans_does_not_block(self) -> None:
        """m05_orphan_position_count == 0 must not trigger kill condition."""
        report = _make_report(fill_count=3, orphan_count=0)
        result = _LIFECYCLE.advance(CertificationState.UNTRUSTED, report)
        assert result == CertificationState.BACKTEST_CERTIFIED, (
            f"Zero orphans must not block UNTRUSTED → BACKTEST_CERTIFIED, got {result!r}."
        )


# ---------------------------------------------------------------------------
# force_dead
# ---------------------------------------------------------------------------


class TestCertificationLifecycleForceDead:
    """force_dead always returns DEAD; empty reason raises."""

    def test_force_dead_from_any_state_returns_dead(self) -> None:
        """force_dead must return DEAD from any state."""
        for state in CertificationState:
            result = _LIFECYCLE.force_dead(state, reason="Kill trigger fired per §10.")
            assert result == CertificationState.DEAD, (
                f"force_dead from {state!r} must return DEAD, got {result!r}."
            )

    def test_force_dead_empty_reason_raises(self) -> None:
        """force_dead with empty reason must raise ValueError."""
        with pytest.raises(ValueError, match="reason"):
            _LIFECYCLE.force_dead(CertificationState.UNTRUSTED, reason="")

    def test_force_dead_whitespace_reason_raises(self) -> None:
        """force_dead with whitespace-only reason must raise ValueError."""
        with pytest.raises(ValueError, match="reason"):
            _LIFECYCLE.force_dead(CertificationState.CERTIFIED, reason="   ")


# ---------------------------------------------------------------------------
# Property tests — transition matrix
# ---------------------------------------------------------------------------


_ADVANCEABLE_STATES = [
    CertificationState.UNTRUSTED,
    CertificationState.BACKTEST_CERTIFIED,
    CertificationState.SHADOW_CERTIFIED,
    CertificationState.OOS_CERTIFIED,
    CertificationState.LIVE_APPROVED,
]


class TestCertificationLifecycleProperties:
    """Property tests over the 6-state x multiple-outcome transition matrix."""

    @given(fill_count=st.integers(min_value=1, max_value=10_000))
    @settings(max_examples=50)
    def test_untrusted_with_any_fill_advances(self, fill_count: int) -> None:
        """For all fill_count >= 1, UNTRUSTED always advances to BACKTEST_CERTIFIED."""
        report = _make_report(fill_count=fill_count)
        result = _LIFECYCLE.advance(CertificationState.UNTRUSTED, report)
        assert result == CertificationState.BACKTEST_CERTIFIED, (
            f"UNTRUSTED with fill_count={fill_count} must always produce BACKTEST_CERTIFIED, "
            f"got {result!r}. Property: ∀ fill_count ≥ 1 → BACKTEST_CERTIFIED."
        )

    @given(orphan_count=st.integers(min_value=1, max_value=100))
    @settings(max_examples=50)
    def test_any_state_with_orphan_blocks(self, orphan_count: int) -> None:
        """For all orphan_count >= 1, any advanceable state returns error."""
        report = _make_report(fill_count=5, orphan_count=orphan_count)
        for state in _ADVANCEABLE_STATES:
            result = _LIFECYCLE.advance(state, report)
            assert isinstance(result, str), (
                f"orphan_count={orphan_count} from {state!r} must return error, "
                f"got {result!r}. SA-4 P0 kill condition."
            )

    @given(state=st.sampled_from(list(CertificationState)))
    @settings(max_examples=20)
    def test_dead_always_returns_error(self, state: CertificationState) -> None:
        """DEAD.advance always returns error regardless of report content."""
        report = _make_report(fill_count=100)
        result = _LIFECYCLE.advance(CertificationState.DEAD, report)
        assert isinstance(result, str), (
            f"DEAD must always return error, got {result!r}. DEAD is absorbing."
        )

    @given(
        fill_count=st.integers(min_value=1, max_value=10_000),
        state=st.sampled_from(_ADVANCEABLE_STATES),
    )
    @settings(max_examples=50)
    def test_advance_returns_state_or_string(
        self, fill_count: int, state: CertificationState
    ) -> None:
        """Advance must always return CertificationState or str — never raises."""
        report = _make_report(fill_count=fill_count)
        result = _LIFECYCLE.advance(state, report)
        assert isinstance(result, (CertificationState, str)), (
            f"advance({state!r}, report) must return CertificationState or str, "
            f"got {type(result).__name__!r}. Must never raise for valid inputs."
        )

    @given(
        state=st.sampled_from(_ADVANCEABLE_STATES),
        fill_count=st.integers(min_value=0, max_value=10_000),
    )
    @settings(max_examples=50)
    def test_advance_is_deterministic(self, state: CertificationState, fill_count: int) -> None:
        """Advance must be deterministic: same (state, report) always yields same result."""
        report = _make_report(fill_count=fill_count)
        result1 = _LIFECYCLE.advance(state, report)
        result2 = _LIFECYCLE.advance(state, report)
        assert result1 == result2, (
            f"advance({state!r}, report) must be deterministic, "
            f"got {result1!r} then {result2!r}. Pure function contract."
        )
        assert type(result1) is type(result2), (
            f"Types must match for determinism: "
            f"{type(result1).__name__} vs {type(result2).__name__}."
        )
