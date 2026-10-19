"""The "checking" spinner and the new row's slide-in render as specified (issue #721).

Invariants:

- the status row shows the spinner only while a due check is under a minute
  overdue and the heartbeat is OK: at the due instant yes, 59 s late yes,
  60 s late no, before it is due no, and never in LATE or DOWN;
- a reveal share shows that fraction of the row's text, rounded up, left to
  right, and a row without a share is drawn whole.
"""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from fractions import Fraction

from hypothesis import given
from hypothesis import strategies as st

from hermes.application.console.render import (
    RenderContext,
    is_checking,
    render_activity,
    render_heartbeat,
    to_plain_text,
)
from hermes.application.console.views import (
    ActivityDay,
    ActivityItem,
    ActivityKind,
    Heartbeat,
    HeartbeatState,
)
from hermes.application.console.visibility import Surface

_DUE = datetime(2026, 10, 19, 16, 0, tzinfo=UTC)


def _heartbeat(state: HeartbeatState = HeartbeatState.OK) -> Heartbeat:
    down = _DUE if state is HeartbeatState.DOWN else None
    return Heartbeat(
        state=state,
        as_of=_DUE,
        last_check_at=_DUE - timedelta(hours=4),
        next_check_at=_DUE,
        down_since=down,
    )


def test_checking_runs_from_the_due_instant_to_just_under_a_minute() -> None:
    """Before due: no. At due and 59 s late: yes. At 60 s: no."""
    beat = _heartbeat()
    verdicts = [is_checking(beat, _DUE + timedelta(seconds=s)) for s in (-1, 0, 59, 60, 61)]
    assert verdicts == [False, True, True, False, False]


def test_checking_never_shows_for_a_late_or_down_heartbeat() -> None:
    """LATE and DOWN keep their own rows even inside the first minute."""
    now = _DUE + timedelta(seconds=10)
    assert [
        is_checking(_heartbeat(state), now) for state in (HeartbeatState.LATE, HeartbeatState.DOWN)
    ] == [False, False]


def test_the_checking_row_names_the_bar_and_leads_with_the_spinner_frame() -> None:
    """The row reads "<frame> checking 16:00 bar…" in UTC with the spinner frame first."""
    ctx = replace(
        RenderContext(surface=Surface.OPERATOR_TUI, now=_DUE + timedelta(seconds=5), tz=UTC),
        spinner="⠹",
    )
    text = to_plain_text(render_heartbeat(_heartbeat(), ctx), width=80)
    assert text.strip() == "⠹ checking 16:00 bar…"


def test_the_ok_row_is_unchanged_once_the_minute_has_passed() -> None:
    """At 60 s late the row is the plain live line, with no spinner."""
    ctx = RenderContext(surface=Surface.OPERATOR_TUI, now=_DUE + timedelta(seconds=60), tz=UTC)
    text = to_plain_text(render_heartbeat(_heartbeat(), ctx), width=80)
    assert text.strip().startswith("● live")


_ITEM = ActivityItem(at=_DUE, kind=ActivityKind.OPENED, coin="ETH")
_DAYS = (ActivityDay(_DUE.date(), (_ITEM,)),)
_CTX = RenderContext(surface=Surface.OPERATOR_TUI, now=_DUE, tz=UTC)


def _row(share: Fraction | None) -> str:
    reveal = None if share is None else {_ITEM: share}
    return to_plain_text(render_activity(_DAYS, _CTX, icons=True, reveal=reveal), width=80)


def test_a_third_and_two_thirds_of_opened_eth_show_four_and_seven_characters() -> None:
    """The 10-character row text shows 4 characters at 1/3 and 7 at 2/3 (rounded up)."""
    shown = [
        _row(share).splitlines()[-1].split("  ")[-1].strip()
        for share in (Fraction(1, 3), Fraction(2, 3), Fraction(1))
    ]
    assert shown == ["open", "opened", "opened ETH"]


def test_a_row_without_a_share_is_drawn_whole() -> None:
    """No reveal entry for the row means its whole text."""
    assert _row(None) == _row(Fraction(1))


@given(st.fractions(min_value=Fraction(1, 10), max_value=Fraction(1)))
def test_a_reveal_never_shows_more_than_the_whole_row(share: Fraction) -> None:
    """Whatever the share, the revealed row is a left prefix of the whole one."""
    whole = _row(None).splitlines()[-1].strip()
    part = _row(share).splitlines()[-1].strip()
    assert whole.startswith(part)
