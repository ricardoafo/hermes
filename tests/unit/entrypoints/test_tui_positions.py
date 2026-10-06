"""Tests for the Positions screen of the Cockpit console (issue #715).

Invariants:

- tab 3 lists the open positions and shows the selected one's detail;
- ↑↓ on tab 3 move the selection, and the detail follows; Home and tab 3
  share one selection;
- with live prices the rows and the detail are the live ones, as on Home;
- the detail sits beside the list from 120 columns and under it below that;
- nothing open shows Home's empty state and no detail;
- a read model that cannot list positions leaves one "not available yet" line;
- the reviewed picture of the screen, in Neon and Control room at the spec's
  four breakpoints, only changes when a reviewer accepts it.

Every app runs with motion off, a frozen clock and synthetic demo data, and
never reads or writes the real settings file.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from importlib import resources
from typing import ClassVar

import pytest
from rich.console import RenderableType
from rich.text import Text
from syrupy.assertion import SnapshotAssertion
from syrupy.extensions.image import SVGImageSnapshotExtension
from syrupy.location import PyTestLocation
from syrupy.types import SnapshotIndex
from textual.pilot import Pilot

from hermes.adapters.console import FixtureReadModel
from hermes.application.console import ConsoleReadModel, PositionRow, ViewNotAvailableError
from hermes.application.console.render import to_plain_text
from hermes.entrypoints.tui import HomeApp
from hermes.entrypoints.tui_positions import BESIDE_FROM, HINT, list_width, positions_screen
from hermes.entrypoints.tui_settings import ThemeChoice
from hermes.entrypoints.tui_theme import BASE
from tests.unit.entrypoints.test_tui_live_screen import _FIRST, _FakeTicker
from tests.unit.entrypoints.test_tui_live_screen import _run as _run_live

# The fixtures' read time (14:12 Madrid), well inside Auto's day window.
_FROZEN = datetime(2026, 10, 3, 12, 12, tzinfo=UTC)
_SIZES: tuple[tuple[int, int], ...] = ((80, 24), (120, 36), (160, 45), (240, 60))
_THEMES: tuple[tuple[str, ThemeChoice], ...] = (
    ("neon", ThemeChoice.NEON),
    ("control", ThemeChoice.CONTROL),
)


def _plain(content: RenderableType, width: int) -> list[str]:
    return [line.rstrip() for line in to_plain_text(content, width=width).splitlines()]


def test_the_detail_sits_beside_the_list_from_120_columns() -> None:
    """From 120 columns the list's title and the detail's first line share a row."""
    wide = _plain(positions_screen(Text("rows"), Text("DETAIL"), 1, BASE, width=BESIDE_FROM), 120)
    narrow = _plain(
        positions_screen(Text("rows"), Text("DETAIL"), 1, BASE, width=BESIDE_FROM - 1), 119
    )

    assert wide[0].startswith("Open positions · 1")
    assert wide[0].endswith("DETAIL")
    assert narrow == ["Open positions · 1", "", "rows", "", HINT, "", "DETAIL"]


def test_with_nothing_open_there_is_no_detail() -> None:
    """The empty state stands alone, with the count at zero and the hint."""
    lines = _plain(positions_screen(Text("No open positions"), None, 0, BASE, width=160), 160)

    assert lines == ["Open positions · 0", "", "No open positions", "", HINT]


@pytest.mark.parametrize(("width", "expected"), [(80, 80), (119, 119), (120, 73), (154, 94)])
def test_the_list_takes_five_eighths_beside_the_detail(width: int, expected: int) -> None:
    """Beside the detail the rows get five eighths of the width, else all of it."""
    assert list_width(width) == expected


def _app(model: ConsoleReadModel, theme: ThemeChoice = ThemeChoice.NEON) -> HomeApp:
    return HomeApp(
        model,
        clock=lambda: _FROZEN,
        motion=False,
        settings_path=None,
        theme=theme,
        refresh_seconds=3600,
    )


def _screen(app: HomeApp, width: int = 154) -> str:
    content = app._content["screen-3"]
    return "\n".join(_plain(content, width))


def _drive[T](model: ConsoleReadModel, probe: Callable[[HomeApp, Pilot[None]], Awaitable[T]]) -> T:
    async def drive() -> T:
        app = _app(model)
        async with app.run_test(size=(160, 45)) as pilot:
            await pilot.pause()
            await pilot.press("3")
            await pilot.pause()
            assert app.tab == 2
            return await probe(app, pilot)

    return asyncio.run(drive())


def test_pressing_3_lists_both_positions_and_details_the_first() -> None:
    """Tab 3 lists ETH and SOL, ETH selected, with ETH's detail beside them."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> str:
        del pilot
        return _screen(app)

    text = _drive(FixtureReadModel.load("ok-two-positions"), probe)

    assert "Open positions · 2" in text
    assert "▸ETH" in text
    assert " SOL   1d" in text
    assert "ETH · 4h · long" in text
    assert "SOL · 1d · long" not in text


