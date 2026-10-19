"""Unit tests for the shadow-phase Sharpe kill (pre-registration §10, #422)."""

import math
from datetime import date, timedelta
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hermes.application.strategy_protocol.preregistration import PreRegistration
from hermes.application.strategy_protocol.shadow_sharpe_kill import (
    ShadowSharpeKill,
    rolling_annualised_sharpe,
)

_START = date(2026, 10, 10)


@pytest.fixture
def rule() -> ShadowSharpeKill:
    """Build the rule from the signed §10 values."""
    here = Path(__file__).resolve().parent
    root = next(p for p in [here, *here.parents] if (p / "pyproject.toml").exists())
    return ShadowSharpeKill.from_pre_registration(PreRegistration(project_root=root))


def _feed(rule: ShadowSharpeKill, values: list[float | None]) -> list[bool]:
    return [rule.observe(_START + timedelta(days=i), v).triggered for i, v in enumerate(values)]


def test_signed_values_are_used(rule: ShadowSharpeKill) -> None:
    """§10: floor 0.3, 60 consecutive days."""
    assert (rule.floor, rule.required_days) == (0.3, 60)


# SEV-1: the shadow kill must not stop the 90-day certification early (#422)
def test_fifty_nine_days_below_do_not_fire(rule: ShadowSharpeKill) -> None:
    """59 days below the floor is one day short of the §10 rule."""
    fired = _feed(rule, [0.1] * 59)
    assert (any(fired), rule.streak) == (False, 59)


# SEV-1: the shadow kill must fire on the 60th consecutive day (pre-reg §10)
def test_sixtieth_day_below_fires(rule: ShadowSharpeKill) -> None:
    """The 60th consecutive day below 0.3 kills, and not earlier."""
    fired = _feed(rule, [0.1] * 60)
    assert fired.index(True) == 59


def test_one_day_at_the_floor_resets_the_streak(rule: ShadowSharpeKill) -> None:
    """Exactly 0.3 is not below the floor, so the count restarts."""
    fired = _feed(rule, [0.1] * 59 + [0.3] + [0.1] * 59)
    assert (any(fired), rule.streak) == (False, 59)


def test_day_without_enough_data_resets(rule: ShadowSharpeKill) -> None:
    """A day with no 30-day Sharpe does not count toward the streak."""
    fired = _feed(rule, [0.1] * 30 + [None] + [0.1] * 40)
    assert (any(fired), rule.streak) == (False, 40)


def test_missing_calendar_day_breaks_consecutiveness(rule: ShadowSharpeKill) -> None:
    """Days must be consecutive; a gap restarts the count at 1."""
    for i in range(59):
        rule.observe(_START + timedelta(days=i), 0.1)
    rule.observe(_START + timedelta(days=61), 0.1)
    assert rule.streak == 1


def test_reason_never_carries_the_sharpe_value(rule: ShadowSharpeKill) -> None:
    """Automation stays blind: the kill reason names the rule, not the value (ADR-012 D-06)."""
    for i in range(59):
        rule.observe(_START + timedelta(days=i), 0.123456)
    decision = rule.observe(_START + timedelta(days=59), 0.123456)
    assert decision.triggered
    assert "0.123" not in decision.reason
    assert "60 consecutive days" in decision.reason


def test_days_must_increase(rule: ShadowSharpeKill) -> None:
    """Replaying or reordering days is a caller error."""
    rule.observe(_START, 0.1)
    with pytest.raises(ValueError, match="days must increase"):
        rule.observe(_START, 0.1)


@given(st.lists(st.one_of(st.none(), st.floats(min_value=-5, max_value=5)), max_size=150))
def test_never_fires_without_sixty_days_in_a_row(values: list[float | None]) -> None:
    """For any sequence, a kill implies the previous 60 days were all below 0.3."""
    kill = ShadowSharpeKill(floor=0.3, required_days=60)
    for i, v in enumerate(values):
        if kill.observe(_START + timedelta(days=i), v).triggered:
            window = values[i - 59 : i + 1]
            assert i >= 59
            assert all(x is not None and x < 0.3 for x in window)


def test_rolling_sharpe_uses_last_30_days_and_sqrt_365() -> None:
    """Annualised 30-day Sharpe on [0.01, 0.03] x 15, older data ignored."""
    returns = [-0.5] * 10 + [0.01, 0.03] * 15
    expected = 0.02 / 0.010170952554312157 * math.sqrt(365)
    value = rolling_annualised_sharpe(returns)
    assert value == pytest.approx(expected)


def test_rolling_sharpe_needs_thirty_days() -> None:
    """With 29 returns there is no 30-day Sharpe; with 30 there is."""
    short = rolling_annualised_sharpe([0.01, 0.03] * 14 + [0.01])
    full = rolling_annualised_sharpe([0.01, 0.03] * 15)
    assert (short, full is not None) == (None, True)


def test_the_kill_names_the_shadow_sharpe_rule(rule: ShadowSharpeKill) -> None:
    """The fired decision carries ``shadow_sharpe``, which value-free records key on (#749)."""
    decisions = [rule.observe(_START + timedelta(days=i), 0.1) for i in range(60)]
    assert decisions[-1].rule == "shadow_sharpe"
