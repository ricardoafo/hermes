"""The read-only operator home screen (``hermes tui``), driven with Textual's pilot.

Each test protects one rule of the approved console UX (issue #377 and the
operator feedback of 2026-10-03):

- the status is one dim line when all is well, amber when late, one red row
  plus dimmed money "as of" when down;
- the range keys change the hero change line and the sparkline together;
- Enter opens the position detail, beside the list on wide terminals;
- the tax footnote lives behind ``?`` only; ``L`` shows the journal (#508);
- DEMO DATA shows on fixtures and the screen is an operator surface;
- the app only reads: it imports no adapter and calls only read-model methods.
"""

# The console prints the typographic minus (U+2212) and times sign on purpose.

from __future__ import annotations

import ast
import asyncio
import io
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from rich.console import Console, RenderableType
from syrupy.assertion import SnapshotAssertion
from textual.binding import Binding
from textual.widgets import Static

from hermes.adapters.console import FixtureReadModel
from hermes.application.console import ConsoleReadModel, Surface, pnl_visible
from hermes.application.console.format import big_digits
from hermes.application.console.logs import LogLine
from hermes.entrypoints import tui as tui_module
from hermes.entrypoints.tui import REFRESH_SECONDS, HomeApp, _PanelScreen
from hermes.entrypoints.tui_widgets import BigNumber
from tests.support.tui_views import assert_view, capture

_TAX_FOOTNOTE = "est. · FIFO · 19% band · €5,988.30 left before 21%"
# The fixture's read time (14:12 Madrid): snapshots never show the wall clock.
_FROZEN = datetime(2026, 10, 3, 12, 12, tzinfo=UTC)
_STATUS_ROW = 2
_READ_METHODS = {
    "demo_scenario",
    "heartbeat",
    "portfolio",
    "positions",
    "position",
    "activity",
    "shadow_test",
    "market_watch",
}
_WIDGETS = (
    "demo-tag",
    "heartbeat",
    "down-row",
    "portfolio",
    "positions",
    "market",
    "activity",
    "detail",
    "shadow",
)


@dataclass(frozen=True)
class Shot:
    """What the app showed after a key sequence."""

    texts: dict[str, str]
    styled: dict[str, str]
    screen: str
    depth: int
    svg: str


def _styled(content: RenderableType, width: int) -> str:
    console = Console(
        width=width, file=io.StringIO(), record=True, force_terminal=True, color_system="standard"
    )
    console.print(content)
    return console.export_text(styles=True)


async def _drive(
    model: ConsoleReadModel,
    keys: Sequence[str],
    size: tuple[int, int],
    journal: Callable[[int], Sequence[LogLine] | None] | None = None,
) -> tuple[Shot, HomeApp]:
    app = HomeApp(model, journal=journal, clock=lambda: _FROZEN, motion=False)
    async with app.run_test(size=size) as pilot:
        await pilot.pause()
        for key in keys:
            await pilot.press(key)
            await pilot.pause()
        texts = {name: app.plain_text(name) for name in _WIDGETS}
        styled = {name: _styled(content, size[0]) for name, content in app._content.items()}
        screen = "\n".join(strip.text.rstrip() for strip in app.screen._compositor.render_strips())
        svg = capture(app)
        shot = Shot(texts, styled, screen, len(app.screen_stack), svg)
    return shot, app


def _shoot(scenario: str, keys: Sequence[str] = (), size: tuple[int, int] = (80, 24)) -> Shot:
    shot, _ = asyncio.run(_drive(FixtureReadModel.load(scenario), keys, size))
    return shot


def test_ok_status_is_one_dim_line_and_nothing_else() -> None:
    """All well: the status is the single heartbeat line, no red row, no 'all good' panel."""
    shot = _shoot("ok-two-positions")
    assert (
        shot.texts["heartbeat"].strip(),
        shot.texts["down-row"].strip(),
        "All good" in shot.screen,
        "Your move" in shot.screen,
    ) == ("● live · checked 14:00 · next 18:00", "", False, False)


