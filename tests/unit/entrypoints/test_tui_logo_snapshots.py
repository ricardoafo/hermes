"""SVG snapshots of the Logo's wordmark fallback in both day themes (#733).

Invariant: the wordmark the console shows where images are not drawn (Neon
brand gradient, Control room plain cyan) only changes when a reviewer accepts
the new picture. Files are named ``logo_wordmark_<theme>_<cols>x<rows>.svg``,
like the Cockpit snapshots in ``test_tui_snapshots``.
"""

import asyncio
from typing import ClassVar

import pytest
from syrupy.assertion import SnapshotAssertion
from syrupy.extensions.image import SVGImageSnapshotExtension
from syrupy.location import PyTestLocation
from syrupy.types import SnapshotIndex
from textual.app import App, ComposeResult

from hermes.entrypoints.tui_logo import Logo
from hermes.entrypoints.tui_theme import (
    BASE,
    CONTROL_THEME,
    NEON_THEME,
    THEMES,
    data_tokens,
    terminal_theme,
    theme_variables,
)

_SIZES: tuple[tuple[int, int], ...] = ((80, 24), (160, 45))
_THEMES: tuple[tuple[str, str], ...] = (("neon", NEON_THEME), ("control", CONTROL_THEME))


class _NamedSVG(SVGImageSnapshotExtension):
    """Store each snapshot under the name given to ``snapshot(name=...)`` alone."""

    _file_extension: ClassVar[str] = "svg"

    @classmethod
    def get_snapshot_name(cls, *, test_location: PyTestLocation, index: SnapshotIndex = 0) -> str:
        """Return the bare custom name, e.g. ``logo_wordmark_neon_80x24``."""
        if isinstance(index, str):
            return index
        return super().get_snapshot_name(test_location=test_location, index=index)


class _LogoApp(App[None]):
    CSS = "Screen { background: $hud-void; align: center middle; }"

    def __init__(self, theme: str) -> None:
        super().__init__()
        for hermes_theme in THEMES:
            self.register_theme(hermes_theme)
        self.ansi_theme_dark = terminal_theme(data_tokens(theme))
        self.theme = theme

    def get_theme_variable_defaults(self) -> dict[str, str]:
        return theme_variables(BASE)

    def compose(self) -> ComposeResult:
        yield Logo()


def _repo_clean(svg: str) -> str:
    """Strip trailing whitespace, as the repository's pre-commit hook does to committed SVGs."""
    return "\n".join(line.rstrip() for line in svg.splitlines()) + "\n"


def _svg(theme: str, size: tuple[int, int]) -> str:
    async def drive() -> str:
        app = _LogoApp(theme)
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            return _repo_clean(app.export_screenshot())

    return asyncio.run(drive())


@pytest.mark.parametrize(
    ("theme_name", "theme", "size"),
    [
        pytest.param(name, theme, size, id=f"logo_wordmark_{name}_{size[0]}x{size[1]}")
        for name, theme in _THEMES
        for size in _SIZES
    ],
)
def test_wordmark_svg_snapshot(
    snapshot: SnapshotAssertion, theme_name: str, theme: str, size: tuple[int, int]
) -> None:
    """The reviewed picture of the wordmark fallback, in one theme, at one size."""
    name = f"logo_wordmark_{theme_name}_{size[0]}x{size[1]}"
    assert _svg(theme, size) == snapshot(extension_class=_NamedSVG, name=name)
