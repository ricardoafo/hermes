"""Tests for the certification record (pre-registration §7, issue #1186).

Invariants:

- A new record starts UNTRUSTED with no fingerprint and no history.
- ``moved_to`` returns a copy in the new state with exactly one transition
  appended, from the old state, and leaves the original untouched.
- A transition history only grows: earlier transitions survive every move.
"""

from datetime import UTC, datetime, timedelta
from itertools import pairwise

from hypothesis import given
from hypothesis import strategies as st

from hermes.domain.calibration.certification import (
    CertificationRecord,
    CertificationState,
    Transition,
)

_AT = datetime(2026, 10, 19, 12, tzinfo=UTC)


def test_a_new_record_starts_untrusted_with_no_history() -> None:
    """Every strategy enters the lifecycle at UNTRUSTED, with nothing certified."""
    record = CertificationRecord("swing-donchian-4h")

    assert (record.state, record.parameter_fingerprint, record.transitions) == (
        CertificationState.UNTRUSTED,
        None,
        (),
    )


def test_moved_to_appends_one_transition_and_leaves_the_original() -> None:
    """A move records where it came from, when, why and on which evidence."""
    record = CertificationRecord("swing-donchian-4h")

    moved = record.moved_to(
        CertificationState.BACKTEST_CERTIFIED,
        at=_AT,
        reason="backtest gate passed",
        fingerprint="f" * 64,
        evidence_sha256="e" * 64,
    )

    assert (moved.state, moved.parameter_fingerprint, moved.transitions, record) == (
        CertificationState.BACKTEST_CERTIFIED,
        "f" * 64,
        (
            Transition(
                CertificationState.UNTRUSTED,
                CertificationState.BACKTEST_CERTIFIED,
                _AT,
                "backtest gate passed",
                "e" * 64,
            ),
        ),
        CertificationRecord("swing-donchian-4h"),
    )


@given(st.lists(st.sampled_from(list(CertificationState)), min_size=1, max_size=8))
def test_the_history_keeps_every_earlier_transition(states: list[CertificationState]) -> None:
    """After any sequence of moves the history is that sequence, in order, chained by state."""
    record = CertificationRecord("swing-donchian-4h")
    for step, state in enumerate(states):
        record = record.moved_to(
            state, at=_AT + timedelta(days=step), reason=f"step {step}", fingerprint=None
        )

    chain = [CertificationState.UNTRUSTED, *states]
    assert [(t.from_state, t.to_state) for t in record.transitions] == list(pairwise(chain))