def test_ok_heartbeat_is_dim() -> None:
    """The OK heartbeat line carries the dim style."""
    shot = _shoot("ok-two-positions")
    assert "\x1b[2m" in shot.styled["heartbeat"]


def test_late_status_is_an_amber_line() -> None:
    """A late check turns the line amber and names how late it is."""
    shot = _shoot("late")
    assert (
        shot.texts["heartbeat"].strip().startswith("◐"),
        "check late" in shot.texts["heartbeat"],
        "\x1b[33m" in shot.styled["heartbeat"],
    ) == (True, True, True)


def test_down_adds_one_red_row_and_dims_the_money() -> None:
    """Down: a single red row with the call to action; money is dim and reads 'as of 18:04'."""
    shot = _shoot("down-stale")
    assert shot.texts["down-row"].strip() == "✖ hermes down since 18:04 · press L for why"
    assert "\x1b[1;31m" in shot.styled["down-row"]
    assert "as of 18:04" in shot.texts["portfolio"]
    assert "\x1b[2m" in shot.styled["portfolio"]
    assert "as of 18:04" in shot.texts["positions"]


def test_total_and_change_lead_the_portfolio() -> None:
    """The portfolio block opens with the total in EUR and the change for the range."""
    shot = _shoot("ok-two-positions")
    first = shot.texts["portfolio"].splitlines()[0]
    assert first == "€1,021.06   ▲ +€12.30 +1.22% today"


def test_range_keys_change_hero_and_sparkline() -> None:
    """D, W, M and Y each change the change line and the sparkline together (#650)."""
    seen = {}
    for key in ("d", "w", "m", "y"):
        lines = _shoot("ok-two-positions", [key]).texts["portfolio"].splitlines()
        seen[key] = (lines[0], lines[2])
    assert seen == {
        "d": ("€1,021.06   ▲ +€12.30 +1.22% today", "▁▂▁▄▆█"),
        "w": ("€1,021.06   ▲ +€15.20 +1.51% this week", seen["w"][1]),
        "m": ("€1,021.06   ▲ +€19.00 +1.90% this month", seen["m"][1]),
        "y": ("€1,021.06   ▲ +€21.06 +2.11% this year", seen["y"][1]),
    }
    assert len({sparkline for _, sparkline in seen.values()}) == 4


def test_range_keys_work_in_upper_case() -> None:
    """Shift+W reaches the same range as w: the footer prints the keys in capitals."""
    first = _shoot("ok-two-positions", ["W"]).texts["portfolio"].splitlines()[0]
    assert first == "€1,021.06   ▲ +€15.20 +1.51% this week"


def test_old_number_keys_no_longer_switch_the_range() -> None:
    """1, 7 and 3 were replaced by D, W and M: pressing them leaves the range on today."""
    first = _shoot("ok-two-positions", ["7", "3"]).texts["portfolio"].splitlines()[0]
    assert first == "€1,021.06   ▲ +€12.30 +1.22% today"


def test_every_key_has_exactly_one_action() -> None:
    """No key of the home screen is bound twice, so D W M Y collide with nothing (#650)."""
    keys = [
        key.strip()
        for binding in HomeApp.BINDINGS
        if isinstance(binding, Binding)
        for key in binding.key.split(",")
    ]
    duplicates = sorted({key for key in keys if keys.count(key) > 1})
    assert (duplicates, {"d", "w", "m", "y"} <= set(keys)) == ([], True)


def test_footer_and_help_name_the_new_keys() -> None:
    """The footer and the help screen both list D W M Y, and neither mentions 30 or 1/7."""
    home = _shoot("ok-two-positions")
    helped = _shoot("ok-two-positions", ["question_mark"])
    footer = home.screen.splitlines()[-1]
    assert (
        footer.strip().startswith("D W M Y range"),
        "D day · W week · M month · Y year" in helped.screen,
        "press 3" in helped.screen,
    ) == (True, True, False)


