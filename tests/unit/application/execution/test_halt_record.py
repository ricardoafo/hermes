"""Unit tests for :func:`halt_record` (#749).

Invariants under test:

- an overlay HALT and a §10 kill each yield one record of their kind, with
  the rule id and a fixed reason, never the rule's own text;
- every other outcome yields no record;
- a day's shadow checks yield strategy-wide kill and drift rows stamped at
  the day's end, the kill only while it is not yet held (#796).
"""

from datetime import UTC, date, datetime

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hermes.application.execution.halt_record import (
    DRIFT_RULE,
    KILL_REASONS,
    halt_record,
    shadow_check_records,
)
from hermes.application.execution.kernel import Declined, Halted
from hermes.application.order_router.router import KillCircuitBreaker
from hermes.application.strategy_protocol.kill_trigger_evaluator import KillDecision
from hermes.application.strategy_protocol.shadow_checks import ShadowCheckResult
from hermes.domain.events.risk_halt_recorded import HaltKind, RiskHaltRecorded
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.strategy_id import StrategyId

_AT = datetime(2026, 10, 17, 8, tzinfo=UTC)
_PAIR = Pair(base=Currency.ETH, quote=Currency.USDT)
_STRATEGY = StrategyId("swing-donchian-4h")
_CID = "01JABCDEFGHJKMNPQRSTVWXYZ0"


def _record(outcome: Declined | Halted) -> RiskHaltRecorded | None:
    return halt_record(outcome, strategy_id=_STRATEGY, pair=_PAIR, at=_AT, correlation_id=_CID)


def test_an_overlay_halt_records_its_rule_and_fixed_reason() -> None:
    """``Declined(rule="HALT")`` from RO-08 records a halt citing §10, without the figures."""
    record = _record(Declined(reason="RO-08: drawdown 11.2% exceeds 10.0%", rule="HALT"))
    assert record is not None
    assert (record.kind, record.rule, record.reason, record.pre_registration_ref) == (
        HaltKind.HALT,
        "RO-08",
        "drawdown_limit",
        "§10",
    )


def test_the_correlation_lock_cites_its_own_section() -> None:
    """RO-01 comes from §5.5, not §10."""
    record = _record(Declined(reason="RO-01: correlation 0.91 above 0.80", rule="HALT"))
    assert record is not None
    assert record.pre_registration_ref == "§5.5"


def test_an_unclassified_halt_records_unknown_without_a_citation() -> None:
    """A HALT whose text names no known rule is still recorded, as unknown."""
    record = _record(Declined(reason="SA-6: capital budget exhausted", rule="HALT"))
    assert record is not None
    assert (record.rule, record.reason, record.pre_registration_ref) == (
        "unknown",
        "unclassified",
        None,
    )


@pytest.mark.parametrize("rule", sorted(KILL_REASONS))
def test_each_kill_rule_records_a_kill_with_its_fixed_reason(rule: str) -> None:
    """A ``Halted`` outcome records a kill, its rule id and the table's reason, citing §10."""
    decision = KillDecision(triggered=True, reason="Max drawdown 16.0% >= 15.0%", rule=rule)
    record = _record(Halted(breaker=KillCircuitBreaker(decision=decision)))
    assert record is not None
    assert (record.kind, record.rule, record.reason, record.pre_registration_ref) == (
        HaltKind.KILL,
        rule,
        KILL_REASONS[rule],
        "§10",
    )


def test_the_record_carries_the_signal_time_and_ids() -> None:
    """``occurred_at`` is the signal time, so a re-evaluated bar yields an equal record."""
    first = _record(Declined(reason="RO-04: book 900 s old", rule="HALT"))
    second = _record(Declined(reason="RO-04: book 901 s old", rule="HALT"))
    assert first is not None
    assert (first.occurred_at, first.strategy_id, first.pair, first.correlation_id) == (
        _AT,
        _STRATEGY,
        _PAIR,
        _CID,
    )
    assert first == second


@given(st.sampled_from(["no-book", "no-position", "fill_model", "portfolio", "sizing", "RO-03"]))
def test_a_decline_that_is_not_an_overlay_halt_records_nothing(rule: str) -> None:
    """Guards, fill rejections and sizing failures are not halts."""
    assert (rule, _record(Declined(reason="RO-08: drawdown 11%", rule=rule))) == (rule, None)


_DAY = date(2026, 12, 1)
_DAY_END = datetime(2026, 12, 2, tzinfo=UTC)
_KILL = KillDecision(triggered=True, reason="Shadow Sharpe below the floor.", rule="shadow_sharpe")
_NO_KILL = KillDecision(triggered=False, reason="")


def _rows(
    result: ShadowCheckResult, *, record_kill: bool = True
) -> tuple[tuple[HaltKind, str, str, str | None, Pair | None, datetime], ...]:
    """Return the shape of each row :func:`shadow_check_records` builds for ``result``."""
    return tuple(
        (r.kind, r.rule, r.reason, r.pre_registration_ref, r.pair, r.occurred_at)
        for r in shadow_check_records(
            result,
            day=_DAY,
            strategy_id=_STRATEGY,
            record_kill=record_kill,
            correlation_id=_CID,
        )
    )


def test_a_daily_kill_and_drift_breach_record_strategy_wide_rows_at_the_day_end() -> None:
    """Both fire: a kill row citing §10 and a drift row citing §7, no pair, stamped at midnight."""
    assert _rows(ShadowCheckResult(kill=_KILL, drift_breached=True)) == (
        (HaltKind.KILL, "shadow_sharpe", "shadow_sharpe_below_floor_streak", "§10", None, _DAY_END),
        (HaltKind.DRIFT, DRIFT_RULE, "drift_p50_above_limit", "§7", None, _DAY_END),
    )


def test_a_kill_already_held_is_not_recorded_again() -> None:
    """With the kill held, only the drift row is built."""
    rows = _rows(ShadowCheckResult(kill=_KILL, drift_breached=True), record_kill=False)
    assert [kind for kind, *_ in rows] == [HaltKind.DRIFT]


def test_a_clean_day_records_nothing() -> None:
    """No kill and no breach build no row."""
    assert _rows(ShadowCheckResult(kill=_NO_KILL, drift_breached=False)) == ()


def test_the_sa9_repeat_kill_has_a_fixed_reason() -> None:
    """The SA-9 repeat kill maps to its own value-free reason, not ``unclassified``."""
    kill = KillDecision(triggered=True, reason="SA-9 rule RO-04 fired.", rule="sa9_repeat")
    rows = _rows(ShadowCheckResult(kill=kill, drift_breached=False))
    assert [reason for _, _, reason, *_ in rows] == ["sa9_rule_fired_repeatedly"]
