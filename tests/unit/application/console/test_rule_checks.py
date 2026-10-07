"""Unit tests for the Signals screen's rule checks read from the journal (#714).

Invariants:

- each yes/no entry field of a bar line lands on its own rule, unchanged;
- a check is a signal exactly when all three rules held;
- a coin checked twice for one candle (a restart) keeps its latest line;
- bar lines without the entry fields, and checks before ``since``, are left out;
- checks come newest candle first, then by coin;
- a rule check carries no number: no price, indicator value or distance.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta

from hypothesis import given
from hypothesis import strategies as st

from hermes.application.console.journal_activity import rule_checks
from hermes.application.console.logs import LogLine
from hermes.application.console.views import RULE_COUNT, RuleCheck

_AT = datetime(2026, 10, 3, 12, tzinfo=UTC)


def _yn(value: bool) -> str:
    return "yes" if value else "no"


def _bar(
    at: datetime,
    pair: str = "BTC/USDT",
    *,
    rules: tuple[bool, bool, bool] = (False, False, False),
    logged: datetime | None = None,
) -> LogLine:
    breakout, momentum, regime = rules
    return LogLine(
        at=logged or at,
        event="serve.bar.processed",
        fields=(
            ("pair", pair),
            ("interval", "4h"),
            ("close_time", at.isoformat()),
            ("entry_breakout", _yn(breakout)),
            ("entry_momentum", _yn(momentum)),
            ("entry_regime", _yn(regime)),
        ),
    )


@given(rules=st.tuples(st.booleans(), st.booleans(), st.booleans()))
def test_each_entry_field_lands_on_its_own_rule(rules: tuple[bool, bool, bool]) -> None:
    """INV: breakout, momentum and regime are read from their own fields, whatever they say."""
    (check,) = rule_checks([_bar(_AT, rules=rules)])

    assert (check.breakout, check.momentum, check.regime) == rules
    assert check.met == sum(rules)
    assert check.signal is (sum(rules) == RULE_COUNT)


def test_the_check_names_the_coin_interval_and_candle() -> None:
    """INV: the pair's base asset, the interval and the candle boundary identify a check."""
    (check,) = rule_checks([_bar(_AT, "ETH/USDT")])

    assert (check.coin, check.interval, check.at) == ("ETH", "4h", _AT)


def test_a_restart_keeps_the_latest_line_of_a_candle() -> None:
    """INV: two lines for one coin and candle give one check, the one logged last."""
    first = _bar(_AT, rules=(True, False, False), logged=_AT + timedelta(seconds=5))
    again = _bar(_AT, rules=(True, True, True), logged=_AT + timedelta(minutes=3))

    (check,) = rule_checks([again, first])

    assert check.signal


def test_a_bar_line_without_entry_fields_is_not_a_check() -> None:
    """INV: an in-position or daily bar line carries no entry fields and adds no check."""
    line = LogLine(
        at=_AT,
        event="serve.bar.processed",
        fields=(("pair", "BTC/USDT"), ("interval", "4h"), ("close_time", _AT.isoformat())),
    )

    assert rule_checks([line]) == ()


def test_other_events_and_bad_close_times_are_not_checks() -> None:
    """INV: only bar lines with a readable close time count."""
    other = LogLine(at=_AT, event="serve.run.started", fields=(("release", "a"),))
    broken = LogLine(
        at=_AT,
        event="serve.bar.processed",
        fields=(
            ("pair", "BTC/USDT"),
            ("close_time", "yesterday"),
            ("entry_breakout", "yes"),
            ("entry_momentum", "yes"),
            ("entry_regime", "yes"),
        ),
    )

    assert rule_checks([other, broken]) == ()


def test_checks_before_since_are_left_out() -> None:
    """INV: a candle that closed before ``since`` is not on the screen."""
    old, new = _bar(_AT - timedelta(days=8)), _bar(_AT)

    assert [c.at for c in rule_checks([old, new], since=_AT - timedelta(days=7))] == [_AT]


def test_checks_come_newest_first_then_by_coin() -> None:
    """INV: the newest candle leads, and coins of one candle are in name order."""
    earlier = _AT - timedelta(hours=4)
    lines = [_bar(earlier, "BTC/USDT"), _bar(_AT, "SOL/USDT"), _bar(_AT, "BTC/USDT")]

    assert [(c.at, c.coin) for c in rule_checks(lines)] == [
        (_AT, "BTC"),
        (_AT, "SOL"),
        (earlier, "BTC"),
    ]


def test_a_rule_check_holds_no_number() -> None:
    """INV: a check has no field that could carry a price or a distance (ADR-012 D-06)."""
    kinds = {field.name: field.type for field in dataclasses.fields(RuleCheck)}

    assert kinds == {
        "at": "datetime",
        "coin": "str",
        "interval": "str",
        "breakout": "bool",
        "momentum": "bool",
        "regime": "bool",
    }