def test_home_has_no_tax_footnote_and_no_usdt() -> None:
    """The home screen keeps only waterfall numbers: no tax explanation, no USDT, no signals."""
    shot = _shoot("ok-two-positions")
    assert [word in shot.screen for word in ("FIFO", "band", "USDT", "signal")] == [False] * 4


def test_question_mark_shows_the_tax_footnote() -> None:
    """The help screen carries the tax footnote that the home screen leaves out."""
    shot = _shoot("ok-two-positions", ["question_mark"])
    assert (_TAX_FOOTNOTE in shot.screen, shot.depth) == (True, 2)


def test_escape_leaves_the_help_screen() -> None:
    """Esc returns from the help screen to the home screen."""
    shot = _shoot("ok-two-positions", ["question_mark", "escape"])
    assert (shot.depth, "Shadow test" in shot.screen) == (1, True)


class _LiveModel:
    """A fixture model that claims to be live (no demo tag), for the logs panel."""

    def __init__(self, scenario: str) -> None:
        self._inner = FixtureReadModel.load(scenario)

    @property
    def demo_scenario(self) -> None:
        return None

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)


_JOURNAL = [
    LogLine(
        at=datetime(2026, 10, 3, 12, 0, 5, tzinfo=UTC),
        level="info",
        event="serve.bar.processed",
        fields=(("pair", "BTC/USDT"),),
        correlation_id="01J9Z8T3M5BQR2FVKN4XK7Q2ZP",
    ),
    LogLine(
        at=datetime(2026, 10, 3, 12, 1, 5, tzinfo=UTC),
        level="warning",
        event="feed.retry",
        fields=(("attempt", "2"),),
    ),
]


def _shoot_live(journal: Callable[[int], Sequence[LogLine] | None] | None) -> Shot:
    model = cast("ConsoleReadModel", _LiveModel("ok-two-positions"))
    shot, _ = asyncio.run(_drive(model, ["l"], (80, 24), journal=journal))
    return shot


def test_logs_key_shows_the_last_journal_lines() -> None:
    """L opens the journal as `hermes logs` prints it, in Madrid time."""
    asked: list[int] = []

    def journal(count: int) -> list[LogLine]:
        asked.append(count)
        return _JOURNAL

    shot = _shoot_live(journal)

    assert "14:00:05  INFO     serve.bar.processed  pair=BTC/USDT  …K7Q2ZP" in shot.screen
    assert "14:01:05  WARNING  feed.retry  attempt=2" in shot.screen
    assert (shot.depth, asked[0]) == (2, 50)


def test_logs_key_says_when_the_journal_is_not_readable() -> None:
    """On a dev box with no journal the panel says where to look instead."""
    shot = _shoot_live(lambda _count: None)

    assert "The journal is not readable here; run hermes logs on the VPS." in shot.screen


def test_logs_key_on_demo_data_says_there_are_no_logs() -> None:
    """Demo scenarios have no journal; the panel says so instead of showing real logs."""
    shot = _shoot("ok-two-positions", ["l"])

    assert "Logs come from the VPS journal; demo data has none." in shot.screen


def test_enter_opens_the_detail_as_a_screen_on_narrow_terminals() -> None:
    """At 80 columns Enter opens the detail full screen and Esc closes it."""
    opened = _shoot("ok-two-positions", ["enter"])
    closed = _shoot("ok-two-positions", ["enter", "escape"])
    assert (opened.depth, "Money if sold now" in opened.screen, closed.depth) == (2, True, 1)


def test_enter_opens_the_detail_beside_the_list_on_wide_terminals() -> None:
    """At 120 columns the detail appears in the right column and no screen is pushed."""
    before = _shoot("ok-two-positions", size=(120, 40))
    after = _shoot("ok-two-positions", ["enter"], size=(120, 40))
    assert (
        "Money if sold now" in before.screen,
        "Money if sold now" in after.screen,
        after.depth,
    ) == (False, True, 1)


def test_down_key_moves_the_cursor_and_the_detail_follows() -> None:
    """Down selects SOL; the opened detail is SOL's."""
    shot = _shoot("ok-two-positions", ["down", "enter"])
    assert shot.texts["positions"].count("▸SOL") == 1
    assert "SOL · 1d · long" in shot.screen


