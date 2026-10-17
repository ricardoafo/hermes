"""SVG snapshots of the position drill-down in both day themes (issue #730).

Invariant: what the drill-down draws in Neon and in Control room, at the design
spec's four breakpoints (80x24 compact, 120x36 standard, 160x45 wide, 240x60
ultra), only changes when a reviewer accepts the new picture. Each file is
named ``<view>_<theme>_<cols>x<rows>.svg`` so a PR body can link it directly.

Views: ``drill_position`` is Enter on the first position (the detail pane
beside the list from 100 columns, a detail screen at 80), and
``drill_breadcrumb`` is the detail screen with its breadcrumb after ``]``,
opened at 80x24 and then widened to the size in its name.

Every app here runs with motion off, a frozen clock and synthetic demo data,
and never reads or writes the real settings file.
"""

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import ClassVar

import pytest
from syrupy.assertion import SnapshotAssertion
from syrupy.extensions.image import SVGImageSnapshotExtension
from syrupy.location import PyTestLocation
from syrupy.types import SnapshotIndex

from hermes.adapters.console import FixtureReadModel
from hermes.entrypoints.tui import HomeApp
from hermes.entrypoints.tui_settings import ThemeChoice

# The fixtures' read time (14:12 Madrid), well inside Auto's day window.
_FROZEN = datetime(2026, 10, 3, 12, 12, tzinfo=UTC)

_SIZES: tuple[tuple[int, int], ...] = ((80, 24), (120, 36), (160, 45), (240, 60))
_THEMES: tuple[tuple[str, ThemeChoice], ...] = (
    ("neon", ThemeChoice.NEON),
    ("control", ThemeChoice.CONTROL),
)
_COMPACT: tuple[int, int] = (80, 24)
_SCENARIO = "ok-two-positions"

# (view, keys, size the app opens at; None opens at the snapshot's own size).
# The breadcrumb opens at the compact size, where Enter pushes a detail screen,
# and is then widened, so the screen and its trail are pictured at every size.
_VIEWS: tuple[tuple[str, tuple[str, ...], tuple[int, int] | None], ...] = (
    ("drill_position", ("enter",), None),
    ("drill_breadcrumb", ("enter", "right_square_bracket"), _COMPACT),
)


class _NamedSVG(SVGImageSnapshotExtension):
    """Store each snapshot under the name given to ``snapshot(name=...)`` alone."""

    _file_extension: ClassVar[str] = "svg"

    @classmethod
    def get_snapshot_name(cls, *, test_location: PyTestLocation, index: SnapshotIndex = 0) -> str:
        """Return the bare custom name, e.g. ``drill_position_neon_160x45``."""
        if isinstance(index, str):
            return index
        return super().get_snapshot_name(test_location=test_location, index=index)


def _repo_clean(svg: str) -> str:
    """Strip trailing whitespace, as the repository's pre-commit hook does to committed SVGs."""
    return "\n".join(line.rstrip() for line in svg.splitlines()) + "\n"


def _svg(
    keys: Sequence[str],
    size: tuple[int, int],
    theme: ThemeChoice,
    open_at: tuple[int, int] | None,
) -> str:
    async def drive() -> str:
        app = HomeApp(
            FixtureReadModel.load(_SCENARIO),
            clock=lambda: _FROZEN,
            motion=False,
            settings_path=None,
            theme=theme,
            refresh_seconds=3600,
        )
        async with app.run_test(size=open_at or size) as pilot:
            await pilot.pause()
            for key in keys:
                await pilot.press(key)
                await pilot.pause()
            if open_at is not None:
                await pilot.resize_terminal(*size)
                await pilot.pause()
            return _repo_clean(app.export_screenshot())

    return asyncio.run(drive())


@pytest.mark.parametrize(
    ("view", "keys", "open_at", "theme_name", "theme", "size"),
    [
        pytest.param(
            view,
            keys,
            open_at,
            theme_name,
            theme,
            size,
            id=f"{view}_{theme_name}_{size[0]}x{size[1]}",
        )
        for view, keys, open_at in _VIEWS
        for theme_name, theme in _THEMES
        for size in _SIZES
    ],
)
def test_drill_down_svg_snapshot(
    snapshot: SnapshotAssertion,
    view: str,
    keys: tuple[str, ...],
    open_at: tuple[int, int] | None,
    theme_name: str,
    theme: ThemeChoice,
    size: tuple[int, int],
) -> None:
    """The reviewed picture of one drill-down view, in one theme, at one spec breakpoint."""
    name = f"{view}_{theme_name}_{size[0]}x{size[1]}"
    svg = _svg(keys, size, theme, open_at)
    assert svg == snapshot(extension_class=_NamedSVG, name=name)
