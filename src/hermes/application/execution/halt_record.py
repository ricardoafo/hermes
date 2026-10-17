"""Turn an execution outcome into the value-free record of a halt or a kill (#749).

The kernel answers an entry signal with ``Declined(rule="HALT")`` when a risk
overlay rule halts it, and with ``Halted`` when a pre-registration §10 kill
criterion fires. Both used to leave one log line only. :func:`halt_record`
builds the :class:`RiskHaltRecorded` serve persists and announces; every other
outcome yields ``None``.

Record-only: nothing here changes whether or when a halt or a kill fires.
The reason comes from a fixed table, never from the rule's own text, which can
quote drawdowns and prices (ADR-012 D-06).

:func:`shadow_check_records` builds the strategy-wide rows of serve's daily
shadow checks the same way (#796).

Reference: GitHub issues #749 and #796; pre-registration-v1.md §5.5, §7 and §10.
"""

from datetime import date, datetime
from typing import Final

from hermes.application.execution.kernel import Declined, ExecutionOutcome, Halted
from hermes.application.execution.shadow import AlreadyFilled
from hermes.application.order_router.router import value_free_reason
from hermes.application.strategy_protocol.shadow_checks import ShadowCheckResult, day_end
from hermes.domain.events.risk_halt_recorded import HaltKind, RiskHaltRecorded
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.strategy_id import StrategyId

__all__ = ["DRIFT_RULE", "KILL_REASONS", "halt_record", "shadow_check_records"]

_OVERLAY_HALT: Final = "HALT"
"""``Declined.rule`` of an entry the risk overlay halted."""

_KILL_SECTION: Final = "§10"
_OVERLAY_SECTIONS: Final[dict[str, str]] = {"RO-01": "§5.5"}
"""Overlay rules with a section of their own; the other SA-9 rules count under §10."""

KILL_REASONS: Final[dict[str, str]] = {
    "max_drawdown": "drawdown_kill_threshold",
    "sa9_halt": "sa9_p0_rule_fired",
    "recovery_days": "recovery_past_kill_limit",
    "rolling_sharpe": "rolling_sharpe_below_floor",
    "shadow_sharpe": "shadow_sharpe_below_floor_streak",
    "sa9_repeat": "sa9_rule_fired_repeatedly",
}
"""Value-free reason of each §10 kill rule, keyed by ``KillDecision.rule``."""

_UNKNOWN_KILL: Final = "unclassified"

DRIFT_RULE: Final = "sim_live_drift"
"""Rule id of a drift breach row (§7, §10 shadow phase)."""

_DRIFT_REASON: Final = "drift_p50_above_limit"
_DRIFT_SECTION: Final = "§7"


def halt_record(
    outcome: ExecutionOutcome | AlreadyFilled,
    *,
    strategy_id: StrategyId,
    pair: Pair,
    at: datetime,
    correlation_id: str,
) -> RiskHaltRecorded | None:
    """Return the record of a halted or killed entry, or ``None`` for any other outcome.

    Args:
        outcome: What the kernel did with the signal.
        strategy_id: The strategy that emitted the signal.
        pair: The signal's pair.
        at: The signal time, stamped as ``occurred_at`` so a re-evaluated bar
            yields the same record.
        correlation_id: The signal's correlation id.

    Returns:
        A :class:`RiskHaltRecorded` for an overlay HALT or a §10 kill, else ``None``.
    """
    match outcome:
        case Halted(breaker=breaker):
            rule = breaker.decision.rule or "unknown"
            kind = HaltKind.KILL
            reason = KILL_REASONS.get(rule, _UNKNOWN_KILL)
            section: str | None = _KILL_SECTION
        case Declined(rule=declined_rule, reason=text) if declined_rule == _OVERLAY_HALT:
            rule, reason = value_free_reason(text)
            kind = HaltKind.HALT
            section = None if rule == "unknown" else _OVERLAY_SECTIONS.get(rule, _KILL_SECTION)
        case _:
            return None
    return RiskHaltRecorded(
        occurred_at=at,
        correlation_id=correlation_id,
        strategy_id=strategy_id,
        pair=pair,
        kind=kind,
        rule=rule,
        reason=reason,
        pre_registration_ref=section,
    )


def shadow_check_records(
    result: ShadowCheckResult,
    *,
    day: date,
    strategy_id: StrategyId,
    record_kill: bool,
    correlation_id: str,
) -> tuple[RiskHaltRecorded, ...]:
    """Return the strategy-wide rows one day's shadow checks call for (#796).

    Args:
        result: What :func:`run_shadow_checks` found for ``day``.
        day: The UTC day checked; the rows are stamped at its end, so checking
            the day again after a restart yields the same rows.
        strategy_id: The strategy checked.
        record_kill: ``False`` once a kill is already held, so it is recorded once.
        correlation_id: Correlation id of the check.

    Returns:
        A kill row, a drift row, both or none.
    """
    rows: list[RiskHaltRecorded] = []
    if record_kill and result.kill.triggered:
        rows.append(
            RiskHaltRecorded(
                occurred_at=day_end(day),
                correlation_id=correlation_id,
                strategy_id=strategy_id,
                pair=None,
                kind=HaltKind.KILL,
                rule=result.kill.rule,
                reason=KILL_REASONS.get(result.kill.rule, _UNKNOWN_KILL),
                pre_registration_ref=_KILL_SECTION,
            )
        )
    if result.drift_breached:
        rows.append(
            RiskHaltRecorded(
                occurred_at=day_end(day),
                correlation_id=correlation_id,
                strategy_id=strategy_id,
                pair=None,
                kind=HaltKind.DRIFT,
                rule=DRIFT_RULE,
                reason=_DRIFT_REASON,
                pre_registration_ref=_DRIFT_SECTION,
            )
        )
    return tuple(rows)
