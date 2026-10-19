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

from hermes.application.calibration.certification_gates import GateCheck, GateReport
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


_NEXT = {
    CertificationState.UNTRUSTED: CertificationState.BACKTEST_CERTIFIED,
    CertificationState.BACKTEST_CERTIFIED: CertificationState.SHADOW_CERTIFIED,
    CertificationState.SHADOW_CERTIFIED: CertificationState.OOS_CERTIFIED,
    CertificationState.OOS_CERTIFIED: CertificationState.LIVE_APPROVED,
    CertificationState.LIVE_APPROVED: CertificationState.CERTIFIED,
}


def _orphan_message(count: int) -> str:
    """Return the refusal for ``count`` orphan positions."""
    return (
        f"CertificationLifecycle: SA-4 P0 violation — "
        f"{count} orphan position(s) detected. "
        "Every open position must trace to a fill event. "
        "Transition blocked; state must be set to DEAD. Pre-reg §7."
    )


def _gate(state: CertificationState, *, passed: bool = True, refused: str = "") -> GateReport:
    """Build a gate report for the transition out of ``state`` with one check."""
    check = GateCheck("evidence", "test requirement", "ok" if passed else "bad", passed)
    return GateReport("swing-mvp1-v1", state, _NEXT.get(state), (check,), refused_reason=refused)


# ---------------------------------------------------------------------------
# Valid forward transitions
# ---------------------------------------------------------------------------


class TestCertificationLifecycleForwardTransitions:
    """Valid single-step forward transitions per pre-reg §7."""

    def test_untrusted_to_backtest_certified_with_fills(self) -> None:
        """UNTRUSTED → BACKTEST_CERTIFIED when m01_fill_count >= 1 (R-3.1)."""
        report = _make_report(fill_count=1)
        result = _LIFECYCLE.advance(
            CertificationState.UNTRUSTED, report, gate=_gate(CertificationState.UNTRUSTED)
        )
        assert result == CertificationState.BACKTEST_CERTIFIED, (
            f"UNTRUSTED → BACKTEST_CERTIFIED must succeed with fill_count=1, "
            f"got {result!r}. R-3.1: at least 1 fill required."
        )

    def test_backtest_certified_to_shadow_certified(self) -> None:
        """BACKTEST_CERTIFIED → SHADOW_CERTIFIED (operator-triggered)."""
        report = _make_report(fill_count=5)
        result = _LIFECYCLE.advance(
            CertificationState.BACKTEST_CERTIFIED,
            report,
            gate=_gate(CertificationState.BACKTEST_CERTIFIED),
        )
        assert result == CertificationState.SHADOW_CERTIFIED, (
            f"BACKTEST_CERTIFIED → SHADOW_CERTIFIED must succeed, got {result!r}."
        )

    def test_shadow_certified_to_oos_certified(self) -> None:
        """SHADOW_CERTIFIED → OOS_CERTIFIED (operator-triggered)."""
        report = _make_report(fill_count=5)
        result = _LIFECYCLE.advance(
            CertificationState.SHADOW_CERTIFIED,
            report,
            gate=_gate(CertificationState.SHADOW_CERTIFIED),
        )
        assert result == CertificationState.OOS_CERTIFIED, (
            f"SHADOW_CERTIFIED → OOS_CERTIFIED must succeed, got {result!r}."
        )

    def test_oos_certified_to_live_approved(self) -> None:
        """OOS_CERTIFIED → LIVE_APPROVED (operator manual gate)."""
        report = _make_report(fill_count=5)
        result = _LIFECYCLE.advance(
            CertificationState.OOS_CERTIFIED, report, gate=_gate(CertificationState.OOS_CERTIFIED)
        )
        assert result == CertificationState.LIVE_APPROVED, (
            f"OOS_CERTIFIED → LIVE_APPROVED must succeed, got {result!r}."
        )

    def test_live_approved_to_certified(self) -> None:
        """LIVE_APPROVED → CERTIFIED (operator-triggered after 90+ days live)."""
        report = _make_report(fill_count=5)
        result = _LIFECYCLE.advance(
            CertificationState.LIVE_APPROVED, report, gate=_gate(CertificationState.LIVE_APPROVED)
        )
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
        result = _LIFECYCLE.advance(
            CertificationState.UNTRUSTED, report, gate=_gate(CertificationState.UNTRUSTED)
        )
        assert result == (
            "CertificationLifecycle: cannot advance UNTRUSTED → BACKTEST_CERTIFIED. "
            "m01_fill_count=0 — at least 1 fill required to certify backtest evidence. "
            "Pre-reg §7."
        )

    def test_certified_advance_returns_error(self) -> None:
        """CERTIFIED → advance must return error — terminal success state (R-3.2 related)."""
        report = _make_report(fill_count=10)
        result = _LIFECYCLE.advance(
            CertificationState.CERTIFIED, report, gate=_gate(CertificationState.CERTIFIED)
        )
        assert result == (
            "CertificationLifecycle: already CERTIFIED — no further advance. "
            "The lifecycle is complete. Pre-reg §7."
        )

    def test_dead_advance_returns_error(self) -> None:
        """DEAD → any returns error — absorbing state (R-3.4)."""
        report = _make_report(fill_count=5)
        result = _LIFECYCLE.advance(
            CertificationState.DEAD, report, gate=_gate(CertificationState.DEAD)
        )
        assert result == (
            "CertificationLifecycle: DEAD state is absorbing — no advance possible. "
            "A new pre-registration (v2) is required to restart the lifecycle. "
            "Pre-reg §7."
        )


# ---------------------------------------------------------------------------
# Gate enforcement (#355)
# ---------------------------------------------------------------------------


