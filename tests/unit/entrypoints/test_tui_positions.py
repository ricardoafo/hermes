"""The Cockpit console's Positions screen, view only (issue #715).

Invariants:

- tab 3 lists the open positions and the recently closed ones, open first,
  with state, when, change, net and stop room;
- a surface that hides money (``pnl_visible`` false) gets no net, exit price
  or EUR figure in any row or closed-position detail;
- with room (100 columns and up) the selected position's detail shows beside
  the list and follows the cursor; below that, Enter opens a DetailScreen
  under ``◂ Positions › <coin>`` and Esc returns with the cursor on the
  position shown last;
- the cursor stays on the same position when a refresh changes the rows;
- the reviewed SVG snapshots at the four spec breakpoints in both themes.

Every app here gets a frozen clock, motion off and the fixture read model.
"""

# ruff: noqa: RUF001, RUF002 - the single right angle quote is the design spec's breadcrumb separator

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import ClassVar
from zoneinfo import ZoneInfo

import pytest
from hypothesis import given
from hypothesis import strategies as st
from syrupy.assertion import SnapshotAssertion
from syrupy.extensions.image import SVGImageSnapshotExtension
from syrupy.location import PyTestLocation
from syrupy.types import SnapshotIndex
from textual.app import App
from textual.pilot import Pilot

from hermes.adapters.console import FixtureReadModel
from hermes.application.console.render import RenderContext
from hermes.application.console.views import ActivityDay, ActivityItem, ActivityKind
from hermes.application.console.visibility import Surface
from hermes.entrypoints.tui import HomeApp
from hermes.entrypoints.tui_drilldown import DetailScreen
from hermes.entrypoints.tui_positions import (
    PositionsView,
    closed_detail,
    closed_items,
    position_rows,
)
from hermes.entrypoints.tui_settings import ThemeChoice
from hermes.entrypoints.tui_theme import BASE

_FROZEN = datetime(2026, 10, 3, 12, 12, tzinfo=UTC)  # 14:12 Madrid
_MADRID = ZoneInfo("Europe/Madrid")
_POSITIONS_TAB = 2
_OPERATOR = RenderContext(surface=Surface.OPERATOR_TUI, now=_FROZEN, tz=_MADRID)
_HIDDEN = RenderContext(surface=Surface.SUPERVISOR_DESK, now=_FROZEN, tz=_MADRID)


def _model() -> FixtureReadModel:
    return FixtureReadModel.load("ok-two-positions")


def _closed(coin: str, hour: int, net: str | None = "3.18") -> ActivityItem:
    return ActivityItem(
        at=datetime(2026, 10, 3, hour, tzinfo=UTC),
        kind=ActivityKind.CLOSED,
        coin=coin,
        price=Decimal("61240"),
        net=None if net is None else Decimal(net),
    )


def test_closed_items_keep_only_closed_rows_with_a_coin_newest_first() -> None:
    """Bars, stop raises and closed rows without a coin are not closed positions."""
    days = (
        ActivityDay(
            date(2026, 10, 3),
            (
                ActivityItem(at=_FROZEN, kind=ActivityKind.BAR, coin="ETH", text="4h check"),
                _closed("BTC", 10),
                ActivityItem(at=_FROZEN, kind=ActivityKind.CLOSED),
                _closed("SOL", 11),
            ),
        ),
    )
    assert [(item.coin, item.at.hour) for item in closed_items(days)] == [("SOL", 11), ("BTC", 10)]


def test_open_rows_come_first_then_closed_with_state_and_when() -> None:
    """Open positions list before the closed ones; each row names its state and time."""
    model = _model()
    rows = position_rows(model.positions(), (_closed("BTC", 10),), _OPERATOR)
    assert [(r["coin"], r["state"], r["when"]) for r in rows] == [
        ("ETH", "open", "opened 10-02 10:00"),
        ("SOL", "open", "opened 09-28 02:00"),
        ("BTC", "closed", "closed 10-03 12:00"),
    ]


def test_a_surface_that_hides_money_gets_no_net_in_any_row() -> None:
    """``pnl_visible`` false empties the net of open and closed rows."""
    model = _model()
    hidden = position_rows(model.positions(), (_closed("BTC", 10),), _HIDDEN)
    shown = position_rows(model.positions(), (_closed("BTC", 10),), _OPERATOR)
    assert ([r["net"] for r in hidden], all(r["net"] for r in shown)) == ([None, None, None], True)


def test_the_closed_detail_shows_exit_price_and_net_only_with_money() -> None:
    """The exit price and the net of a closed position are money."""
    tokens = BASE
    item = _closed("BTC", 10)
    shown = closed_detail(item, _OPERATOR, tokens).plain
    hidden = closed_detail(item, _HIDDEN, tokens).plain
    assert (
        "exit price" in shown and "net +€3.18" in shown,
        "exit price" in hidden or "€" in hidden,
    ) == (
        True,
        False,
    )


@given(
    net=st.one_of(st.none(), st.decimals(min_value=-1000, max_value=1000, places=2)),
    hour=st.integers(min_value=0, max_value=23),
)
def test_a_hidden_surface_never_leaks_a_closed_net_into_a_row(
    net: Decimal | None, hour: int
) -> None:
    """Whatever the closed position's net, a money-hiding surface shows none."""
    item = _closed("BTC", hour, None if net is None else str(net))
    rows = position_rows((), (item,), _HIDDEN)
    assert rows[0]["net"] is None


# ----------------------------------------------------------------------
# The screen
# ----------------------------------------------------------------------


