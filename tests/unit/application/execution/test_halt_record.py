"""Unit tests for :func:`halt_record` (#749).

Invariants under test:

- an overlay HALT and a §10 kill each yield one record of their kind, with
  the rule id and a fixed reason, never the rule's own text;
- every other outcome yields no record.
"""

from datetime import UTC, datetime

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hermes.application.execution.halt_record import KILL_REASONS, halt_record
from hermes.application.execution.kernel import Declined, Halted
from hermes.application.order_router.router import KillCircuitBreaker
from hermes.application.strategy_protocol.kill_trigger_evaluator import KillDecision
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