class TestCertificationLifecycleGateEnforcement:
    """Forward transitions need a passed gate for exactly that transition."""

    # SEV-1: real-capital gate — no transition without its pre-registration §7 evidence
    @pytest.mark.parametrize("state", list(_NEXT))
    def test_failed_gate_blocks_transition(self, state: CertificationState) -> None:
        """A gate with a failing check keeps the state (pre-reg §7 entry criteria)."""
        result = _LIFECYCLE.advance(state, _make_report(), gate=_gate(state, passed=False))
        assert isinstance(result, str)
        assert "evidence" in result

    # SEV-1: real-capital gate — a refused gate never advances
    @pytest.mark.parametrize("state", list(_NEXT))
    def test_refused_gate_blocks_transition(self, state: CertificationState) -> None:
        """A refused gate (too early, shot spent) keeps the state."""
        result = _LIFECYCLE.advance(state, _make_report(), gate=_gate(state, refused="too early"))
        assert isinstance(result, str)
        assert "too early" in result

    def test_gate_for_another_transition_blocks(self) -> None:
        """A passed backtest gate cannot authorise the OOS transition."""
        result = _LIFECYCLE.advance(
            CertificationState.SHADOW_CERTIFIED,
            _make_report(),
            gate=_gate(CertificationState.UNTRUSTED),
        )
        assert isinstance(result, str)
        assert "not SHADOW_CERTIFIED" in result


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
            result = _LIFECYCLE.advance(state, report, gate=_gate(state))
            assert result == (
                "CertificationLifecycle: SA-4 P0 violation — 1 orphan position(s) detected. "
                "Every open position must trace to a fill event. "
                "Transition blocked; state must be set to DEAD. Pre-reg §7."
            ), f"Orphan position must block transition from {state!r}, got {result!r}."

    def test_full_rejection_rate_blocks_transition(self) -> None:
        """m04_rejection_rate == 1.0 must block transition — fill model broken."""
        report = _make_report(fill_count=0, rejection_rate="1")
        result = _LIFECYCLE.advance(
            CertificationState.UNTRUSTED, report, gate=_gate(CertificationState.UNTRUSTED)
        )
        assert result == (
            "CertificationLifecycle: fill model completely broken — "
            "rejection rate=1 (100%). "
            "No fills produced. Transition blocked; state must be set to DEAD. "
            "Pre-reg §7."
        )

    def test_zero_orphans_does_not_block(self) -> None:
        """m05_orphan_position_count == 0 must not trigger kill condition."""
        report = _make_report(fill_count=3, orphan_count=0)
        result = _LIFECYCLE.advance(
            CertificationState.UNTRUSTED, report, gate=_gate(CertificationState.UNTRUSTED)
        )
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
        result = _LIFECYCLE.advance(
            CertificationState.UNTRUSTED, report, gate=_gate(CertificationState.UNTRUSTED)
        )
        assert result == CertificationState.BACKTEST_CERTIFIED, (
            f"UNTRUSTED with fill_count={fill_count} must always produce BACKTEST_CERTIFIED, "
            f"got {result!r}. Property: ∀ fill_count ≥ 1 → BACKTEST_CERTIFIED."
        )

    @given(orphan_count=st.integers(min_value=1, max_value=100))
    @settings(max_examples=50)
    def test_any_state_with_orphan_blocks(self, orphan_count: int) -> None:
        """For all orphan_count >= 1, any advanceable state is refused with the SA-4 message."""
        report = _make_report(fill_count=5, orphan_count=orphan_count)
        for state in _ADVANCEABLE_STATES:
            assert _LIFECYCLE.advance(state, report, gate=_gate(state)) == _orphan_message(
                orphan_count
            )

    @given(
        fill_count=st.integers(min_value=0, max_value=10_000),
        orphan_count=st.integers(min_value=0, max_value=100),
    )
    @settings(max_examples=20)
    def test_dead_always_returns_error(self, fill_count: int, orphan_count: int) -> None:
        """DEAD.advance gives the absorbing-state message whatever the report holds."""
        report = _make_report(fill_count=fill_count, orphan_count=orphan_count)
        result = _LIFECYCLE.advance(
            CertificationState.DEAD, report, gate=_gate(CertificationState.DEAD)
        )
        assert result == (
            "CertificationLifecycle: DEAD state is absorbing — no advance possible. "
            "A new pre-registration (v2) is required to restart the lifecycle. "
            "Pre-reg §7."
        )

    @given(
        fill_count=st.integers(min_value=1, max_value=10_000),
        state=st.sampled_from(_ADVANCEABLE_STATES),
    )
    @settings(max_examples=50)
    def test_advance_moves_one_step_forward(
        self, fill_count: int, state: CertificationState
    ) -> None:
        """With fills, no kill condition and a passing gate, advance gives the next state."""
        report = _make_report(fill_count=fill_count)
        assert _LIFECYCLE.advance(state, report, gate=_gate(state)) == _NEXT[state]

    @given(
        state=st.sampled_from(_ADVANCEABLE_STATES),
        fill_count=st.integers(min_value=0, max_value=10_000),
    )
    @settings(max_examples=50)
    def test_advance_is_deterministic(self, state: CertificationState, fill_count: int) -> None:
        """Advance must be deterministic: same (state, report) always yields same result."""
        report = _make_report(fill_count=fill_count)
        result1 = _LIFECYCLE.advance(state, report, gate=_gate(state))
        result2 = _LIFECYCLE.advance(state, report, gate=_gate(state))
        assert result1 == result2, (
            f"advance({state!r}, report) must be deterministic, "
            f"got {result1!r} then {result2!r}. Pure function contract."
        )
        assert type(result1) is type(result2), (
            f"Types must match for determinism: "
            f"{type(result1).__name__} vs {type(result2).__name__}."
        )
