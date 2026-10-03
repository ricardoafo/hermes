"""KillTriggerEvaluator — §10 kill-criteria evaluator for running experiment state.

Evaluates a snapshot of running experiment state against the binding kill criteria
in ``docs/experiments/swing-mvp1/pre-registration-v1.md`` §10 and returns a
:class:`KillDecision` with a human-readable reason string.

Kill triggers (§10 live phase):
    - Max drawdown >= 15% peak-to-trough on experiment capital
    - Time-to-recovery > 90 days from any drawdown >= 8%
    - SA-9 P0 rule fires HALT signal
    - 30 rolling trades OOS Sharpe < 0.3

This evaluator applies the live-phase rules. The shadow-phase Sharpe rule
("< 0.3 for 60 consecutive days") needs a streak across days, so it lives in
:class:`~hermes.application.strategy_protocol.shadow_sharpe_kill.ShadowSharpeKill`;
using this evaluator's Sharpe check in shadow would kill up to 60 days early
(#422).

The evaluator is stateless — it holds no mutable state between calls. Thresholds
are injected at construction time via
:class:`~hermes.application.strategy_protocol.preregistration.PreRegistration`.

Reference:
  pre-registration-v1.md §10.
  spec-9-strategy-development-protocol change.md (KillDecision design).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from hermes.application.strategy_protocol.preregistration import (
    KillTriggers,
    PreRegistration,
)

__all__ = [
    "ExperimentState",
    "KillDecision",
    "KillTriggerEvaluator",
]


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ExperimentState:
    """Snapshot of running experiment state for kill-trigger evaluation.

    Args:
        max_drawdown: Current peak-to-trough drawdown fraction on experiment
            capital (e.g. 0.12 = 12%). Must be >= 0.
        days_since_equity_high: Number of calendar days since the last equity
            high-water mark. Used for the 90-day recovery trigger.
        sa9_halt: True if any SA-9 P0 rule has fired HALT (depeg >= 3%,
            spread spike >= 10x, exchange downtime > 5 min during open position,
            correlation lock breach, stale-book detection).
        rolling_30d_sharpe: 30-rolling-trade Sharpe estimate. ``None`` when
            fewer than 30 OOS trades exist (insufficient sample — not evaluated).

    Reference: pre-registration-v1.md §10.
    """

    max_drawdown: float
    days_since_equity_high: float
    sa9_halt: bool
    rolling_30d_sharpe: float | None = field(default=None)


@dataclass(frozen=True, slots=True)
class KillDecision:
    """Result of the kill-trigger evaluation.

    Args:
        triggered: True iff at least one kill criterion has fired.
        reason: Human-readable description of the first criterion that fired.
            Empty string when ``triggered=False``. Where multiple criteria fire
            simultaneously, only the first matched criterion is reported — the
            caller should treat this as "kill immediately, reason logged".

    Reference: pre-registration-v1.md §10.
    """

    triggered: bool
    reason: str


# ---------------------------------------------------------------------------
# Evaluator
# ---------------------------------------------------------------------------


class KillTriggerEvaluator:
    """Evaluates :class:`ExperimentState` against §10 kill criteria.

    Instantiate once with a
    :class:`~hermes.application.strategy_protocol.preregistration.PreRegistration`
    instance; then call :meth:`evaluate` for each state snapshot (e.g. once per
    bar close during the live or shadow phase).

    Kill trigger evaluation order (first match wins):
        1. Max drawdown >= threshold
        2. SA-9 HALT signal
        3. Days since equity high >= recovery threshold
        4. 30-day rolling Sharpe < rolling_sharpe_min (when sample is sufficient)

    Args:
        pre_reg: Pre-registration instance providing §10 kill thresholds.

    Reference: pre-registration-v1.md §10, spec-9 change.md.
    """

    def __init__(self, pre_reg: PreRegistration) -> None:
        """Initialise the evaluator with kill thresholds from the signed pre-registration.

        Args:
            pre_reg: Pre-registration accessor providing §10 kill thresholds.
        """
        self._triggers: KillTriggers = pre_reg.kill_triggers()

    def evaluate(self, state: ExperimentState) -> KillDecision:
        """Evaluate a :class:`ExperimentState` snapshot against §10 kill criteria.

        Checks each trigger in priority order and returns on the first match.
        This ensures a deterministic single ``reason`` in the :class:`KillDecision`
        even when multiple criteria fire simultaneously.

        Args:
            state: Current experiment state snapshot from the monitoring loop.

        Returns:
            :class:`KillDecision` with ``triggered=True`` and ``reason`` on the
            first matched kill criterion, or ``triggered=False`` with ``reason=""``
            when no criterion fires.

        Reference: pre-registration-v1.md §10.
        """
        t = self._triggers

        # 1. Max drawdown >= 15% peak-to-trough
        if state.max_drawdown >= t.max_drawdown_threshold:
            return KillDecision(
                triggered=True,
                reason=(
                    f"Max drawdown {state.max_drawdown:.1%} >= "
                    f"kill threshold {t.max_drawdown_threshold:.1%} "
                    f"(pre-reg §10)."
                ),
            )

        # 2. SA-9 P0 HALT signal
        if state.sa9_halt:
            return KillDecision(
                triggered=True,
                reason=(
                    "SA-9 P0 HALT signal fired. "
                    "See SA-9 rule log for specific trigger (depeg, spread spike, "
                    "correlation lock breach, stale book, or exchange downtime). "
                    "Pre-reg §10."
                ),
            )

        # 3. Recovery period > 90 days
        if state.days_since_equity_high >= t.recovery_days_threshold:
            return KillDecision(
                triggered=True,
                reason=(
                    f"Recovery period {state.days_since_equity_high:.0f} days >= "
                    f"{t.recovery_days_threshold:.0f}-day kill threshold "
                    f"(pre-reg §10)."
                ),
            )

        # 4. 30-day rolling Sharpe < 0.3 (only evaluated when sample >= 30 trades)
        if state.rolling_30d_sharpe is not None and state.rolling_30d_sharpe < t.rolling_sharpe_min:
            return KillDecision(
                triggered=True,
                reason=(
                    f"30-rolling-trade Sharpe {state.rolling_30d_sharpe:.3f} < "
                    f"kill floor {t.rolling_sharpe_min:.1f} "
                    f"(pre-reg §10)."
                ),
            )

        return KillDecision(triggered=False, reason="")
