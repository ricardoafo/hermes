"""SVG snapshots of the Tax screen in both day themes (issue #719).

Invariant: what the Tax screen's Summary and Lots sub-tabs draw, in Neon and
in Control room, at the design spec's four breakpoints (80x24, 120x36, 160x45,
240x60), only changes when a reviewer accepts the new picture. Each file is
named ``tax_<view>_<theme>_<cols>x<rows>.svg``.

The console runs with motion off, a frozen clock, the ``ok-two-positions``
demo scenario and the synthetic demo tax year; the Lots view has the flagged
SOL lot highlighted, so its FIFO detail shows.
"""

import asyncio
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from syrupy.assertion import SnapshotAssertion

from hermes.adapters.console import FixtureReadModel
from hermes.adapters.console.tax_demo import DemoTaxSource, demo_profile
from hermes.entrypoints.tui import HomeApp
from hermes.entrypoints.tui_query import SortableTable
from hermes.entrypoints.tui_settings import ThemeChoice
from hermes.entrypoints.tui_tax import TaxPackStatus, TaxTab
from tests.support.tui_views import assert_view, capture
from tests.unit.domain.tax.scales import SCALE_2026

_FROZEN = datetime(2026, 10, 3, 12, 12, tzinfo=UTC)
_SIZES: tuple[tuple[int, int], ...] = ((80, 24), (120, 36), (160, 45), (240, 60))
_THEMES: tuple[tuple[str, ThemeChoice], ...] = (
    ("neon", ThemeChoice.NEON),
    ("control", ThemeChoice.CONTROL),
)
_VIEWS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("summary", ("4",)),
    ("lots", ("4", "right_square_bracket")),
)
# Newest first: ETH, BTC, SOL (open), then the flagged SOL lot.
_FLAGGED_ROW = 3


def _repo_clean(svg: str) -> str:
    """Strip trailing whitespace, as the repository's pre-commit hook does to committed SVGs."""
    return "\n".join(line.rstrip() for line in svg.splitlines()) + "\n"


def _svg(view: str, keys: tuple[str, ...], size: tuple[int, int], theme: ThemeChoice) -> str:
    async def drive() -> str:
        app = HomeApp(
            FixtureReadModel.load("ok-two-positions"),
            clock=lambda: _FROZEN,
            motion=False,
            settings_path=None,
            theme=theme,
            refresh_seconds=3600,
            tax=DemoTaxSource(scales={2026: SCALE_2026}, default_personal_minimum=Decimal(5550)),
            taxpayer_profile=demo_profile(),
            tax_pack=lambda year: TaxPackStatus(year=year, state="not built in the demo"),
        )
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            for key in keys:
                await pilot.press(key)
                await pilot.pause()
            if view == "lots":
                table = app.query_one(TaxTab).query_one(SortableTable)
                table.focus()
                table.move_cursor(row=_FLAGGED_ROW, animate=False)
                await pilot.pause()
            return capture(app)

    return asyncio.run(drive())


@pytest.mark.parametrize(
    ("view", "keys", "theme_name", "theme", "size"),
    [
        pytest.param(view, keys, name, theme, size, id=f"tax_{view}_{name}_{size[0]}x{size[1]}")
        for view, keys in _VIEWS
        for name, theme in _THEMES
        for size in _SIZES
    ],
)
def test_tax_svg_snapshot(
    snapshot: SnapshotAssertion,
    view: str,
    keys: tuple[str, ...],
    theme_name: str,
    theme: ThemeChoice,
    size: tuple[int, int],
) -> None:
    """The reviewed picture of one Tax sub-tab, in one theme, at one breakpoint."""
    name = f"tax_{view}_{theme_name}_{size[0]}x{size[1]}"
    assert_view(snapshot, _svg(view, keys, size, theme), name=name)