def _text(app: App[None]) -> str:
    return "\n".join(strip.text.rstrip() for strip in app.screen._compositor.render_strips())


async def _press(pilot: Pilot[None], keys: Sequence[str]) -> None:
    for key in keys:
        await pilot.press(key)
        await pilot.pause()


async def _until(pilot: Pilot[None], done: Callable[[], bool], *, seconds: float = 5.0) -> None:
    """Wait until ``done()`` holds, so a slow runner cannot read a stale screen.

    Raises:
        TimeoutError: ``done()`` did not hold within ``seconds``.

    """
    async with asyncio.timeout(seconds):
        while not done():
            await pilot.pause(0.01)


def _run[T](
    probe: Callable[[HomeApp, Pilot[None]], Awaitable[T]],
    *,
    size: tuple[int, int] = (160, 45),
    theme: ThemeChoice | None = None,
) -> T:
    async def drive() -> T:
        app = HomeApp(
            _model(),
            clock=lambda: _FROZEN,
            motion=False,
            refresh_seconds=3600,
            theme=theme,
        )
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            await _press(pilot, ["3"])
            await _until(pilot, lambda: app.positions_view.selected_key() is not None)
            return await probe(app, pilot)

    return asyncio.run(drive())


def test_the_screen_lists_open_positions_then_the_closed_one() -> None:
    """Tab 3 shows ETH and SOL as open, then BTC as closed, in the list."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> list[tuple[object, object]]:
        del pilot
        return [(r["coin"], r["state"]) for r in app.positions_view.query_one("QueryList").shown]  # type: ignore[attr-defined]

    assert _run(probe) == [("ETH", "open"), ("SOL", "open"), ("BTC", "closed")]


def test_the_detail_beside_the_list_follows_the_cursor() -> None:
    """Moving down to the next position replaces the detail with that position's."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[bool, bool]:
        first = "ETH" in _text(app).split("Detail", 1)[-1]
        await _press(pilot, ["down"])
        second = "SOL" in _text(app).split("Detail", 1)[-1]
        return first, second

    assert _run(probe) == (True, True)


def test_enter_on_a_compact_terminal_opens_a_detail_screen_and_esc_returns() -> None:
    """Below 100 columns Enter opens ``◂ Positions › ETH``; Esc leaves the cursor on it."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[bool, bool, str | None]:
        await _press(pilot, ["down", "enter"])
        opened = isinstance(app.screen, DetailScreen)
        crumb = "◂ Positions › SOL" in _text(app)
        await _press(pilot, ["escape"])
        return (
            opened and crumb,
            not isinstance(app.screen, DetailScreen),
            app.positions_view.selected_key(),
        )

    assert _run(probe, size=(80, 24)) == (True, True, "SOL")


def test_the_cursor_stays_on_its_position_when_a_refresh_changes_the_rows() -> None:
    """A read that adds a closed position above the cursor does not move it off SOL."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> str | None:
        await _press(pilot, ["down"])
        view = app.positions_view
        reads = app._reads
        assert reads is not None
        newer = _closed("XRP", 12)
        view.receive(reads.positions, (newer, *closed_items(reads.activity)), reads.ctx)  # type: ignore[arg-type]
        await pilot.pause()
        return view.selected_key()

    assert _run(probe) == "SOL"


def test_a_failed_read_says_so_in_the_note_and_lists_no_row() -> None:
    """When the positions cannot be read the screen names the reason and shows no rows."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[str, int]:
        view = app.positions_view
        view.receive(None, (), _OPERATOR, error="Positions not available: ledger locked")
        await pilot.pause()
        return _text(app), len(view.query_one("QueryList").shown)  # type: ignore[attr-defined]

    text, rows = _run(probe)
    assert ("Positions not available: ledger locked" in text, rows) == (True, 0)


def test_the_view_type_is_the_tab_widget() -> None:
    """Tab 3's switcher child is the PositionsView."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> bool:
        del pilot
        return isinstance(app.positions_view, PositionsView)

    assert _run(probe)


# ----------------------------------------------------------------------
# Snapshots (motion off, frozen clock)
# ----------------------------------------------------------------------


def _repo_clean(svg: str) -> str:
    """Strip trailing whitespace, as the repository's pre-commit hook does to committed SVGs."""
    return "\n".join(line.rstrip() for line in svg.splitlines()) + "\n"


class _NamedSVG(SVGImageSnapshotExtension):
    """Store each snapshot under the name given to ``snapshot(name=...)`` alone."""

    _file_extension: ClassVar[str] = "svg"

    @classmethod
    def get_snapshot_name(cls, *, test_location: PyTestLocation, index: SnapshotIndex = 0) -> str:
        """Return the bare custom name, e.g. ``positions_neon_160x45``."""
        if isinstance(index, str):
            return index
        return super().get_snapshot_name(test_location=test_location, index=index)


_SIZES: tuple[tuple[int, int], ...] = ((80, 24), (120, 36), (160, 45), (240, 60))
_THEMES: tuple[tuple[str, ThemeChoice], ...] = (
    ("neon", ThemeChoice.NEON),
    ("control", ThemeChoice.CONTROL),
)


def _svg(size: tuple[int, int], theme: ThemeChoice) -> str:
    async def probe(app: HomeApp, pilot: Pilot[None]) -> str:
        del pilot
        return _repo_clean(app.export_screenshot())

    return _run(probe, size=size, theme=theme)


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
    """The reviewed Positions screen in one theme at one of the four spec breakpoints."""
    name = f"positions_{theme_name}_{size[0]}x{size[1]}"
    assert _svg(size, theme) == snapshot(extension_class=_NamedSVG, name=name)
