"""Unit tests for H1AcceptanceGate.

Tests cover:
- All thresholds individually: failure when each criterion fails.
- Full pass: accepted=True when all criteria pass.
- Multiple failures reported in a single AcceptanceDecision.
- Boundary: DSR exactly at threshold fails (threshold is STRICT >).
- Property: any result with DSR <= 0.7 must produce accepted=False.
- Property: any result where drawdown >= 0.15 must produce accepted=False.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from hermes.application.strategy_protocol.h1_acceptance_gate import (
    CertificationResult,
    H1AcceptanceGate,
)
from hermes.application.strategy_protocol.preregistration import PreRegistration

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def gate() -> H1AcceptanceGate:
    """Return an H1AcceptanceGate using the real pre-registration thresholds."""
    here = Path(__file__).resolve().parent
    for candidate in [here, *here.parents]:
        if (candidate / "pyproject.toml").exists():
            pre_reg = PreRegistration(project_root=candidate)
            return H1AcceptanceGate(pre_reg=pre_reg)
    pytest.skip("Could not find project root")


def _passing_result(**overrides: float | int) -> CertificationResult:
    """Return a CertificationResult that passes all §9 criteria by default."""
    defaults: dict[str, float | int] = {
        "dsr": 0.75,
        "bootstrap_ci_lower": 0.05,
        "sharpe": 0.8,
        "max_drawdown": 0.10,
        "ulcer_index": 7.0,
        "recovery_days": 60.0,
        "win_rate": 0.40,
        "oos_trade_count": 45,
    }
    defaults.update(overrides)
    return CertificationResult(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Full pass
# ---------------------------------------------------------------------------


class TestH1AcceptanceGateFullPass:
    """H1 is accepted when all §9 criteria pass simultaneously."""

    def test_all_criteria_pass_accepted_true(self, gate: H1AcceptanceGate) -> None:
        """accepted=True when DSR, bootstrap CI, and all secondary metrics pass."""
        result = _passing_result()
        decision = gate.evaluate(result)
        assert decision.accepted is True, (
            f"Expected accepted=True for a fully passing result, got {decision}. "
            f"Check §9 threshold logic."
        )

    def test_all_criteria_pass_failed_criteria_empty(self, gate: H1AcceptanceGate) -> None:
        """failed_criteria must be an empty tuple when all criteria pass."""
        result = _passing_result()
        decision = gate.evaluate(result)
        assert decision.failed_criteria == (), (
            f"Expected empty failed_criteria for fully passing result, "
            f"got {decision.failed_criteria}."
        )


# ---------------------------------------------------------------------------
# Individual criterion failures
# ---------------------------------------------------------------------------


class TestH1AcceptanceGateIndividualFailures:
    """Each §9 criterion failure independently produces accepted=False with the criterion named."""

    def test_dsr_at_threshold_fails(self, gate: H1AcceptanceGate) -> None:
        """DSR = 0.7 exactly must fail — threshold is strictly > 0.7 per §9."""
        result = _passing_result(dsr=0.7)
        decision = gate.evaluate(result)
        assert decision.accepted is False, (
            "DSR exactly at 0.7 must fail (threshold is strictly >). Got accepted=True."
        )
        assert "dsr" in decision.failed_criteria, (
            f"'dsr' must be in failed_criteria when DSR = 0.7. Got {decision.failed_criteria}."
        )

    def test_dsr_below_threshold_fails(self, gate: H1AcceptanceGate) -> None:
        """DSR < 0.7 must produce accepted=False with 'dsr' in failed_criteria."""
        result = _passing_result(dsr=0.3)
        decision = gate.evaluate(result)
        assert decision.accepted is False, "DSR below 0.7 must produce accepted=False."
        assert "dsr" in decision.failed_criteria, (
            f"'dsr' must be in failed_criteria for DSR=0.3. Got {decision.failed_criteria}."
        )

    def test_bootstrap_ci_at_zero_fails(self, gate: H1AcceptanceGate) -> None:
        """bootstrap_ci_lower = 0.0 must fail — threshold is strictly > 0 per §9."""
        result = _passing_result(bootstrap_ci_lower=0.0)
        decision = gate.evaluate(result)
        assert decision.accepted is False, (
            "bootstrap_ci_lower exactly at 0.0 must fail (threshold is strictly >)."
        )
        assert "bootstrap_ci" in decision.failed_criteria, (
            f"'bootstrap_ci' must be in failed_criteria. Got {decision.failed_criteria}."
        )

    def test_bootstrap_ci_negative_fails(self, gate: H1AcceptanceGate) -> None:
        """Negative bootstrap_ci_lower must fail — CI lower bound must be > 0."""
        result = _passing_result(bootstrap_ci_lower=-0.1)
        decision = gate.evaluate(result)
        assert "bootstrap_ci" in decision.failed_criteria, (
            f"Negative bootstrap_ci_lower must fail. Got {decision.failed_criteria}."
        )

    def test_oos_trade_count_below_minimum_fails(self, gate: H1AcceptanceGate) -> None:
        """oos_trade_count < 30 must fail — minimum sample for CI meaningfulness is 30."""
        result = _passing_result(oos_trade_count=29)
        decision = gate.evaluate(result)
        assert decision.accepted is False, "oos_trade_count=29 must fail (minimum is 30)."
        assert "oos_trade_count" in decision.failed_criteria, (
            f"'oos_trade_count' must be in failed_criteria for count=29. "
            f"Got {decision.failed_criteria}."
        )

    def test_max_drawdown_at_threshold_fails(self, gate: H1AcceptanceGate) -> None:
        """max_drawdown = 0.15 exactly must fail — threshold is strictly < 15% per §9."""
        result = _passing_result(max_drawdown=0.15)
        decision = gate.evaluate(result)
        assert decision.accepted is False, (
            "max_drawdown exactly at 0.15 must fail (threshold is strictly <)."
        )
        assert "max_drawdown" in decision.failed_criteria, (
            f"'max_drawdown' must be in failed_criteria. Got {decision.failed_criteria}."
        )

    def test_max_drawdown_above_threshold_fails(self, gate: H1AcceptanceGate) -> None:
        """max_drawdown > 15% must fail with 'max_drawdown' in failed_criteria."""
        result = _passing_result(max_drawdown=0.20)
        decision = gate.evaluate(result)
        assert "max_drawdown" in decision.failed_criteria, (
            f"max_drawdown=20% must fail. Got {decision.failed_criteria}."
        )

    def test_recovery_days_at_threshold_fails(self, gate: H1AcceptanceGate) -> None:
        """recovery_days = 90.0 must fail — threshold is strictly < 90 days per §9."""
        result = _passing_result(recovery_days=90.0)
        decision = gate.evaluate(result)
        assert decision.accepted is False, (
            "recovery_days exactly at 90.0 must fail (threshold is strictly <)."
        )
        assert "recovery_days" in decision.failed_criteria, (
            f"'recovery_days' must be in failed_criteria for 90 days. "
            f"Got {decision.failed_criteria}."
        )

    def test_ulcer_index_at_threshold_fails(self, gate: H1AcceptanceGate) -> None:
        """ulcer_index = 10.0 must fail — threshold is strictly < 10 per §9."""
        result = _passing_result(ulcer_index=10.0)
        decision = gate.evaluate(result)
        assert decision.accepted is False, (
            "ulcer_index exactly at 10.0 must fail (threshold is strictly <)."
        )
        assert "ulcer_index" in decision.failed_criteria, (
            f"'ulcer_index' must be in failed_criteria for index=10.0. "
            f"Got {decision.failed_criteria}."
        )

    def test_win_rate_below_threshold_fails(self, gate: H1AcceptanceGate) -> None:
        """win_rate < 0.30 must fail — minimum win rate is 30% per §9."""
        result = _passing_result(win_rate=0.25)
        decision = gate.evaluate(result)
        assert decision.accepted is False, "win_rate=25% must fail (minimum is 30%)."
        assert "win_rate" in decision.failed_criteria, (
            f"'win_rate' must be in failed_criteria for win_rate=0.25. "
            f"Got {decision.failed_criteria}."
        )

    def test_win_rate_at_threshold_passes(self, gate: H1AcceptanceGate) -> None:
        """win_rate = 0.30 exactly must pass — threshold is >= 30% per §9."""
        result = _passing_result(win_rate=0.30)
        decision = gate.evaluate(result)
        assert "win_rate" not in decision.failed_criteria, (
            f"win_rate exactly at 0.30 must pass. Got {decision.failed_criteria}."
        )


# ---------------------------------------------------------------------------
# Multiple failures
# ---------------------------------------------------------------------------


class TestH1AcceptanceGateMultipleFailures:
    """Multiple failed criteria are all reported in a single AcceptanceDecision."""

    def test_multiple_failures_all_reported(self, gate: H1AcceptanceGate) -> None:
        """All failed criteria must appear in failed_criteria (not just the first)."""
        result = _passing_result(
            dsr=0.5,
            max_drawdown=0.20,
            win_rate=0.20,
        )
        decision = gate.evaluate(result)
        assert decision.accepted is False, "Multiple failures must produce accepted=False."
        assert "dsr" in decision.failed_criteria, (
            f"'dsr' must be in failed_criteria. Got {decision.failed_criteria}."
        )
        assert "max_drawdown" in decision.failed_criteria, (
            f"'max_drawdown' must be in failed_criteria. Got {decision.failed_criteria}."
        )
        assert "win_rate" in decision.failed_criteria, (
            f"'win_rate' must be in failed_criteria. Got {decision.failed_criteria}."
        )
        assert len(decision.failed_criteria) >= 3, (
            f"Expected at least 3 failed criteria, got {len(decision.failed_criteria)}: "
            f"{decision.failed_criteria}."
        )


# ---------------------------------------------------------------------------
# Property tests
# ---------------------------------------------------------------------------


class TestH1AcceptanceGateProperties:
    """Property tests — structural invariants across the threshold matrix."""

    @given(dsr=st.floats(max_value=0.7, allow_nan=False, allow_infinity=False))
    @settings(max_examples=50)
    def test_dsr_lte_threshold_always_fails(self, dsr: float, gate: H1AcceptanceGate) -> None:
        """Any DSR <= 0.7 must always produce accepted=False with 'dsr' in failed_criteria."""
        result = _passing_result(dsr=dsr)
        decision = gate.evaluate(result)
        assert decision.accepted is False, (
            f"DSR={dsr} <= 0.7 must always produce accepted=False, got accepted=True."
        )
        assert "dsr" in decision.failed_criteria, (
            f"'dsr' must be in failed_criteria for DSR={dsr}. Got {decision.failed_criteria}."
        )

    @given(drawdown=st.floats(min_value=0.15, max_value=1.0, allow_nan=False, allow_infinity=False))
    @settings(max_examples=50)
    def test_drawdown_gte_threshold_always_fails(
        self, drawdown: float, gate: H1AcceptanceGate
    ) -> None:
        """Drawdown >= 0.15 must produce accepted=False with 'max_drawdown' in failed_criteria."""
        result = _passing_result(max_drawdown=drawdown)
        decision = gate.evaluate(result)
        assert decision.accepted is False, (
            f"max_drawdown={drawdown} >= 0.15 must always produce accepted=False."
        )
        assert "max_drawdown" in decision.failed_criteria, (
            f"'max_drawdown' must be in failed_criteria for drawdown={drawdown}."
        )
