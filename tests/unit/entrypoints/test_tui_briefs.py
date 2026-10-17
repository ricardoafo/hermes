"""Tests for the Briefs screen of the Cockpit console (issue #720).

Invariants:

- tab 6 shows the supervisor desk's daily brief, unchanged, with the list of
  briefs beside it from 100 columns and above it below that;
- every brief carries the "never sees profit" line, and a digest says so;
- no price, value or P&L of the scenario is drawn on the screen;
- a read model that cannot serve briefs leaves one "not available yet" line;
- the reviewed picture of the screen, in Neon and Control room at the spec's
  four breakpoints, only changes when a reviewer accepts it.

Every app runs with motion off, a frozen clock and synthetic demo data, and
never reads or writes the real settings file.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from importlib import resources
from typing import ClassVar

import pytest
from syrupy.assertion import SnapshotAssertion
from syrupy.extensions.image import SVGImageSnapshotExtension
from syrupy.location import PyTestLocation
from syrupy.types import SnapshotIndex

from hermes.adapters.console import FixtureReadModel
from hermes.application.console import ConsoleReadModel, Surface, ViewNotAvailableError
from hermes.application.console.render import RenderContext, to_plain_text
from hermes.application.console.views import BriefSection, ConsoleBrief
from hermes.entrypoints.tui import HomeApp
from hermes.entrypoints.tui_briefs import (
    BESIDE_FROM,
    BRIEFS_FOOTER,
    DIGEST_NOTE,
    EARLIER_DAYS_NOTE,
    NO_BRIEF,
    briefs_screen,
)
from hermes.entrypoints.tui_settings import ThemeChoice
from hermes.entrypoints.tui_theme import BASE

# The fixtures' read time (14:12 Madrid), well inside Auto's day window.
_FROZEN = datetime(2026, 10, 3, 12, 12, tzinfo=UTC)
_CTX = RenderContext(surface=Surface.OPERATOR_TUI, now=_FROZEN)
_BRIEF = ConsoleBrief(
    "daily",
    _FROZEN,
    (
        BriefSection("Today", "Health ok.", ("health",)),
        BriefSection("What the engine did", "BTC/USDT: no signal.", ("engine",)),
    ),
)
_SIZES: tuple[tuple[int, int], ...] = ((80, 24), (120, 36), (160, 45), (240, 60))
_THEMES: tuple[tuple[str, ThemeChoice], ...] = (
    ("neon", ThemeChoice.NEON),
    ("control", ThemeChoice.CONTROL),
)


def _shifted(brief: ConsoleBrief, *, days: int) -> ConsoleBrief:
    return ConsoleBrief(brief.kind, brief.as_of - timedelta(days=days), brief.sections)


def _plain(briefs: Sequence[ConsoleBrief], width: int) -> list[str]:
    body = briefs_screen(briefs, _CTX, BASE, width=width)
    return [line.rstrip() for line in to_plain_text(body, width=width).splitlines()]


def test_the_list_sits_beside_the_brief_on_a_wide_screen() -> None:
    """From 100 columns the selected row and the brief title share one line."""
    lines = _plain((_BRIEF,), BESIDE_FROM)

    assert lines[0].startswith("▸ Today")
    assert lines[0].endswith("Daily brief · Sat 03 Oct 14:12")


def test_the_list_sits_above_the_brief_below_100_columns() -> None:
    """Below 100 columns the list comes first, then the brief, on lines of their own."""
    lines = _plain((_BRIEF,), BESIDE_FROM - 1)

    assert lines[:4] == ["▸ Today       daily", "", EARLIER_DAYS_NOTE, ""]
    assert lines[4] == "Daily brief · Sat 03 Oct 14:12"


def test_the_list_holds_the_kept_days_newest_first_and_drops_the_earlier_days_note() -> None:
    """Invariant: kept briefs are listed by day, newest selected, and the "none kept" note goes."""
    yesterday = _shifted(_BRIEF, days=1)
    last_week = _shifted(_BRIEF, days=7)

    lines = _plain((_BRIEF, yesterday, last_week), 80)

    assert lines[:3] == ["▸ Today       daily", "  Fri 02 Oct  daily", "  Sat 26 Sep  daily"]
    assert EARLIER_DAYS_NOTE not in "\n".join(lines)


def test_the_selected_brief_is_the_newest_not_a_kept_one() -> None:
    """Invariant: opening the screen shows today's brief, whatever is kept behind it."""
    older = ConsoleBrief(
        "daily",
        _BRIEF.as_of - timedelta(days=1),
        (BriefSection("Health", "Kept text.", ("health",)),),
    )

    text = "\n".join(_plain((_BRIEF, older), 80))

    assert "Daily brief · Sat 03 Oct 14:12" in text
    assert "Kept text." not in text


