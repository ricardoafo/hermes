"""CertificationLifecycle — 6-state certification state machine.

Implements the state machine defined in
``docs/experiments/swing-mvp1/pre-registration-v1.md`` §7 for the
``swing-mvp1-v1`` experiment.

States:

- ``UNTRUSTED`` — default; no evidence. Starting state for every new adapter.
- ``BACKTEST_CERTIFIED`` — every §9 row passes on Train + Val.
- ``SHADOW_CERTIFIED`` — 90+ days of live shadow with sim-vs-live drift p50 < 30 bps.
- ``OOS_CERTIFIED`` — H1 accepted on the OOS window per §9, one shot, not before
  2027-01-01.
- ``LIVE_APPROVED`` — OOS_CERTIFIED plus explicit operator sign-off.
- ``CERTIFIED`` — 90+ days of live capital with no kill trigger fired.
- ``DEAD`` — any kill-trigger fires → absorbing; requires new pre-reg v2.

The ``advance`` method is a pure function: it does not hold mutable state,
performs no I/O, and returns a deterministic next state (or error string) for
any (current_state, CalibrationReport) input pair. This makes it trivially
property-testable across the full 7x6 transition matrix.

Reference: spec-6-calibration-harness/change.md D-3, pre-reg §7.
"""

from decimal import Decimal
from typing import TYPE_CHECKING, ClassVar

from hermes.application.calibration.harness import CalibrationReport
from hermes.domain.calibration.certification import CertificationState

if TYPE_CHECKING:
    from hermes.application.calibration.certification_gates import GateReport

__all__ = ["CertificationLifecycle"]


