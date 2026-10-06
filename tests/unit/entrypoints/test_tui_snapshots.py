"""SVG snapshots of the Cockpit console in both day themes (issue #711).

Invariant: what the console draws in Neon and in Control room, at the design
spec's four breakpoints (80x24 compact, 120x36 standard, 160x45 wide, 240x60
ultra), only changes when a reviewer accepts the new picture. Each file is
named ``<view>_<theme>_<cols>x<rows>.svg`` so a PR body can link it directly.

Views: Home on the ``ok-two-positions`` and ``empty`` demo scenarios, the
Signals placeholder (tab 2), the theme picker open over Home, and Home with a
fake ticker (``home_live``, issue #713): live market prices, a position valued
live and the window bar's feed pulse after a run of polls, one of them failed.

Every app here runs with motion off, a frozen clock and synthetic demo data,
and never reads or writes the real settings file.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import ClassVar

import pytest
from syrupy.assertion import SnapshotAssertion
from syrupy.extensions.image import SVGImageSnapshotExtension
from syrupy.location import PyTestLocation
from syrupy.types import SnapshotIndex

from hermes.adapters.console import FixtureReadModel
from hermes.application.console.live import TickOutcome
from hermes.domain.errors import KrakenPublicError
from hermes.domain.market_data.ticker import Ticker
from hermes.domain.value_objects.pair import Pair
from hermes.entrypoints.tui import HomeApp
from hermes.entrypoints.tui_settings import ThemeChoice

# The fixtures' read time (14:12 Madrid), well inside Auto's day window.
_FROZEN = datetime(2026, 10, 3, 12, 12, tzinfo=UTC)

_SIZES: tuple[tuple[int, int], ...] = ((80, 24), (120, 36), (160, 45), (240, 60))
_THEMES: tuple[tuple[str, ThemeChoice], ...] = (
    ("neon", ThemeChoice.NEON),
    ("control", ThemeChoice.CONTROL),
)
_PRICES = {"BTC": Decimal(62000), "ETH": Decimal(2700), "SOL": Decimal(150)}
# The feed pulse's history after the opening poll: good polls, one with SOL failed.
_POLLS: tuple[str | None, ...] = (None, None, None, "SOL", None, None, None)


class _FakeTicker:
    """Synthetic prices; never reaches the network."""

    def get_ticker(self, pair: Pair) -> Ticker:
        price = _PRICES[pair.base.value]
        return Ticker(
            pair=pair,
            best_bid=price - 1,
            best_ask=price + 1,
            last_price=price,
            snapshot_time=_FROZEN,
        )


def _poll(failed: str | None) -> tuple[TickOutcome, ...]:
    return tuple(
        TickOutcome(
            coin=coin,
            at=_FROZEN,
            price=None if coin == failed else price,
            error=KrakenPublicError.__name__ if coin == failed else None,
        )
        for coin, price in _PRICES.items()
    )


_VIEWS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("home_ok", "ok-two-positions", ()),
    ("home_empty", "empty", ()),
    ("tab2_placeholder", "ok-two-positions", ("2",)),
    ("theme_picker", "ok-two-positions", ("t",)),
    ("home_live", "ok-two-positions", ()),
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


def _svg(
    scenario: str,
    keys: Sequence[str],
    size: tuple[int, int],
    theme: ThemeChoice,
    *,
    live: bool = False,
) -> str:
    async def drive() -> str:
        app = HomeApp(
            FixtureReadModel.load(scenario),
            clock=lambda: _FROZEN,
            motion=False,
            settings_path=None,
            theme=theme,
            refresh_seconds=3600,
            ticker=_FakeTicker() if live else None,
        )
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            if live:
                await app.workers.wait_for_complete()
                await pilot.pause()
                for failed in _POLLS:
                    app.apply_ticks(_poll(failed))
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
    svg = _svg(scenario, keys, size, theme, live=view == "home_live")
    assert svg == snapshot(extension_class=_NamedSVG, name=name)
