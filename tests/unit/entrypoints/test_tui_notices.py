"""Tests for the console's bar-close toast and optional bell (issue #721).

Invariants:

- a toast says which bar was checked and when the next one is, once per new
  check, only while hermes is OK, and never for the reading the console
  opens on;
- the bell rings once each time hermes turns late or down, never twice for
  the same state, and only with ``HERMES_CONSOLE_BEEP=on``;
- in the running console a new check raises exactly one toast and no bell,
  and turning down rings the bell once when it is on.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st
from textual.notifications import SeverityLevel

from hermes.adapters.console import FixtureReadModel
from hermes.application.console import Heartbeat
from hermes.application.console.render import MADRID
from hermes.application.console.views import HeartbeatState
from hermes.entrypoints.tui import HomeApp
from hermes.entrypoints.tui_notices import (
    BEEP_ENV,
    bar_checked_notice,
    beep_enabled,
    should_beep,
)

_FROZEN = datetime(2026, 10, 3, 12, 12, tzinfo=UTC)
_BAR = timedelta(hours=4)
_OK = Heartbeat(
    HeartbeatState.OK,
    as_of=_FROZEN,
    last_check_at=datetime(2026, 10, 3, 12, tzinfo=UTC),
    next_check_at=datetime(2026, 10, 3, 16, tzinfo=UTC),
)


def _state(state: HeartbeatState) -> Heartbeat:
    return replace(_OK, state=state, down_since=_FROZEN if state is HeartbeatState.DOWN else None)


def _next(beat: Heartbeat) -> Heartbeat:
    return replace(beat, last_check_at=beat.next_check_at, next_check_at=beat.next_check_at + _BAR)


def test_a_new_check_reads_its_bar_and_the_next() -> None:
    """The toast names the checked bar and the next one in Madrid time."""
    assert bar_checked_notice(_OK, _next(_OK), MADRID) == "✓ 18:00 bar checked · next 22:00"


@given(state=st.sampled_from(HeartbeatState), moved=st.booleans(), first=st.booleans())
def test_a_toast_only_for_a_new_check_while_ok(
    state: HeartbeatState, *, moved: bool, first: bool
) -> None:
    """INV: a toast exactly when a reading came before, the check moved on and hermes is OK."""
    current = _state(state)
    if moved:
        current = _next(current)
    previous = None if first else _OK

    notice = bar_checked_notice(previous, current, MADRID)

    assert (notice is not None) == (not first and moved and state is HeartbeatState.OK)


@pytest.mark.parametrize(
    ("before", "after", "rings"),
    [
        (HeartbeatState.OK, HeartbeatState.LATE, True),
        (HeartbeatState.OK, HeartbeatState.DOWN, True),
        (HeartbeatState.LATE, HeartbeatState.DOWN, True),
        (HeartbeatState.LATE, HeartbeatState.LATE, False),
        (HeartbeatState.DOWN, HeartbeatState.DOWN, False),
        (HeartbeatState.DOWN, HeartbeatState.OK, False),
        (HeartbeatState.OK, HeartbeatState.OK, False),
        (None, HeartbeatState.DOWN, True),
        (None, HeartbeatState.OK, False),
    ],
)
def test_the_bell_rings_on_turning_late_or_down(
    before: HeartbeatState | None, after: HeartbeatState, *, rings: bool
) -> None:
    """The bell rings once per change into late or down, never for staying there."""
    previous = None if before is None else _state(before)

    assert should_beep(previous, _state(after)) is rings


@given(states=st.lists(st.sampled_from(HeartbeatState), min_size=1, max_size=20))
def test_the_bell_never_rings_twice_for_one_spell(states: list[HeartbeatState]) -> None:
    """INV: over any run of readings, rings equal the number of entries into late or down."""
    beats = [_state(state) for state in states]
    rings = sum(should_beep(prev, cur) for prev, cur in zip([None, *beats], beats, strict=False))
    entries = sum(
        cur in {HeartbeatState.LATE, HeartbeatState.DOWN} and cur is not prev
        for prev, cur in zip([None, *states], states, strict=False)
    )

    assert rings == entries


@pytest.mark.parametrize(
    ("value", "on"),
    [("on", True), ("ON", True), ("1", True), ("yes", True), ("off", False), ("", False)],
)
def test_the_bell_is_off_unless_asked_for(value: str, *, on: bool) -> None:
    """Only an explicit on value turns the bell on."""
    assert beep_enabled({BEEP_ENV: value}) is on
    assert beep_enabled({}) is False


class _Moving(FixtureReadModel):
    """A demo scenario whose heartbeat the test sets."""

    beat: Heartbeat = _OK

    def heartbeat(self) -> Heartbeat:
        return self.beat


class _Recording(HomeApp):
    """The console, recording its toasts and bells instead of showing them."""

    toasts: list[str]
    bells: int

    def notify(
        self,
        message: str,
        *,
        title: str = "",
        severity: SeverityLevel = "information",
        timeout: float | None = None,
        markup: bool = True,
    ) -> None:
        del title, severity, timeout, markup
        self.toasts.append(message)

    def bell(self) -> None:
        self.bells += 1


def _run(beats: list[Heartbeat]) -> tuple[list[str], int]:
    model = _Moving.load("ok-two-positions")
    assert isinstance(model, _Moving)

    async def drive() -> tuple[list[str], int]:
        app = _Recording(
            model, clock=lambda: _FROZEN, motion=False, settings_path=None, refresh_seconds=3600
        )
        app.toasts, app.bells = [], 0
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.pause()
            for beat in beats:
                model.beat = beat
                app._redraw()
                await pilot.pause()
        return app.toasts, app.bells

    return asyncio.run(drive())


def test_the_console_toasts_each_new_check_once() -> None:
    """Opening shows no toast; a new check shows one; reading it again shows none."""
    toasts, bells = _run([_next(_OK), _next(_OK)])

    assert toasts == ["✓ 18:00 bar checked · next 22:00"]
    assert bells == 0


def test_the_console_rings_once_on_going_down_when_asked(monkeypatch: pytest.MonkeyPatch) -> None:
    """With the bell on, turning down rings once, and staying down rings no more."""
    monkeypatch.setenv(BEEP_ENV, "on")
    down = _state(HeartbeatState.DOWN)

    toasts, bells = _run([down, down])

    assert (toasts, bells) == ([], 1)
