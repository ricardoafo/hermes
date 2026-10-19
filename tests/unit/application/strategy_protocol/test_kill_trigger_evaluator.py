"""Unit tests for KillTriggerEvaluator.

Tests cover:
- Each kill trigger independently:
    - Max drawdown >= 15% triggers kill.
    - SA-9 HALT signal triggers kill.
    - Days since equity high >= 90 triggers kill.
    - 30-day rolling Sharpe < 0.3 triggers kill (when sample >= 30).
- No kill when all conditions are below threshold.
- Kill reason string is non-empty when triggered.
- rolling_30d_sharpe=None skips the Sharpe check.
- Boundary conditions: drawdown exactly at 15% triggers.
- Priority: drawdown checked before SA-9 (first match wins).
- Property: drawdown >= 0.15 always triggers.
- Property: no trigger below all thresholds.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from hermes.application.strategy_protocol.kill_trigger_evaluator import (
    ExperimentState,
    KillTriggerEvaluator,
)
from hermes.application.strategy_protocol.preregistration import PreRegistration
from tests.support.paths import REPO_ROOT

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def evaluator() -> KillTriggerEvaluator:
    """Return a KillTriggerEvaluator using real pre-registration thresholds."""
    pre_reg = PreRegistration(project_root=REPO_ROOT)
    return KillTriggerEvaluator(pre_reg=pre_reg)


def _safe_state(**overrides: float | bool | None) -> ExperimentState:
    """Return a safe ExperimentState below all kill thresholds."""
    defaults: dict[str, float | bool | None] = {
        "max_drawdown": 0.05,
        "days_since_equity_high": 10.0,
        "sa9_halt": False,
        "rolling_30d_sharpe": 0.8,
    }
    defaults.update(overrides)
    return ExperimentState(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# No-kill path
# ---------------------------------------------------------------------------


class TestKillTriggerEvaluatorNoKill:
    """KillDecision(triggered=False) when all conditions are below kill thresholds."""

    def test_safe_state_not_triggered(self, evaluator: KillTriggerEvaluator) -> None:
        """triggered=False for a state where all metrics are below kill thresholds."""
        state = _safe_state()
        decision = evaluator.evaluate(state)
        assert decision.triggered is False, (
            f"Expected triggered=False for safe state, got {decision}. "
            f"No kill criterion should fire below all thresholds."
        )

    def test_safe_state_reason_is_empty(self, evaluator: KillTriggerEvaluator) -> None:
        """Reason must be empty string when no kill criterion fires."""
        state = _safe_state()
        decision = evaluator.evaluate(state)
        assert decision.reason == "", (
            f"Expected empty reason for safe state, got '{decision.reason}'."
        )

    def test_none_rolling_sharpe_skips_sharpe_check(self, evaluator: KillTriggerEvaluator) -> None:
        """rolling_30d_sharpe=None must not trigger kill — insufficient sample, check skipped."""
        state = _safe_state(rolling_30d_sharpe=None)
        decision = evaluator.evaluate(state)
        assert decision.triggered is False, (
            f"rolling_30d_sharpe=None must not trigger kill (insufficient sample). Got {decision}."
        )


# ---------------------------------------------------------------------------
# Individual trigger tests
# ---------------------------------------------------------------------------


class TestKillTriggerEvaluatorIndividualTriggers:
    """Each §10 kill criterion independently triggers KillDecision(triggered=True)."""

    def test_max_drawdown_at_threshold_triggers(self, evaluator: KillTriggerEvaluator) -> None:
        """Drawdown exactly at 15% must trigger kill — threshold is >= per §10."""
        state = _safe_state(max_drawdown=0.15)
        decision = evaluator.evaluate(state)
        assert decision.triggered is True, (
            f"max_drawdown=15% must trigger kill (threshold is exactly 15%). Got {decision}."
        )

    def test_max_drawdown_above_threshold_triggers(self, evaluator: KillTriggerEvaluator) -> None:
        """Drawdown > 15% must trigger kill."""
        state = _safe_state(max_drawdown=0.20)
        decision = evaluator.evaluate(state)
        assert decision.triggered is True, f"max_drawdown=20% must trigger kill. Got {decision}."

    def test_max_drawdown_trigger_has_non_empty_reason(
        self, evaluator: KillTriggerEvaluator
    ) -> None:
        """Kill reason must be a non-empty string that describes the drawdown trigger."""
        state = _safe_state(max_drawdown=0.20)
        decision = evaluator.evaluate(state)
        assert decision.reason == "Max drawdown 20.0% >= kill threshold 15.0% (pre-reg §10).", (
            f"Kill reason must name the drawdown and the 15% threshold. Got '{decision.reason}'."
        )

    def test_sa9_halt_triggers(self, evaluator: KillTriggerEvaluator) -> None:
        """SA-9 HALT signal must trigger kill per §10."""
        state = _safe_state(sa9_halt=True)
        decision = evaluator.evaluate(state)
        assert decision.triggered is True, f"SA-9 HALT signal must trigger kill. Got {decision}."

    def test_sa9_halt_reason_mentions_sa9(self, evaluator: KillTriggerEvaluator) -> None:
        """Kill reason for SA-9 halt must mention 'SA-9' for operator traceability."""
        state = _safe_state(sa9_halt=True)
        decision = evaluator.evaluate(state)
        assert "SA-9" in decision.reason, (
            f"Kill reason for SA-9 halt must mention 'SA-9'. Got '{decision.reason}'."
        )

    def test_recovery_days_at_threshold_triggers(self, evaluator: KillTriggerEvaluator) -> None:
        """days_since_equity_high = 90 must trigger kill — threshold is >= 90 per §10."""
        state = _safe_state(days_since_equity_high=90.0)
        decision = evaluator.evaluate(state)
        assert decision.triggered is True, (
            f"days_since_equity_high=90 must trigger kill. Got {decision}."
        )

    def test_recovery_days_below_threshold_safe(self, evaluator: KillTriggerEvaluator) -> None:
        """days_since_equity_high < 90 must not trigger kill."""
        state = _safe_state(days_since_equity_high=89.0)
        decision = evaluator.evaluate(state)
        assert decision.triggered is False, (
            f"days_since_equity_high=89 must not trigger kill. Got {decision}."
        )

    def test_rolling_sharpe_below_floor_triggers(self, evaluator: KillTriggerEvaluator) -> None:
        """rolling_30d_sharpe < 0.3 must trigger kill per §10."""
        state = _safe_state(rolling_30d_sharpe=0.2)
        decision = evaluator.evaluate(state)
        assert decision.triggered is True, (
            f"rolling_30d_sharpe=0.2 < 0.3 must trigger kill. Got {decision}."
        )

    def test_rolling_sharpe_at_floor_does_not_trigger(
        self, evaluator: KillTriggerEvaluator
    ) -> None:
        """rolling_30d_sharpe exactly at 0.3 must not trigger kill — threshold is strictly <."""
        state = _safe_state(rolling_30d_sharpe=0.3)
        decision = evaluator.evaluate(state)
        assert decision.triggered is False, (
            f"rolling_30d_sharpe=0.3 exactly at floor must not trigger. Got {decision}."
        )

    def test_rolling_sharpe_above_floor_safe(self, evaluator: KillTriggerEvaluator) -> None:
        """rolling_30d_sharpe > 0.3 must not trigger kill."""
        state = _safe_state(rolling_30d_sharpe=0.5)
        decision = evaluator.evaluate(state)
        assert decision.triggered is False, (
            f"rolling_30d_sharpe=0.5 must not trigger kill. Got {decision}."
        )


# ---------------------------------------------------------------------------
# Priority ordering
# ---------------------------------------------------------------------------


class TestHeldShadowKill:
    """A shadow kill serve recorded holds on every later evaluation (#796)."""

    # SEV-1: S6 a recorded kill that a healthy snapshot clears would reopen entries (#796)
    def test_a_held_kill_fires_on_an_otherwise_safe_state(
        self, evaluator: KillTriggerEvaluator
    ) -> None:
        """``killed_by`` set fires under that rule even with every metric safe."""
        decision = evaluator.evaluate(replace(_safe_state(), killed_by="sa9_repeat"))
        assert (decision.triggered, decision.rule) == (True, "sa9_repeat")

    def test_the_held_kill_wins_over_a_live_drawdown(self, evaluator: KillTriggerEvaluator) -> None:
        """The held rule is reported, not the drawdown that would also fire."""
        state = replace(_safe_state(max_drawdown=0.5), killed_by="shadow_sharpe")
        assert evaluator.evaluate(state).rule == "shadow_sharpe"


class TestKillTriggerEvaluatorPriority:
    """Kill triggers are evaluated in priority order; first match wins."""

    def test_drawdown_checked_before_sa9(self, evaluator: KillTriggerEvaluator) -> None:
        """When both drawdown and SA-9 fire, the reason must reference drawdown (priority 1)."""
        state = _safe_state(max_drawdown=0.20, sa9_halt=True)
        decision = evaluator.evaluate(state)
        assert decision.triggered is True, (
            "When both drawdown and SA-9 fire, kill must be triggered."
        )
        assert decision.rule == "max_drawdown", (
            f"Drawdown outranks SA-9 when both fire. Got rule {decision.rule!r}."
        )


# ---------------------------------------------------------------------------
# Property tests
# ---------------------------------------------------------------------------


class TestKillTriggerEvaluatorProperties:
    """Structural property tests across the kill threshold matrix."""

    @given(drawdown=st.floats(min_value=0.15, max_value=1.0, allow_nan=False, allow_infinity=False))
    @settings(max_examples=50)
    def test_drawdown_gte_15pct_always_triggers(
        self, drawdown: float, evaluator: KillTriggerEvaluator
    ) -> None:
        """Any drawdown >= 15% must always trigger kill per §10 (property: for all x >= 0.15)."""
        state = _safe_state(max_drawdown=drawdown)
        decision = evaluator.evaluate(state)
        assert decision.triggered is True, (
            f"drawdown={drawdown:.3f} >= 0.15 must always trigger kill. Got triggered=False."
        )

    @given(
        drawdown=st.floats(min_value=0.0, max_value=0.149, allow_nan=False, allow_infinity=False),
        days=st.floats(min_value=0.0, max_value=89.0, allow_nan=False, allow_infinity=False),
        sharpe=st.floats(min_value=0.3, max_value=5.0, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=50)
    def test_below_all_thresholds_never_triggers(
        self,
        drawdown: float,
        days: float,
        sharpe: float,
        evaluator: KillTriggerEvaluator,
    ) -> None:
        """State strictly below all kill thresholds must never trigger kill."""
        state = ExperimentState(
            max_drawdown=drawdown,
            days_since_equity_high=days,
            sa9_halt=False,
            rolling_30d_sharpe=sharpe,
        )
        decision = evaluator.evaluate(state)
        assert decision.triggered is False, (
            f"State below all thresholds (drawdown={drawdown:.3f}, days={days:.1f}, "
            f"sharpe={sharpe:.3f}) must not trigger kill. Got triggered=True."
        )


@pytest.mark.parametrize(
    ("overrides", "rule"),
    [
        ({"max_drawdown": 0.16}, "max_drawdown"),
        ({"sa9_halt": True}, "sa9_halt"),
        ({"days_since_equity_high": 91.0}, "recovery_days"),
        ({"rolling_30d_sharpe": 0.1}, "rolling_sharpe"),
    ],
)
def test_each_criterion_names_its_rule_id(
    evaluator: KillTriggerEvaluator, overrides: dict[str, float | bool], rule: str
) -> None:
    """A fired criterion carries its stable rule id, which value-free records key on (#749)."""
    assert evaluator.evaluate(_safe_state(**overrides)).rule == rule


def test_no_kill_carries_no_rule_id(evaluator: KillTriggerEvaluator) -> None:
    """An untriggered decision names no rule."""
    assert evaluator.evaluate(_safe_state()).rule == ""
