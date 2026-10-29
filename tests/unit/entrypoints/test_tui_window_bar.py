"""The text-free window bar, and the feed pulse and clock on the status row (issues #713, #1097).

Code Owner decision 2026-10-06: the feed shows one glyph per recent ticker
poll ("●" live colour for a good poll, "○" dim for a failed one, newest on the
right, 16 wide). Code Owner decision 2026-10-17: the window bar leads every
theme as one row of the ``chrome`` gradient with no characters; the feed pulse
and the clock sit on the status row, and the next-bar countdown is dropped
because the status row's countdown already says it. Every app here uses
synthetic demo data, a frozen clock and no network.
"""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from rich.text import Text
from textual.color import Color
from textual.pilot import Pilot

from hermes.adapters.console import FixtureReadModel
from hermes.application.console.live import TickOutcome
from hermes.entrypoints.tui import HomeApp
from hermes.entrypoints.tui_settings import ThemeChoice
from hermes.entrypoints.tui_theme import (
    CONTROL_ROOM,
    FEED_WIDTH,
    MOTION,
    NEON,
    NIGHT,
    Tokens,
    blend,
)
from hermes.entrypoints.tui_widgets import WindowBar, feed_pulse, window_bar_text

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
    width: int = 120,
) -> T:
    async def drive() -> T:
        app = HomeApp(
            FixtureReadModel.load("ok-two-positions"),
            clock=clock,
            motion=motion,
            theme=theme,
            refresh_seconds=3600,
        )
        async with app.run_test(size=(width, 36)) as pilot:
            await pilot.pause()
            return await probe(app, pilot)

    return asyncio.run(drive())


_THEMES = (
    (ThemeChoice.NEON, _DAY, NEON),
    (ThemeChoice.CONTROL, _DAY, CONTROL_ROOM),
    (ThemeChoice.AUTO, _NIGHT, NIGHT),
)
_THEME_IDS = ("neon", "control", "night")


def _first_row(app: HomeApp) -> str:
    return app.screen._compositor.render_strips()[0].text


def _slot(app: HomeApp, widget_id: str) -> Text:
    content = app._content.get(widget_id, Text(""))
    assert isinstance(content, Text)
    return content


def _newest_is_bold(app: HomeApp) -> bool:
    feed = _slot(app, "feed")
    return bool(feed.spans) and str(feed.spans[-1].style).startswith("bold ")


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


def test_window_bar_text_is_blank_cells_on_the_chrome_gradient() -> None:
    """The bar is ``width`` spaces whose background runs from the first chrome stop to the last."""
    bar = window_bar_text(30, NEON)
    first, last = bar.spans[0].style, bar.spans[-1].style
    assert (bar.plain, bar.cell_len, str(first).split(" on ")[1], str(last).split(" on ")[1]) == (
        " " * 30,
        30,
        NEON.chrome[0],
        NEON.chrome[-1],
    )


@pytest.mark.parametrize(("theme", "now", "tokens"), _THEMES, ids=_THEME_IDS)
def test_window_bar_leads_every_theme_with_no_text(
    theme: ThemeChoice, now: datetime, tokens: Tokens
) -> None:
    """In Neon, Control room and night, row 0 is the window bar: shown, no characters, chrome."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[bool, str, Color | None]:
        del pilot
        bar = app.query_one(WindowBar)
        strip = app.screen._compositor.render_strips()[0]
        first = next(iter(strip))
        background = first.style.bgcolor if first.style is not None else None
        colour = None if background is None else Color.from_rich_color(background)
        return bar.display, _first_row(app).strip(), colour

    assert _run(probe, theme=theme, clock=lambda: now) == (
        True,
        "",
        Color.parse(tokens.chrome[0]),
    )


def test_without_a_ticker_the_status_row_has_no_feed() -> None:
    """No ticker: no feed glyphs, and the countdown alone tells the time to the next bar."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[str, tuple[bool, ...], str]:
        del pilot
        return _slot(app, "feed").plain, app.feed, app.plain_text("countdown").strip()

    assert _run(probe, width=160) == ("", (), "in 3h 48m 00s")


def test_polls_show_the_feed_pulse_on_the_status_row() -> None:
    """Two good polls and one with SOL failed: the status row's feed reads ``●●○``."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[tuple[bool, ...], str]:
        for outcomes in (_outcomes(), _outcomes(), _outcomes(failed="SOL")):
            app.apply_ticks(outcomes)
        app._tick()
        await pilot.pause()
        return app.feed, _slot(app, "feed").plain.strip()

    assert _run(probe, width=160) == ((True, True, False), "●●○")


def test_wide_status_row_shows_the_feed_then_the_clock() -> None:
    """At 200 columns the feed pulse and then the Madrid clock sit before the theme chip."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> str:
        app.apply_ticks(_outcomes())
        app._tick()
        await pilot.pause()
        row = app.screen._compositor.render_strips()[2].text
        return row.split("▏")[1].split("◑")[0].strip()

    assert _run(probe, width=200) == "● 14:12:00"


def test_a_narrow_status_row_drops_the_clock_before_the_feed() -> None:
    """At 160 columns the feed still fits after the countdown meter but the clock does not."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[str, str, str]:
        app.apply_ticks(_outcomes())
        app._tick()
        await pilot.pause()
        return (
            _slot(app, "feed").plain.strip(),
            _slot(app, "clock").plain,
            app.plain_text("countdown").strip(),
        )

    assert _run(probe, width=160) == ("●", "", "in 3h 48m 00s")


def test_compact_status_row_has_neither_feed_nor_clock() -> None:
    """At 80 columns the heartbeat keeps the row: no feed pulse and no clock."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[str, str]:
        app.apply_ticks(_outcomes())
        app._tick()
        await pilot.pause()
        return _slot(app, "feed").plain, _slot(app, "clock").plain

    assert _run(probe, width=80) == ("", "")


def test_motion_on_brightens_the_newest_glyph_then_dims_it() -> None:
    """With motion on a poll brightens the newest glyph; after ``MOTION.flash`` it is plain."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[bool, bool]:
        app.apply_ticks(_outcomes())
        right_after = _newest_is_bold(app)
        await pilot.pause(MOTION.flash + 0.3)
        return right_after, _newest_is_bold(app)

    assert _run(probe, motion=True, width=160) == (True, False)


def test_motion_off_never_brightens_the_feed() -> None:
    """With motion off a poll adds its glyph but nothing brightens."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[bool, tuple[bool, ...]]:
        del pilot
        app.apply_ticks(_outcomes())
        return _newest_is_bold(app), app.feed

    assert _run(probe, motion=False, width=160) == (False, (True,))


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