def test_down_selects_the_next_position_and_its_detail() -> None:
    """↓ on tab 3 moves to SOL and the detail follows; Home keeps the same choice."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[str, str]:
        await pilot.press("down")
        await pilot.pause()
        screen = _screen(app)
        await pilot.press("1")
        await pilot.pause()
        return screen, app.plain_text("positions")

    screen, home = _drive(FixtureReadModel.load("ok-two-positions"), probe)

    assert "SOL · 1d · long" in screen
    assert "▸SOL" in screen
    assert "▸SOL" in home


def test_nothing_open_shows_the_empty_state() -> None:
    """With no position the screen shows Home's radar and no detail."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> str:
        del pilot
        return _screen(app)

    text = _drive(FixtureReadModel.load("empty"), probe)

    assert "Open positions · 0" in text
    assert "No open positions" in text
    assert "Money if sold now" not in text


class _NoPositions(FixtureReadModel):
    """A demo scenario whose positions cannot be read."""

    def positions(self) -> tuple[PositionRow, ...]:
        raise ViewNotAvailableError("positions: not readable here")


def test_without_positions_the_screen_is_not_available_yet() -> None:
    """A read model that cannot list positions leaves one plain line, not a crash."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> str:
        del pilot
        return _screen(app).strip()

    model = _NoPositions("ok-two-positions", _document("ok-two-positions"))

    assert _drive(model, probe) == "Positions · not available yet"


def test_live_prices_reach_the_rows_and_the_detail() -> None:
    """With a ticker, tab 3 shows ETH's live price in the row and the live detail line."""
    fake = _FakeTicker(_FIRST)

    async def probe(app: HomeApp, pilot: Pilot[None], clock: object) -> str:
        del clock
        await pilot.press("3")
        await pilot.pause()
        return _screen(app)

    text = _run_live(FixtureReadModel.load("ok-two-positions"), fake, probe)

    assert "● live · now €2,313" in text
    row = next(line for line in text.splitlines() if "ETH   4h" in line)
    assert "€2,243 → €2,313" in row


def _document(name: str) -> dict[str, object]:
    path = resources.files("hermes.adapters.console") / "scenarios" / f"{name}.json"
    loaded: dict[str, object] = json.loads(path.read_text(encoding="utf-8"))
    return loaded


class _NamedSVG(SVGImageSnapshotExtension):
    """Store each snapshot under the name given to ``snapshot(name=...)`` alone."""

    _file_extension: ClassVar[str] = "svg"

    @classmethod
    def get_snapshot_name(cls, *, test_location: PyTestLocation, index: SnapshotIndex = 0) -> str:
        """Return the bare custom name, e.g. ``positions_neon_160x45``."""
        if isinstance(index, str):
            return index
        return super().get_snapshot_name(test_location=test_location, index=index)


def _svg(size: tuple[int, int], theme: ThemeChoice) -> str:
    async def drive() -> str:
        app = _app(FixtureReadModel.load("ok-two-positions"), theme)
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            await pilot.press("3")
            await pilot.pause()
            svg = app.export_screenshot()
        return "\n".join(line.rstrip() for line in svg.splitlines()) + "\n"

    return asyncio.run(drive())


@pytest.mark.parametrize(
    ("theme_name", "theme", "size"),
    [
        pytest.param(name, theme, size, id=f"positions_{name}_{size[0]}x{size[1]}")
        for name, theme in _THEMES
        for size in _SIZES
    ],
)
def test_positions_svg_snapshot(
    snapshot: SnapshotAssertion, theme_name: str, theme: ThemeChoice, size: tuple[int, int]
) -> None:
    """The reviewed picture of the Positions screen, in one theme, at one spec breakpoint."""
    name = f"positions_{theme_name}_{size[0]}x{size[1]}"
    assert _svg(size, theme) == snapshot(extension_class=_NamedSVG, name=name)
