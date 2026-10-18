"""A new activity row wears a highlight that fades (issue #721).

Invariants:

- the first read of the console highlights nothing, and neither does a read
  that brings no new row;
- a row that was not in the last read is highlighted, the others are drawn as
  usual, and the highlight is gone after ``MOTION.activity_fade`` seconds
  (counted as lines that set a background colour); the tests never wait on the
  wall clock: the fade timer is parked and each step is taken by hand;
- with motion off no row is ever highlighted;
- ``render_activity`` lays the given style over exactly the rows it is given.
"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from rich.console import Console
from textual.pilot import Pilot

from hermes.adapters.console import FixtureReadModel
from hermes.application.console.render import RenderContext, render_activity
from hermes.application.console.views import ActivityDay, ActivityItem, ActivityKind
from hermes.application.console.visibility import Surface
from hermes.domain.errors import LedgerError
from hermes.entrypoints import tui
from hermes.entrypoints.tui import HomeApp
from hermes.entrypoints.tui_theme import MOTION

_FROZEN = datetime(2026, 10, 3, 12, 12, tzinfo=UTC)
_NEW = ActivityItem(
    at=_FROZEN - timedelta(minutes=1),
    kind=ActivityKind.CLOSED,
    coin="XRP",
    price=Decimal("0.52"),
    net=Decimal("1.10"),
)


class _GrowingModel(FixtureReadModel):
    """The fixture whose activity gains one closed row once ``grown`` is set."""

    grown = False
    broken = False
    extra: ActivityItem | None = None

    def activity(self, *, include_quiet: bool = False) -> tuple[ActivityDay, ...]:
        if self.broken:
            msg = "ledger locked"
            raise LedgerError(msg=msg)
        days = super().activity(include_quiet=include_quiet)
        if not self.grown:
            return days
        today = days[0]
        extra = () if self.extra is None else (self.extra,)
        return (replace(today, items=(*extra, _NEW, *today.items)), *days[1:])


@pytest.fixture(autouse=True)
def _parked_fade(monkeypatch: pytest.MonkeyPatch) -> None:
    """Park the fade timer for an hour so only the steps a test takes move the fade."""
    monkeypatch.setattr(tui, "MOTION", replace(MOTION, activity_fade=3600.0))


def _fade_to_the_end(app: HomeApp) -> None:
    """Take every remaining fade step by hand."""
    for _ in range(MOTION.activity_fade_steps):
        app._fade_activity()


def _run[T](
    probe: Callable[[HomeApp, Pilot[None], _GrowingModel], Awaitable[T]], *, motion: bool = True
) -> T:
    model = _GrowingModel.load("ok-two-positions")
    assert isinstance(model, _GrowingModel)

    async def drive() -> T:
        app = HomeApp(
            model, clock=lambda: _FROZEN, motion=motion, refresh_seconds=3600, exposure_cap=None
        )
        async with app.run_test(size=(160, 45)) as pilot:
            await pilot.pause()
            return await probe(app, pilot, model)

    return asyncio.run(drive())


def _background_rows(card: object) -> int:
    """Return how many lines of a rendered card set a background colour."""
    console = Console(width=120, force_terminal=True, color_system="truecolor", record=False)
    with console.capture() as capture:
        console.print(card)
    return sum(1 for line in capture.get().splitlines() if "\x1b[48;2;" in line or ";48;2;" in line)


def test_a_first_read_and_a_read_with_nothing_new_highlight_nothing() -> None:
    """Opening the console, then reading the same rows again, leaves every row plain."""

    async def probe(app: HomeApp, pilot: Pilot[None], model: _GrowingModel) -> frozenset[object]:
        del model
        app._redraw()
        await pilot.pause()
        return app._fresh_activity

    assert _run(probe) == frozenset()


def test_a_new_row_is_highlighted_and_the_others_are_not() -> None:
    """After a read that adds a closed XRP row, only that row is fresh."""

    async def probe(app: HomeApp, pilot: Pilot[None], model: _GrowingModel) -> frozenset[object]:
        model.grown = True
        app._redraw()
        await pilot.pause()
        return app._fresh_activity

    assert _run(probe) == frozenset({_NEW})


def test_the_highlight_fades_out_after_its_last_step() -> None:
    """A fresh row has a background until the last fade step, and none after it."""

    async def probe(app: HomeApp, pilot: Pilot[None], model: _GrowingModel) -> tuple[int, int]:
        model.grown = True
        app._redraw()
        await pilot.pause()
        during = _background_rows(app._content["activity"])
        _fade_to_the_end(app)
        return during, _background_rows(app._content["activity"])

    assert _run(probe) == (1, 0)


def test_motion_off_never_highlights_a_row() -> None:
    """The same new row with motion off is not fresh and no span has a background."""

    async def probe(app: HomeApp, pilot: Pilot[None], model: _GrowingModel) -> tuple[int, int]:
        model.grown = True
        app._redraw()
        await pilot.pause()
        return len(app._fresh_activity), _background_rows(app._content["activity"])

    assert _run(probe, motion=False) == (0, 0)


def test_render_activity_styles_only_the_rows_it_is_given() -> None:
    """A style given for the XRP row is on its cells and on none of the other rows."""
    ctx = RenderContext(surface=Surface.OPERATOR_TUI, now=_FROZEN)
    other = ActivityItem(at=_FROZEN - timedelta(hours=1), kind=ActivityKind.OPENED, coin="ETH")
    days = (ActivityDay(_FROZEN.date(), (_NEW, other)),)
    styled = render_activity(days, ctx, icons=True, emphasis={_NEW: "on #123456"})
    plain = render_activity(days, ctx, icons=True)
    assert (_background_rows(styled), _background_rows(plain)) == (1, 0)


def test_a_second_new_row_in_mid_fade_restarts_the_fade_and_still_ends() -> None:
    """A row arriving mid-fade becomes the highlighted one, and its highlight ends too."""
    later = replace(_NEW, coin="DOGE", at=_FROZEN)

    async def probe(
        app: HomeApp, pilot: Pilot[None], model: _GrowingModel
    ) -> tuple[frozenset[object], int, int]:
        model.grown = True
        app._redraw()
        await pilot.pause()
        app._fade_activity()
        model.extra = later
        app._redraw()
        await pilot.pause()
        both = app._fresh_activity
        _fade_to_the_end(app)
        return both, len(app._fresh_activity), _background_rows(app._content["activity"])

    assert _run(probe) == (frozenset({later}), 0, 0)


def test_a_failed_activity_read_in_mid_fade_shows_the_error_and_no_highlight() -> None:
    """The card says it is unavailable while rows were fading; no stale tint stays behind."""

    async def probe(app: HomeApp, pilot: Pilot[None], model: _GrowingModel) -> tuple[bool, int]:
        model.grown = True
        app._redraw()
        await pilot.pause()
        model.broken = True
        app._redraw()
        await pilot.pause()
        card = " ".join(app.plain_text("activity").split())
        return "could not read the ledger: ledger locked" in card, _background_rows(
            app._content["activity"]
        )

    assert _run(probe) == (True, 0)