def test_demo_data_tag_is_visible_on_fixtures() -> None:
    """A fixture read model always shows DEMO DATA with its scenario name where the row has room.

    At 80 columns the mainnet badge leads the status row in every theme
    (issue #1097), so the scenario name only fits on a wider terminal.
    """
    shot = _shoot("ok-two-positions", size=(120, 36))
    assert shot.texts["demo-tag"].strip() == "DEMO DATA · scenario ok-two-positions"


def test_tui_is_an_operator_surface() -> None:
    """The TUI renders on the operator TUI surface, which may show P&L."""
    _, app = asyncio.run(_drive(FixtureReadModel.load("ok-two-positions"), [], (80, 24)))
    assert (app._render_context().surface, pnl_visible(Surface.OPERATOR_TUI)) == (
        Surface.OPERATOR_TUI,
        True,
    )


def test_screen_fits_80x24_with_footer_and_90_day_line() -> None:
    """At 80x24 the stacked screen ends with the 90-day line and the read-only footer."""
    lines = _shoot("ok-two-positions").screen.splitlines()
    assert lines[-2].startswith("Shadow test · day 12 of 90")
    assert lines[-1].endswith("read-only")


def test_big_total_appears_only_on_tall_terminals() -> None:
    """The big total shows from 30 rows; under 30 the text line leads with the total.

    From 30 rows the total is the big gradient number and the text opens with
    the change; at 24 rows the big number is hidden (#712).
    """

    async def run(size: tuple[int, int]) -> tuple[bool, str, str]:
        app = HomeApp(
            FixtureReadModel.load("ok-two-positions"), clock=lambda: _FROZEN, motion=False
        )
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            big = app.query_one("#portfolio-total", BigNumber)
            return big.display, big.figure, app.plain_text("portfolio").splitlines()[0]

    tall = asyncio.run(run((120, 40)))
    short = asyncio.run(run((80, 24)))
    assert (tall, short[0], short[2].startswith("€1,021.06")) == (
        (True, "€1,021.06", "▲ +€12.30 +1.22% today"),
        False,
        True,
    )


def test_big_digits_are_three_rows_tall() -> None:
    """A glyph is three rows; the digit zero is a closed box."""
    assert big_digits("0") == ("┌─┐", "│ │", "└─┘")


def test_refresh_interval_is_five_seconds() -> None:
    """The home screen re-reads the model every 5 seconds."""
    assert REFRESH_SECONDS == 5.0


class _SpyModel:
    """Delegates to a fixture and records every attribute the app touches."""

    def __init__(self, inner: FixtureReadModel) -> None:
        self._inner = inner
        self.touched: set[str] = set()
        self.heartbeats = 0

    def __getattr__(self, name: str) -> object:
        self.touched.add(name)
        if name == "heartbeat":
            self.heartbeats += 1
        return getattr(self._inner, name)


def test_app_refreshes_and_only_reads() -> None:
    """The app re-reads the model on a timer and touches nothing but read methods."""
    spy = _SpyModel(FixtureReadModel.load("ok-two-positions"))

    async def run() -> None:
        app = HomeApp(spy, refresh_seconds=0.05)  # type: ignore[arg-type]
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause(0.4)
            await pilot.press("enter")
            await pilot.press("question_mark")

    asyncio.run(run())
    assert (spy.heartbeats > 3, spy.touched <= _READ_METHODS) == (True, True)


def test_refresh_before_a_panel_mounts_keeps_the_app_running() -> None:
    """A refresh landing between push_screen and the panel's mount neither crashes nor blanks it."""

    async def run() -> tuple[bool, str]:
        app = HomeApp(FixtureReadModel.load("ok-two-positions"), refresh_seconds=3600)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            app.push_screen(_PanelScreen(lambda: "panel body"))
            app._redraw()
            await pilot.pause()
            return app.is_running, str(app.screen.query_one("#panel", Static).content)

    assert asyncio.run(run()) == (True, "panel body")


