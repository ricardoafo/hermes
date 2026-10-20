"""Tests for the bot's allowlist and rate limit (ADR-015 D-04, D-08).

Invariants protected here:

- only the configured chat is answered; any other chat is refused;
- one reply per command per 5 s; a dropped request does not restart the window;
- different commands have independent windows.
"""

from datetime import UTC, datetime, timedelta

from hermes.application.bot.gate import CommandGate, GateDecision

_T0 = datetime(2026, 10, 4, 9, tzinfo=UTC)
_OPERATOR = "-1001234567890"


def test_a_foreign_chat_is_refused() -> None:
    """A stranger who found the bot gets nothing."""
    gate = CommandGate(allowed_chat_id=_OPERATOR)

    assert gate.decide("42", "status", _T0) is GateDecision.FOREIGN_CHAT


def test_the_operator_is_answered() -> None:
    """The configured chat gets a reply."""
    gate = CommandGate(allowed_chat_id=_OPERATOR)

    assert gate.decide(_OPERATOR, "status", _T0) is GateDecision.ANSWER


def test_a_repeat_inside_five_seconds_is_dropped_and_after_is_answered() -> None:
    """At 4.9 s the repeat is dropped; at 5 s from the first reply it is answered."""
    gate = CommandGate(allowed_chat_id=_OPERATOR)
    gate.decide(_OPERATOR, "status", _T0)

    decisions = (
        gate.decide(_OPERATOR, "status", _T0 + timedelta(seconds=4.9)),
        gate.decide(_OPERATOR, "status", _T0 + timedelta(seconds=5)),
    )

    assert decisions == (GateDecision.RATE_LIMITED, GateDecision.ANSWER)


def test_commands_have_independent_windows() -> None:
    """Asking /status does not block an immediate /money."""
    gate = CommandGate(allowed_chat_id=_OPERATOR)
    gate.decide(_OPERATOR, "status", _T0)

    assert gate.decide(_OPERATOR, "money", _T0) is GateDecision.ANSWER