class CertificationLifecycle:
    """Pure state machine for the certification lifecycle.

    ``advance`` is the only public method. It is a pure function — stateless,
    no I/O. Callers are responsible for persisting the returned state.

    Every forward transition needs a passed
    :class:`~hermes.application.calibration.certification_gates.GateReport` for
    exactly that transition (pre-reg §7 entry criteria). In addition:

    - ``UNTRUSTED → BACKTEST_CERTIFIED``: requires at least one fill in the
      calibration report (``m01_fill_count >= 1``).
    - Any state → ``DEAD``: when ``m05_orphan_position_count > 0`` (SA-4 P0 violation)
      or ``m04_rejection_rate >= 1.0`` (100% rejection — fill model broken).
    - ``DEAD → anything``: always returns an error — absorbing state.

    Reference: spec-6-calibration-harness/change.md D-3, pre-reg §7.
    """

    # Ordered valid forward-progression sequence (excluding DEAD).
    _FORWARD_ORDER: ClassVar[list[CertificationState]] = [
        CertificationState.UNTRUSTED,
        CertificationState.BACKTEST_CERTIFIED,
        CertificationState.SHADOW_CERTIFIED,
        CertificationState.OOS_CERTIFIED,
        CertificationState.LIVE_APPROVED,
        CertificationState.CERTIFIED,
    ]

    def advance(
        self,
        current_state: CertificationState,
        report: CalibrationReport,
        *,
        gate: GateReport,
    ) -> CertificationState | str:
        """Attempt to advance the certification state.

        Returns the next :class:`CertificationState` on a valid transition, or
        a human-readable error string when the transition is illegal or the
        calibration report does not satisfy the preconditions for the next state.

        Callers MUST treat a ``str`` return as a failed transition and keep the
        previous state — the state machine did not advance.

        Args:
            current_state: The current certification state.
            report: The :class:`~hermes.application.calibration.harness.CalibrationReport`
                produced by the calibration harness for the current data window.
            gate: The evaluated gate for the transition out of ``current_state``.

        Returns:
            :class:`CertificationState` — the next state on a valid transition.
            ``str`` — human-readable error on an illegal or precondition-failed
            transition. Callers must NOT parse this string; it is for operators only.

        Reference: spec-6-calibration-harness/change.md R-3, pre-reg §7.
        """
        # DEAD is absorbing — no transition possible.
        if current_state == CertificationState.DEAD:
            return (
                "CertificationLifecycle: DEAD state is absorbing — no advance possible. "
                "A new pre-registration (v2) is required to restart the lifecycle. "
                "Pre-reg §7."
            )

        # Check kill triggers that send any state to DEAD immediately.
        kill_error = self._check_kill_conditions(report)
        if kill_error:
            return kill_error

        # CERTIFIED is the terminal success state.
        if current_state == CertificationState.CERTIFIED:
            return (
                "CertificationLifecycle: already CERTIFIED — no further advance. "
                "The lifecycle is complete. Pre-reg §7."
            )

        # Resolve next state in the forward order.
        try:
            current_idx = self._FORWARD_ORDER.index(current_state)
        except ValueError:
            return (
                f"CertificationLifecycle: unknown state {current_state!r}. "
                "Cannot determine next state."
            )

        next_state = self._FORWARD_ORDER[current_idx + 1]

        # State-specific preconditions.
        error = self._check_precondition(current_state, next_state, report) or self._check_gate(
            current_state, next_state, gate
        )
        return error or next_state

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _check_kill_conditions(report: CalibrationReport) -> str:
        """Return an error string if any kill condition is met, else empty string.

        Kill conditions that transition any state to DEAD:
        - M-05 orphan position count > 0 (SA-4 P0 violation).
        - M-04 rejection rate == 1.0 (all fill attempts rejected — fill model broken).

        Args:
            report: Calibration report to check.

        Returns:
            Non-empty error string when a kill condition fires, else ``""``.
        """
        if report.m05_orphan_position_count > 0:
            return (
                f"CertificationLifecycle: SA-4 P0 violation — "
                f"{report.m05_orphan_position_count} orphan position(s) detected. "
                "Every open position must trace to a fill event. "
                "Transition blocked; state must be set to DEAD. Pre-reg §7."
            )
        if report.m04_rejection_rate >= Decimal("1"):
            return (
                "CertificationLifecycle: fill model completely broken — "
                f"rejection rate={report.m04_rejection_rate} (100%). "
                "No fills produced. Transition blocked; state must be set to DEAD. "
                "Pre-reg §7."
            )
        return ""

    @staticmethod
    def _check_precondition(
        current_state: CertificationState,
        next_state: CertificationState,
        report: CalibrationReport,
    ) -> str:
        """Return an error string if the precondition for the transition is not met.

        Args:
            current_state: State we are transitioning FROM.
            next_state: State we are transitioning TO.
            report: Calibration report to evaluate.

        Returns:
            Non-empty error string when precondition fails, else ``""``.
        """
        if current_state == CertificationState.UNTRUSTED and report.m01_fill_count < 1:
            # UNTRUSTED → BACKTEST_CERTIFIED requires at least one fill.
            return (
                "CertificationLifecycle: cannot advance UNTRUSTED → BACKTEST_CERTIFIED. "
                f"m01_fill_count={report.m01_fill_count} — at least 1 fill required "
                "to certify backtest evidence. Pre-reg §7."
            )
        return ""

    @staticmethod
    def _check_gate(
        current_state: CertificationState,
        next_state: CertificationState,
        gate: GateReport,
    ) -> str:
        """Return an error string unless ``gate`` passed for this exact transition.

        Args:
            current_state: State we are transitioning FROM.
            next_state: State we are transitioning TO.
            gate: The evaluated gate report.

        Returns:
            Non-empty error string when the gate does not authorise the move.
        """
        if gate.from_state != current_state or gate.to_state != next_state:
            return (
                f"CertificationLifecycle: gate report is for {gate.from_state} → "
                f"{gate.to_state}, not {current_state} → {next_state}. Pre-reg §7."
            )
        if gate.refused_reason:
            return f"CertificationLifecycle: gate refused: {gate.refused_reason}"
        if not gate.passed:
            return (
                f"CertificationLifecycle: cannot advance {current_state} → {next_state}; "
                f"failed checks: {', '.join(gate.failed_checks) or 'none evaluated'}. "
                "Pre-reg §7."
            )
        return ""

    def force_dead(
        self,
        current_state: CertificationState,
        reason: str,
    ) -> CertificationState:
        """Unconditionally transition to DEAD with a logged reason.

        Use this when an external kill trigger (from
        :class:`~hermes.application.strategy_protocol.kill_trigger_evaluator.KillTriggerEvaluator`)
        fires — the trigger evaluator already determined the reason; this method
        records the transition.

        ``DEAD → DEAD`` is a no-op (already in the absorbing state).

        Args:
            current_state: Current certification state.
            reason: Human-readable reason for the kill (from the kill trigger
                evaluator). Must be non-empty.

        Returns:
            :class:`CertificationState.DEAD` always.

        Raises:
            ValueError: When ``reason`` is empty.
        """
        if not reason.strip():
            raise ValueError(
                "CertificationLifecycle.force_dead: reason must be non-empty — "
                "provide the kill trigger reason for operator audit."
            )
        _ = current_state  # acknowledged: from-state is logged externally
        return CertificationState.DEAD