def test_tui_module_imports_no_adapter_or_port() -> None:
    """The TUI depends on the application layer only, so it cannot write to the ledger."""
    tree = ast.parse(Path(tui_module.__file__).read_text(encoding="utf-8"))
    imported = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    forbidden = {m for m in imported if m.startswith(("hermes.adapters", "hermes.ports"))}
    assert forbidden == set()


def test_q_quits_the_app() -> None:
    """Q ends the app."""

    async def run() -> bool:
        app = HomeApp(FixtureReadModel.load("ok-two-positions"))
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            await pilot.press("q")
            await pilot.pause()
            return not app.is_running

    assert asyncio.run(run()) is True


def test_svg_snapshot_80x24(snapshot: SnapshotAssertion) -> None:
    """The reviewed 80x24 rendering of ok-two-positions."""
    svg = _shoot("ok-two-positions").svg
    assert_view(snapshot, svg, record=False)


def test_svg_snapshot_120x36(snapshot: SnapshotAssertion) -> None:
    """The reviewed 120x36 rendering of ok-two-positions with the detail open."""
    svg = _shoot("ok-two-positions", ["enter"], size=(120, 36)).svg
    assert_view(snapshot, svg, record=False)


def test_svg_snapshot_160x45(snapshot: SnapshotAssertion) -> None:
    """The reviewed 160x45 (wide) rendering of ok-two-positions."""
    svg = _shoot("ok-two-positions", size=(160, 45)).svg
    assert_view(snapshot, svg, record=True)


def test_svg_snapshot_240x60(snapshot: SnapshotAssertion) -> None:
    """The reviewed 240x60 (ultra) rendering of ok-two-positions."""
    svg = _shoot("ok-two-positions", size=(240, 60)).svg
    assert_view(snapshot, svg, record=False)


def test_svg_snapshot_down_80x24(snapshot: SnapshotAssertion) -> None:
    """The reviewed 80x24 rendering while hermes is down: red row, dimmed money."""
    svg = _shoot("down-stale").svg
    assert_view(snapshot, svg, record=True)


def _top_line(release: Callable[[], str] | None, size: tuple[int, int] = (100, 30)) -> str:
    async def run() -> str:
        app = HomeApp(
            FixtureReadModel.load("ok-two-positions"),
            release=release,
            clock=lambda: _FROZEN,
            motion=False,
        )
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            return app.screen._compositor.render_strips()[_STATUS_ROW].text.rstrip()

    return asyncio.run(run())


def test_top_line_names_the_running_release() -> None:
    """The status row reads "release <name>" beside the heartbeat (#649).

    Since issue #711 the window bar and the tabs sit above it and the theme
    chip follows the heartbeat; since issue #1097 the mainnet badge leads it
    in every theme. The heartbeat is still drawn whole.
    """
    top = _top_line(lambda: "abc123def456", size=(140, 30))
    assert (
        top.startswith(
            "SHADOW · mainnet off  DEMO DATA · scenario ok-two-positions release abc123def456"
        ),
        "● live · checked 14:00 · next 18:00" in top,
    ) == (True, True)


def test_top_line_has_no_release_without_a_source() -> None:
    """Without a release source nothing about a release is drawn."""
    assert "release" not in _top_line(None)


def test_release_is_read_again_on_every_redraw() -> None:
    """A deploy flips the link while the screen is open: the next redraw shows the new name."""
    names = iter(["aaaaaaaaaaaa", "bbbbbbbbbbbb"])
    current = {"name": next(names)}

    async def run() -> tuple[str, str]:
        app = HomeApp(FixtureReadModel.load("ok-two-positions"), release=lambda: current["name"])
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            before = app.plain_text("release").strip()
            current["name"] = next(names)
            app._redraw()
            await pilot.pause()
            return before, app.plain_text("release").strip()

    assert asyncio.run(run()) == ("release aaaaaaaaaaaa", "release bbbbbbbbbbbb")
