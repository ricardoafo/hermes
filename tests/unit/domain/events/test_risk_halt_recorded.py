"""Unit tests for the ``RiskHaltRecorded`` event (#749).

Invariant under test: the value-free fields refuse free text, so no caller can
record a drawdown, a price or a Sharpe in a halt row.
"""

from datetime import UTC, datetime

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hermes.domain.events.risk_halt_recorded import HaltKind, RiskHaltRecorded
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.strategy_id import StrategyId

_AT = datetime(2026, 10, 17, 8, tzinfo=UTC)


def _record(
    *, rule: str = "RO-08", reason: str = "drawdown_limit", ref: str | None = "§10"
) -> RiskHaltRecorded:
    return RiskHaltRecorded(
        occurred_at=_AT,
        correlation_id="01JABCDEFGHJKMNPQRSTVWXYZ0",
        strategy_id=StrategyId("swing-donchian-4h"),
        pair=Pair(base=Currency.BTC, quote=Currency.USDT),
        kind=HaltKind.HALT,
        rule=rule,
        reason=reason,
        pre_registration_ref=ref,
    )


def test_value_free_fields_are_kept_as_given() -> None:
    """A rule id, a snake_case reason and a section build an event that keeps them."""
    record = _record(rule="max_drawdown", reason="drawdown_kill_threshold", ref="§5.5")
    assert (record.rule, record.reason, record.pre_registration_ref) == (
        "max_drawdown",
        "drawdown_kill_threshold",
        "§5.5",
    )


def test_a_reason_quoting_a_figure_is_refused() -> None:
    """The rule's own text, which quotes the drawdown, cannot be recorded as the reason."""
    with pytest.raises(ValueError, match="reason"):
        _record(reason="RO-08: drawdown 12.5% >= 10%")


def test_a_free_text_rule_is_refused() -> None:
    """The rule field takes an id, not a sentence."""
    with pytest.raises(ValueError, match="rule"):
        _record(rule="Max drawdown 16%")


def test_a_reference_not_naming_a_section_is_refused() -> None:
    """The citation reads ``§N``; anything else is refused."""
    with pytest.raises(ValueError, match="pre_registration_ref"):
        _record(ref="see the doc")


@given(st.text(), st.sampled_from([" ", "%", ".", "-", "$"]), st.text())
def test_no_text_with_spaces_or_figures_passes_as_a_reason(head: str, mark: str, tail: str) -> None:
    """Any reason holding a space, a percent sign, a decimal point or a sign is refused."""
    with pytest.raises(ValueError, match="reason"):
        _record(reason=f"{head}{mark}{tail}")
