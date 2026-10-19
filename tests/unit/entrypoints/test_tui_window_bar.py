"""The Neon window bar's live-feed pulse and next-bar countdown (issue #713).

Code Owner decision 2026-10-06: the decorative binary strip is gone; in its
place the bar shows one glyph per recent ticker poll ("●" live colour for a
good poll, "○" dim for a failed one, newest on the right, 16 wide), or, with
no ticker, the time to the next 4h check. Every app here uses synthetic demo
data, a frozen clock and no network.
"""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from textual.color import Color
from textual.pilot import Pilot

from hermes.adapters.console import FixtureReadModel
from hermes.application.console import Heartbeat, HeartbeatState
from hermes.application.console.live import TickOutcome
from hermes.entrypoints.tui import HomeApp, next_bar_text
from hermes.entrypoints.tui_settings import ThemeChoice
from hermes.entrypoints.tui_theme import (
    CONTROL_ROOM,
    FEED_WIDTH,
    MOTION,
    NEON,
    NIGHT,
    blend,
)
from hermes.entrypoints.tui_widgets import WindowBar, feed_pulse

# The fixture's read time: 14:12 Madrid, next check 18:00 Madrid.
_DAY = datetime(2026, 10, 3, 12, 12, tzinfo=UTC)
_NIGHT = datetime(2026, 10, 3, 21, 30, tzinfo=UTC)


def _outcomes(*, failed: str | None = None) -> tuple[TickOutcome, ...]:
    return tuple(
        TickOutcome(
            coin=coin,
            at=_DAY,
            price=None if coin == failed else Decimal(100),
            error="KrakenPublicError" if coin == failed else None,
        )
        for coin in ("BTC", "ETH", "SOL")
    )


def _run[T](
    probe: Callable[[HomeApp, Pilot[None]], Awaitable[T]],
    *,
    motion: bool = False,
    theme: ThemeChoice = ThemeChoice.NEON,
    clock: Callable[[], datetime] = lambda: _DAY,
) -> T:
    async def drive() -> T:
        app = HomeApp(
            FixtureReadModel.load("ok-two-positions"),
            clock=clock,
            motion=motion,
            theme=theme,
            refresh_seconds=3600,
        )
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.pause()
            return await probe(app, pilot)

    return asyncio.run(drive())


def _first_row(app: HomeApp) -> str:
    return app.screen._compositor.render_strips()[0].text.rstrip()


def test_feed_pulse_draws_one_glyph_per_poll_newest_right() -> None:
    """A good, a failed and a good poll: ``●○●`` right-aligned in 16 cells, live / dim / live."""
    pulse = feed_pulse([True, False, True], NEON)
    styles = [str(span.style) for span in pulse.spans]
    assert (pulse.plain, styles) == (" " * 13 + "●○●", [NEON.live, NEON.dim, NEON.live])


def test_feed_pulse_keeps_only_the_last_sixteen_polls() -> None:
    """Twenty polls with the first four failed: only the sixteen good ones show."""
    pulse = feed_pulse([False] * 4 + [True] * 16, NEON)
    assert (pulse.plain, FEED_WIDTH) == ("●" * 16, 16)


def test_bright_newest_glyph_is_bold_and_whiter() -> None:
    """Brightening touches the newest glyph only: bold, half way to white."""
    pulse = feed_pulse([True, True], NEON, bright=True)
    styles = [str(span.style) for span in pulse.spans]
    assert styles == [NEON.live, f"bold {blend(NEON.live, '#FFFFFF', 0.5)}"]


def test_next_bar_text_counts_down_to_the_check() -> None:
    """1h 12m 05s before the check reads ``next bar 01:12:05``; after it ``next bar due``."""
    check = datetime(2026, 10, 3, 16, 0, tzinfo=UTC)
    ok = Heartbeat(HeartbeatState.OK, check, check, check)
    late = Heartbeat(HeartbeatState.LATE, check, check, check)
    assert [
        next_bar_text(ok, check - timedelta(hours=1, minutes=12, seconds=5)),
        next_bar_text(ok, check + timedelta(seconds=1)),
        next_bar_text(late, check),
        next_bar_text(None, check),
    ] == ["next bar 01:12:05", "next bar due", "", ""]


def test_without_a_ticker_the_bar_shows_the_next_bar_countdown() -> None:
    """No ticker: at 14:12 Madrid with the check at 18:00 the bar reads ``next bar 03:48:00``."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[str, tuple[bool, ...]]:
        del pilot
        return _first_row(app), app.feed

    row, feed = _run(probe)
    assert (row.split("CONSOLE")[1].split()[:3], feed) == (["next", "bar", "03:48:00"], ())


def test_polls_replace_the_countdown_with_the_feed_pulse() -> None:
    """Two good polls and one with SOL failed: the bar shows ``●●○`` and no countdown."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[tuple[bool, ...], str]:
        for outcomes in (_outcomes(), _outcomes(), _outcomes(failed="SOL")):
            app.apply_ticks(outcomes)
        app._tick()
        await pilot.pause()
        return app.feed, _first_row(app)

    feed, row = _run(probe)
    assert (feed, row.split("CONSOLE ")[1].strip()[:3], "next bar" in row) == (
        (True, True, False),
        "●●○",
        False,
    )


def test_motion_on_brightens_the_newest_glyph_then_dims_it() -> None:
    """With motion on a poll brightens the newest glyph; after ``MOTION.flash`` it is plain."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[bool, bool]:
        bar = app.query_one(WindowBar)
        app.apply_ticks(_outcomes())
        right_after = bar.bright
        await pilot.pause(MOTION.flash + 0.3)
        return right_after, bar.bright

    assert _run(probe, motion=True) == (True, False)


def test_motion_off_never_brightens_the_feed() -> None:
    """With motion off a poll adds its glyph but nothing brightens."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[bool, tuple[bool, ...]]:
        del pilot
        app.apply_ticks(_outcomes())
        return app.query_one(WindowBar).bright, app.feed

    assert _run(probe, motion=False) == (False, (True,))


def test_whole_window_is_the_theme_void() -> None:
    """Screen, body and every container that can show empty space are the theme's void.

    Code Owner 2026-10-06: on a large terminal the space outside the panels
    was the terminal's black instead of the theme background.
    """
    ids = ("#screens", "#tab-1", "#body", "#left-column", "#right-column", "#mid-row", "#top")
    cases = (
        (ThemeChoice.NEON, _DAY, NEON.void),
        (ThemeChoice.CONTROL, _DAY, CONTROL_ROOM.void),
        (ThemeChoice.AUTO, _NIGHT, NIGHT.void),
    )
    seen = []
    for theme, now, void in cases:

        async def probe(app: HomeApp, pilot: Pilot[None]) -> set[Color]:
            del pilot
            backgrounds = {app.screen.styles.background}
            backgrounds |= {app.query_one(widget_id).styles.background for widget_id in ids}
            return backgrounds

        seen.append((_run(probe, theme=theme, clock=lambda now=now: now), void))
    assert [backgrounds for backgrounds, _ in seen] == [{Color.parse(void)} for _, void in seen]
