"""The read-only operator home screen (``hermes tui``), driven with Textual's pilot.

Each test protects one rule of the approved console UX (issue #377 and the
operator feedback of 2026-10-03):

- the status is one dim line when all is well, amber when late, one red row
  plus dimmed money "as of" when down;
- the range keys change the hero change line and the sparkline together;
- Enter opens the position detail, beside the list on wide terminals;
- the tax footnote lives behind ``?`` only; ``L`` is a placeholder until #376;
- DEMO DATA shows on fixtures and the screen is an operator surface;
- the app only reads: it imports no adapter and calls only read-model methods.
"""

# The console prints the typographic minus (U+2212) and times sign on purpose.

from __future__ import annotations

import ast
import asyncio
import io
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console, RenderableType
from syrupy.assertion import SnapshotAssertion
from syrupy.extensions.image import SVGImageSnapshotExtension

from hermes.adapters.console import FixtureReadModel
from hermes.application.console import ConsoleReadModel, Surface, pnl_visible
from hermes.application.console.format import big_digits
from hermes.entrypoints import tui as tui_module
from hermes.entrypoints.tui import REFRESH_SECONDS, HomeApp

_TAX_FOOTNOTE = "est. · FIFO · 19% band · €5,988.30 left before 21%"
_READ_METHODS = {
    "demo_scenario",
    "heartbeat",
    "portfolio",
    "positions",
    "position",
    "activity",
    "shadow_test",
}
_WIDGETS = (
    "demo-tag",
    "heartbeat",
    "down-row",
    "portfolio",
    "positions",
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


def _repo_clean(svg: str) -> str:
    """Strip trailing whitespace, as the repository's pre-commit hook does to committed SVGs."""
    return "\n".join(line.rstrip() for line in svg.splitlines()) + "\n"


async def _drive(
    model: ConsoleReadModel, keys: Sequence[str], size: tuple[int, int]
) -> tuple[Shot, HomeApp]:
    app = HomeApp(model)
    async with app.run_test(size=size) as pilot:
        await pilot.pause()
        for key in keys:
            await pilot.press(key)
            await pilot.pause()
        texts = {name: app.plain_text(name) for name in _WIDGETS}
        styled = {name: _styled(content, size[0]) for name, content in app._content.items()}
        screen = "\n".join(strip.text.rstrip() for strip in app.screen._compositor.render_strips())
        svg = _repo_clean(app.export_screenshot())
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
    assert "\x1b[31m" in shot.styled["down-row"] or "\x1b[1;31m" in shot.styled["down-row"]
    assert "as of 18:04" in shot.texts["portfolio"]
    assert "\x1b[2m" in shot.styled["portfolio"]
    assert "as of 18:04" in shot.texts["positions"]


def test_total_and_change_lead_the_portfolio() -> None:
    """The portfolio block opens with the total in EUR and the change for the range."""
    shot = _shoot("ok-two-positions")
    first = shot.texts["portfolio"].splitlines()[0]
    assert first == "€1,021.06   ▲ +€12.30 +1.22% today"


def test_range_keys_change_hero_and_sparkline() -> None:
    """1, 7, 3 (30 days) and Y each change the change line and the sparkline together."""
    seen = {}
    for key in ("1", "7", "3", "y"):
        lines = _shoot("ok-two-positions", [key]).texts["portfolio"].splitlines()
        seen[key] = (lines[0], lines[2])
    assert seen == {
        "1": ("€1,021.06   ▲ +€12.30 +1.22% today", "▁▂▁▄▆█"),
        "7": ("€1,021.06   ▲ +€15.20 +1.51% this week", seen["7"][1]),
        "3": ("€1,021.06   ▲ +€19.00 +1.90% this month", seen["3"][1]),
        "y": ("€1,021.06   ▲ +€21.06 +2.11% this year", seen["y"][1]),
    }
    assert len({sparkline for _, sparkline in seen.values()}) == 4


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


def test_logs_key_shows_the_placeholder() -> None:
    """Until #376 lands, L says logs arrive with it."""
    shot = _shoot("ok-two-positions", ["l"])
    assert "Logs arrive with #376." in shot.screen


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
    """A fixture read model always shows DEMO DATA with its scenario name."""
    shot = _shoot("ok-two-positions")
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
    """The total is drawn three rows tall from 30 rows; at 24 rows it is one line."""
    tall = _shoot("ok-two-positions", size=(120, 40)).texts["portfolio"].splitlines()[0]
    short = _shoot("ok-two-positions").texts["portfolio"].splitlines()[0]
    assert (tall.startswith("╭─╴"), short.startswith("€1,021.06")) == (True, True)


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
    assert svg == snapshot(extension_class=SVGImageSnapshotExtension)


def test_svg_snapshot_120x40(snapshot: SnapshotAssertion) -> None:
    """The reviewed 120x40 rendering of ok-two-positions with the detail open."""
    svg = _shoot("ok-two-positions", ["enter"], size=(120, 40)).svg
    assert svg == snapshot(extension_class=SVGImageSnapshotExtension)


def test_svg_snapshot_down_80x24(snapshot: SnapshotAssertion) -> None:
    """The reviewed 80x24 rendering while hermes is down: red row, dimmed money."""
    svg = _shoot("down-stale").svg
    assert svg == snapshot(extension_class=SVGImageSnapshotExtension)
