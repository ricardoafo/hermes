"""SVG snapshots of Home with the new top bar (issues #833 and #815).

Invariant: the top bar the operator sees (the mainnet badge, the version and
release line, the next-bar countdown and its meter) only changes when a
reviewer accepts the new picture, in Neon and in Control room at the design
spec's four breakpoints. Files are named ``topbar_<theme>_<cols>x<rows>.svg``;
``topbar_on_*`` and ``topbar_unknown_*`` show a live console (no demo
scenario, so no demo tag) when the config says mainnet is on and when the
config could not be read; a demo scenario always reads mainnet off.

The clock is frozen at 16:00 Madrid, half way from the 14:00 check to the
18:00 one, so the meter is visibly half full. Every app runs with motion off,
synthetic data (the demo fixture, wrapped as live for the on and unknown
views), a fixed version and release, and no settings file.
"""

import asyncio
from datetime import UTC, datetime
from typing import ClassVar

import pytest
from syrupy.assertion import SnapshotAssertion
from syrupy.extensions.image import SVGImageSnapshotExtension
from syrupy.location import PyTestLocation
from syrupy.types import SnapshotIndex

from hermes.adapters.console import FixtureReadModel
from hermes.application.console import ConsoleReadModel
from hermes.entrypoints.tui import HomeApp
from hermes.entrypoints.tui_settings import ThemeChoice

# 16:00 Madrid: two hours after the fixture's 14:00 check, two before the 18:00 one.
_HALF_WAY = datetime(2026, 10, 3, 14, 0, tzinfo=UTC)
_SIZES: tuple[tuple[int, int], ...] = ((80, 24), (120, 36), (160, 45), (240, 60))
_THEMES: tuple[tuple[str, ThemeChoice], ...] = (
    ("neon", ThemeChoice.NEON),
    ("control", ThemeChoice.CONTROL),
)
_STATES: tuple[tuple[str, bool | None, tuple[tuple[int, int], ...]], ...] = (
    ("topbar", False, _SIZES),
    ("topbar_on", True, ((160, 45),)),
    ("topbar_unknown", None, ((160, 45),)),
)


class _Live:
    """The demo fixture claiming to be live data (no demo scenario), as a ledger model is."""

    def __init__(self) -> None:
        self._inner = FixtureReadModel.load("ok-two-positions")

    @property
    def demo_scenario(self) -> None:
        return None

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)


class _NamedSVG(SVGImageSnapshotExtension):
    """Store each snapshot under the name given to ``snapshot(name=...)`` alone."""

    _file_extension: ClassVar[str] = "svg"

    @classmethod
    def get_snapshot_name(cls, *, test_location: PyTestLocation, index: SnapshotIndex = 0) -> str:
        """Return the bare custom name, e.g. ``topbar_neon_160x45``."""
        if isinstance(index, str):
            return index
        return super().get_snapshot_name(test_location=test_location, index=index)


def _repo_clean(svg: str) -> str:
    """Strip trailing whitespace, as the repository's pre-commit hook does to committed SVGs."""
    return "\n".join(line.rstrip() for line in svg.splitlines()) + "\n"


def _svg(size: tuple[int, int], theme: ThemeChoice, allow_mainnet: bool | None) -> str:
    async def drive() -> str:
        model: ConsoleReadModel = (
            FixtureReadModel.load("ok-two-positions") if allow_mainnet is False else _Live()  # type: ignore[assignment]
        )
        app = HomeApp(
            model,
            clock=lambda: _HALF_WAY,
            motion=False,
            settings_path=None,
            theme=theme,
            refresh_seconds=3600,
            release=lambda: "679b676",
            version="v1.0.0-shadow.1",
            allow_mainnet=allow_mainnet,
        )
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            return _repo_clean(app.export_screenshot())

    return asyncio.run(drive())


_CASES = [
    pytest.param(
        f"{view}_{theme_name}_{size[0]}x{size[1]}",
        theme,
        size,
        allow_mainnet,
        id=f"{view}_{theme_name}_{size[0]}x{size[1]}",
    )
    for view, allow_mainnet, sizes in _STATES
    for theme_name, theme in _THEMES
    for size in sizes
]


@pytest.mark.parametrize(("name", "theme", "size", "allow_mainnet"), _CASES)
def test_topbar_svg_snapshot(
    snapshot: SnapshotAssertion,
    name: str,
    theme: ThemeChoice,
    size: tuple[int, int],
    allow_mainnet: bool | None,
) -> None:
    """The reviewed picture of Home's top bar for one mainnet state, theme and breakpoint."""
    assert _svg(size, theme, allow_mainnet) == snapshot(extension_class=_NamedSVG, name=name)
