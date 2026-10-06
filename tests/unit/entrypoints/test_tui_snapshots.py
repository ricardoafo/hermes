"""SVG snapshots of the Cockpit console in both day themes (issue #711).

Invariant: what the console draws in Neon and in Control room, at the design
spec's four breakpoints (80x24 compact, 120x36 standard, 160x45 wide, 240x60
ultra), only changes when a reviewer accepts the new picture. Each file is
named ``<view>_<theme>_<cols>x<rows>.svg`` so a PR body can link it directly.

Views: Home on the ``ok-two-positions`` and ``empty`` demo scenarios, the
Signals placeholder (tab 2), the theme picker open over Home, and the Shadow
test screen (tab 5, issue #718) counting from a day-0 record
(``ok-two-positions``) and in the warm-up with no record (``empty``).

Every app here runs with motion off, a frozen clock and synthetic demo data,
and never reads or writes the real settings file.
"""

from __future__ import annotations

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
from hermes.application.console.shadow_screen import ShadowSources
from hermes.entrypoints.tui import HomeApp
from hermes.entrypoints.tui_settings import ThemeChoice

# The fixtures' read time (14:12 Madrid), well inside Auto's day window.
_FROZEN = datetime(2026, 10, 3, 12, 12, tzinfo=UTC)

_SIZES: tuple[tuple[int, int], ...] = ((80, 24), (120, 36), (160, 45), (240, 60))
_THEMES: tuple[tuple[str, ThemeChoice], ...] = (
    ("neon", ThemeChoice.NEON),
    ("control", ThemeChoice.CONTROL),
)
_VIEWS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("home_ok", "ok-two-positions", ()),
    ("home_empty", "empty", ()),
    ("tab2_placeholder", "ok-two-positions", ("2",)),
    ("theme_picker", "ok-two-positions", ("t",)),
    ("shadow_counting", "ok-two-positions", ("5",)),
    ("shadow_warmup", "empty", ("5",)),
)


class _NamedSVG(SVGImageSnapshotExtension):
    """Store each snapshot under the name given to ``snapshot(name=...)`` alone."""

    _file_extension: ClassVar[str] = "svg"

    @classmethod
    def get_snapshot_name(cls, *, test_location: PyTestLocation, index: SnapshotIndex = 0) -> str:
        """Return the bare custom name, e.g. ``home_ok_neon_160x45``."""
        if isinstance(index, str):
            return index
        return super().get_snapshot_name(test_location=test_location, index=index)


def _repo_clean(svg: str) -> str:
    """Strip trailing whitespace, as the repository's pre-commit hook does to committed SVGs."""
    return "\n".join(line.rstrip() for line in svg.splitlines()) + "\n"


def _svg(scenario: str, keys: Sequence[str], size: tuple[int, int], theme: ThemeChoice) -> str:
    async def drive() -> str:
        model = FixtureReadModel.load(scenario)
        app = HomeApp(
            model,
            clock=lambda: _FROZEN,
            motion=False,
            settings_path=None,
            theme=theme,
            refresh_seconds=3600,
            shadow=ShadowSources(evidence=model, runs=model, notebook=model, strategy_id="demo"),
        )
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            for key in keys:
                await pilot.press(key)
                await pilot.pause()
            return _repo_clean(app.export_screenshot())

    return asyncio.run(drive())


_CASES = [
    pytest.param(
        view,
        scenario,
        keys,
        theme_name,
        theme,
        size,
        id=f"{view}_{theme_name}_{size[0]}x{size[1]}",
    )
    for view, scenario, keys in _VIEWS
    for theme_name, theme in _THEMES
    for size in _SIZES
]


@pytest.mark.parametrize(("view", "scenario", "keys", "theme_name", "theme", "size"), _CASES)
def test_cockpit_svg_snapshot(
    snapshot: SnapshotAssertion,
    view: str,
    scenario: str,
    keys: tuple[str, ...],
    theme_name: str,
    theme: ThemeChoice,
    size: tuple[int, int],
) -> None:
    """The reviewed picture of one view, in one theme, at one spec breakpoint."""
    name = f"{view}_{theme_name}_{size[0]}x{size[1]}"
    svg = _svg(scenario, keys, size, theme)
    assert svg == snapshot(extension_class=_NamedSVG, name=name)
