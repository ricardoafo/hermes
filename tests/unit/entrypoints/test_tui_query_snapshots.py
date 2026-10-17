"""SVG snapshots of the reusable query list in both day themes (issue #729).

Invariant: what the activity query list draws with ``since:2d`` typed into the
bar and the facet counts under it, in Neon and in Control room, at the design
spec's four breakpoints (80x24, 120x36, 160x45, 240x60), only changes when a
reviewer accepts the new picture. Each file is named
``query_activity_<theme>_<cols>x<rows>.svg``.

The app runs with motion off, a frozen clock and synthetic activity items, and
every picture waits for the app to settle (``_settle``), never for CPU idle alone.
"""

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from typing import ClassVar

import pytest
from syrupy.assertion import SnapshotAssertion
from syrupy.extensions.image import SVGImageSnapshotExtension
from syrupy.location import PyTestLocation
from syrupy.types import SnapshotIndex

from hermes.application.console.render import MADRID
from hermes.application.console.views import ActivityItem, ActivityKind
from hermes.entrypoints.tui_query import QueryInput, activity_query_app
from hermes.entrypoints.tui_theme import CONTROL_THEME, NEON_THEME
from tests.unit.entrypoints._settle import settle

_FROZEN = datetime(2026, 10, 3, 12, 12, tzinfo=UTC)

_SIZES: tuple[tuple[int, int], ...] = ((80, 24), (120, 36), (160, 45), (240, 60))


class _NamedSVG(SVGImageSnapshotExtension):
    """Store each snapshot under the name given to ``snapshot(name=...)`` alone."""

    _file_extension: ClassVar[str] = "svg"

    @classmethod
    def get_snapshot_name(cls, *, test_location: PyTestLocation, index: SnapshotIndex = 0) -> str:
        """Return the bare custom name, e.g. ``query_activity_neon_160x45``."""
        if isinstance(index, str):
            return index
        return super().get_snapshot_name(test_location=test_location, index=index)


def _repo_clean(svg: str) -> str:
    """Strip trailing whitespace, as the repository's pre-commit hook does to committed SVGs."""
    return "\n".join(line.rstrip() for line in svg.splitlines()) + "\n"


_QUERY_THEMES: tuple[tuple[str, str], ...] = (("neon", NEON_THEME), ("control", CONTROL_THEME))
_QUERY = "since:2d"


def _stamp(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=UTC)


_QUERY_ITEMS: tuple[ActivityItem, ...] = (
    ActivityItem(
        at=_stamp(3, 11, 59), kind=ActivityKind.BAR, text="checked 3 coins · no signal", count=3
    ),
    ActivityItem(
        at=_stamp(3, 8, 1), kind=ActivityKind.RESTART, text="deployed 9f3a1c0 (was 2bc2798)"
    ),
    ActivityItem(
        at=_stamp(3, 7), kind=ActivityKind.WARNING, coin="ETH", text="exchange.slow (Timeout)"
    ),
    ActivityItem(
        at=_stamp(2, 16),
        kind=ActivityKind.CLOSED,
        coin="BTC",
        price=Decimal(61240),
        net=Decimal("3.18"),
    ),
    ActivityItem(at=_stamp(2, 12), kind=ActivityKind.STOP_RAISED, coin="BTC", count=2),
    ActivityItem(at=_stamp(1, 20), kind=ActivityKind.ERROR, text="order.failed (ApiError)"),
    ActivityItem(at=_stamp(1, 9), kind=ActivityKind.OPENED, coin="BTC", price=Decimal(60100)),
    ActivityItem(at=_stamp(1, 8), kind=ActivityKind.OPENED, coin="SOL"),
    ActivityItem(at=_stamp(1, 7), kind=ActivityKind.RESTART, text="restarted · release 2bc2798"),
    ActivityItem(at=_stamp(1, 6), kind=ActivityKind.CLOSED, coin="ETH", net=Decimal("-1.20")),
)


def _query_svg(theme: str, size: tuple[int, int]) -> str:
    async def drive() -> str:
        app = activity_query_app(_QUERY_ITEMS, MADRID, lambda: _FROZEN, theme=theme, motion=False)
        async with app.run_test(size=size) as pilot:
            await settle(pilot)
            app.query_one(QueryInput).value = _QUERY
            await settle(pilot)
            return _repo_clean(app.export_screenshot())

    return asyncio.run(drive())


@pytest.mark.parametrize(
    ("theme_name", "theme", "size"),
    [
        pytest.param(name, theme, size, id=f"query_activity_{name}_{size[0]}x{size[1]}")
        for name, theme in _QUERY_THEMES
        for size in _SIZES
    ],
)
def test_query_activity_svg_snapshot(
    snapshot: SnapshotAssertion, theme_name: str, theme: str, size: tuple[int, int]
) -> None:
    """The reviewed picture of the activity query list, in one theme, at one breakpoint."""
    name = f"query_activity_{theme_name}_{size[0]}x{size[1]}"
    assert _query_svg(theme, size) == snapshot(extension_class=_NamedSVG, name=name)