def test_each_item_shows_its_heading_text_and_citations() -> None:
    """The desk's items appear in order, each with its citations, then the footer."""
    lines = [line for line in _plain((_BRIEF,), 80) if line]

    assert lines[3:] == [
        "✦ Today",
        "Health ok.  [health]",
        "✦ What the engine did",
        "BTC/USDT: no signal.  [engine]",
        BRIEFS_FOOTER,
    ]


def test_a_digest_says_it_is_the_digest() -> None:
    """A brief that failed the gate is labelled as the digest of states and counts."""
    digest = ConsoleBrief(_BRIEF.kind, _BRIEF.as_of, _BRIEF.sections, digest=True)

    assert DIGEST_NOTE in "\n".join(_plain((digest,), 200))


def test_no_brief_says_so_and_keeps_the_footer() -> None:
    """With nothing to show, the screen says why rather than drawing an empty list."""
    assert _plain((), 80) == [NO_BRIEF, "", BRIEFS_FOOTER]


def _app(model: ConsoleReadModel, theme: ThemeChoice = ThemeChoice.NEON) -> HomeApp:
    return HomeApp(
        model,
        clock=lambda: _FROZEN,
        motion=False,
        settings_path=None,
        theme=theme,
        refresh_seconds=3600,
    )


def _briefs_text(model: ConsoleReadModel) -> str:
    async def drive() -> str:
        app = _app(model)
        async with app.run_test(size=(160, 45)) as pilot:
            await pilot.pause()
            await pilot.press("6")
            await pilot.pause()
            assert app.tab == 5
            return app.plain_text("screen-6")

    return asyncio.run(drive())


def test_pressing_6_shows_the_desks_daily_brief() -> None:
    """Tab 6 draws the scenario's brief, as the desk published it."""
    model = FixtureReadModel.load("ok-two-positions")
    (brief,) = model.briefs()

    text = _briefs_text(model)

    assert "Daily brief · Sat 03 Oct 14:12" in text
    for section in brief.sections:
        assert f"✦ {section.heading}" in text
    assert BRIEFS_FOOTER in " ".join(text.split())


def test_the_screen_draws_no_scenario_figure() -> None:
    """No entry, current or stop price, net or total of the scenario is on the screen."""
    model = FixtureReadModel.load("ok-two-positions")
    figures = {
        f"{value:,.2f}"
        for row in model.positions()
        for value in (row.entry_price, row.current_price, row.stop_price, abs(row.net))
    } | {f"{model.portfolio().total_value:,.2f}"}

    text = _briefs_text(model)

    assert [f for f in figures if f in text] == []
    assert "€" not in text


class _NoDesk(FixtureReadModel):
    """A demo scenario without a supervisor desk to read briefs from."""

    def briefs(self) -> tuple[ConsoleBrief, ...]:
        raise ViewNotAvailableError("briefs: no supervisor desk to read them from")


def test_without_a_desk_the_screen_is_not_available_yet() -> None:
    """A read model that cannot serve briefs leaves one plain line, not a crash."""
    model = _NoDesk("ok-two-positions", _document("ok-two-positions"))

    assert _briefs_text(model).strip() == "Briefs · not available yet"


def _document(name: str) -> dict[str, object]:
    path = resources.files("hermes.adapters.console") / "scenarios" / f"{name}.json"
    loaded: dict[str, object] = json.loads(path.read_text(encoding="utf-8"))
    return loaded


class _NamedSVG(SVGImageSnapshotExtension):
    """Store each snapshot under the name given to ``snapshot(name=...)`` alone."""

    _file_extension: ClassVar[str] = "svg"

    @classmethod
    def get_snapshot_name(cls, *, test_location: PyTestLocation, index: SnapshotIndex = 0) -> str:
        """Return the bare custom name, e.g. ``briefs_neon_160x45``."""
        if isinstance(index, str):
            return index
        return super().get_snapshot_name(test_location=test_location, index=index)


def _svg(size: tuple[int, int], theme: ThemeChoice) -> str:
    async def drive() -> str:
        app = _app(FixtureReadModel.load("ok-two-positions"), theme)
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            await pilot.press("6")
            await pilot.pause()
            svg = app.export_screenshot()
        return "\n".join(line.rstrip() for line in svg.splitlines()) + "\n"

    return asyncio.run(drive())


@pytest.mark.parametrize(
    ("theme_name", "theme", "size"),
    [
        pytest.param(name, theme, size, id=f"briefs_{name}_{size[0]}x{size[1]}")
        for name, theme in _THEMES
        for size in _SIZES
    ],
)
def test_briefs_svg_snapshot(
    snapshot: SnapshotAssertion, theme_name: str, theme: ThemeChoice, size: tuple[int, int]
) -> None:
    """The reviewed picture of the Briefs screen, in one theme, at one spec breakpoint."""
    name = f"briefs_{theme_name}_{size[0]}x{size[1]}"
    assert _svg(size, theme) == snapshot(extension_class=_NamedSVG, name=name)
