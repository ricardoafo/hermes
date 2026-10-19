"""The Cockpit home screen at every breakpoint (issue #712), driven with Textual's pilot.

Invariants:

- the home body carries the breakpoint classes of the terminal size, and the
  third column (Signals, log tail) shows only at ultra width;
- the curve is hidden under 30 rows and twice as tall from 45 rows; compact
  width draws the sparkline line instead;
- the big total counts up to a new value with motion on and jumps with motion
  off; the shimmer never starts with motion off;
- a big number's digits are one bold ``hero-digits`` colour in every theme,
  night and stale included; the gradient and the shimmer live only on the
  1-row ``hero-underline`` (the Code Owner, 2026-10-06); compact width keeps
  the plain bold total line;
- no position: the radar empty state names the watched coins and the next look;
- the exposure meter names the roster cap passed to the app, or says it is unknown;
- the log tail shows the journal at ultra width, or why it cannot;
- the Engine brief is the AI variant and has no brief yet;
- the app still calls only read-model read methods, at every width;
- reviewed SVG snapshots for the empty scenario and an empty live ledger at
  80x24, 120x36, 160x45 and 240x60, motion off and a frozen clock.
"""

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import cast

import pytest
from syrupy.assertion import SnapshotAssertion
from syrupy.extensions.image import SVGImageSnapshotExtension
from textual.pilot import Pilot

from hermes.adapters.console import FixtureReadModel
from hermes.adapters.console.ledger_read_model import LedgerReadModel
from hermes.application.console import ConsoleReadModel, PortfolioRange, PortfolioSummary
from hermes.application.console.logs import LogLine
from hermes.entrypoints.tui import HomeApp
from hermes.entrypoints.tui_home import LOGS_UNAVAILABLE
from hermes.entrypoints.tui_theme import HERO_RULE_GLYPH, MOTION
from hermes.entrypoints.tui_widgets import BigNumber, HudPanel
from tests.unit.adapters.console._ledger_builder import LedgerBuilder

_FROZEN = datetime(2026, 10, 3, 12, 12, tzinfo=UTC)
_CAP = Decimal("0.15")
_SIZES = ((80, 24), (120, 36), (160, 45), (240, 60))
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


def _repo_clean(svg: str) -> str:
    """Strip trailing whitespace, as the repository's pre-commit hook does to committed SVGs."""
    return "\n".join(line.rstrip() for line in svg.splitlines()) + "\n"


def _run[T](
    model: ConsoleReadModel,
    probe: Callable[[HomeApp, Pilot[None]], Awaitable[T]],
    *,
    size: tuple[int, int] = (120, 36),
    motion: bool = False,
    cap: Decimal | None = _CAP,
    journal: Callable[[int], Sequence[LogLine] | None] | None = None,
    at: datetime = _FROZEN,
) -> T:
    async def drive() -> T:
        app = HomeApp(
            model,
            clock=lambda: at,
            motion=motion,
            refresh_seconds=3600,
            exposure_cap=cap,
            journal=journal,
        )
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            return await probe(app, pilot)

    return asyncio.run(drive())


def _fixture(scenario: str = "ok-two-positions") -> ConsoleReadModel:
    return FixtureReadModel.load(scenario)


def _screen(app: HomeApp) -> str:
    return "\n".join(strip.text.rstrip() for strip in app.screen._compositor.render_strips())


class _Live:
    """A fixture that claims to be live data (no demo tag), for the journal tail."""

    def __init__(self, scenario: str = "ok-two-positions") -> None:
        self._inner = FixtureReadModel.load(scenario)

    @property
    def demo_scenario(self) -> None:
        return None

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)


class _Growing:
    """A fixture whose total value is €100 higher once ``grown`` is set."""

    def __init__(self) -> None:
        self._inner = FixtureReadModel.load("ok-two-positions")
        self.grown = False

    def portfolio(self, range_: PortfolioRange = PortfolioRange.TODAY) -> PortfolioSummary:
        summary = self._inner.portfolio(range_)
        bump = Decimal(100) if self.grown else Decimal(0)
        return replace(summary, total_value=summary.total_value + bump)

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)


class _Spy:
    """Delegates to a fixture and records every attribute the app touches."""

    def __init__(self) -> None:
        self._inner = FixtureReadModel.load("ok-two-positions")
        self.touched: set[str] = set()

    def __getattr__(self, name: str) -> object:
        self.touched.add(name)
        return getattr(self._inner, name)


# ----------------------------------------------------------------------
# Breakpoints
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("size", "classes", "third_column"),
    [
        ((80, 24), {"-compact", "-short"}, False),
        ((120, 36), {"-standard", "-tall"}, False),
        ((160, 45), {"-wide", "-towering"}, False),
        ((240, 60), {"-ultra", "-towering"}, True),
    ],
)
def test_body_carries_the_breakpoint_classes(
    size: tuple[int, int], classes: set[str], third_column: bool
) -> None:
    """The body's classes name the width and height tokens; only ultra shows the third column."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[set[str], bool]:
        del pilot
        body = app.query_one("#body")
        shown = app.query_one("#third-column").display and "SIGNALS" in _screen(app)
        return set(body.classes) & {*classes, "-standard", "-wide", "-ultra", "-tall"}, shown

    assert _run(_fixture(), probe, size=size) == (classes, third_column)


def test_curve_doubles_from_45_rows_and_hides_under_30() -> None:
    """At 160 columns the curve is 6 braille rows at 44 rows, 12 at 45, and hidden at 29.

    The axis and caption lines under the curve (issue #713) are not curve rows.
    """

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[int, bool]:
        del pilot
        shown = app.query_one("#portfolio-curve").display
        lines = app.plain_text("portfolio-curve").splitlines()
        return sum(1 for line in lines if line and "⠀" <= line[0] <= "⣿"), shown

    heights = {rows: _run(_fixture(), probe, size=(160, rows)) for rows in (29, 44, 45)}
    assert (heights[44], heights[45], heights[29][1]) == ((6, True), (12, True), False)


def test_compact_draws_the_sparkline_line_instead_of_the_curve() -> None:
    """At 80 columns the portfolio text keeps the sparkline and the curve widget is hidden."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[str, bool]:
        del pilot
        return app.plain_text("portfolio").splitlines()[2], app.query_one(
            "#portfolio-curve"
        ).display

    assert _run(_fixture(), probe, size=(80, 40)) == ("▁▂▁▄▆█", False)


# ----------------------------------------------------------------------
# Big total
# ----------------------------------------------------------------------


def test_big_total_counts_up_with_motion_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """A €100 rise counts up: mid-way the number is between, after 0.6 s it is the new total."""
    model = _Growing()

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[str, str, str]:
        del pilot
        # The animator reads its own clock, so the frames are stepped by hand:
        # a busy CI runner can stall a real pause past the whole count-up.
        now = [app.animator._get_time()]
        monkeypatch.setattr(app.animator, "_get_time", lambda: now[0])
        big = app.query_one("#portfolio-total", BigNumber)
        before = big.value
        model.grown = True
        app._redraw()
        now[0] += MOTION.slow / 3
        app.animator()
        during = big.value
        now[0] += MOTION.slow
        app.animator()
        return before, during, big.value

    before, during, after = _run(cast("ConsoleReadModel", model), probe, motion=True)
    assert (before, during not in {before, after}, after) == ("€1,021.06", True, "€1,121.06")


def test_big_total_jumps_with_motion_off_and_never_shimmers() -> None:
    """Motion off: the new total shows at once and a shimmer request does nothing."""
    model = _Growing()

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[str, float | None]:
        del pilot
        big = app.query_one("#portfolio-total", BigNumber)
        model.grown = True
        app._redraw()
        big.start_shimmer()
        return big.value, big.shimmer_at

    assert _run(cast("ConsoleReadModel", model), probe) == ("€1,121.06", None)


def test_shimmer_pass_moves_along_the_underline_and_ends() -> None:
    """A shimmer pass moves along the underline, leaves the digits one colour, and ends."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[bool, set[str], float | None]:
        big = app.query_one("#portfolio-total", BigNumber)
        big.start_shimmer()
        await pilot.pause(0.3)
        moving = big.shimmer_at is not None and big.shimmer_at > 0
        digits = _digit_styles(big)
        await pilot.pause(2.5)
        return moving, digits, big.shimmer_at

    assert _run(_fixture(), probe, motion=True) == (True, {"bold #C8FBFF"}, None)


def _digit_styles(big: BigNumber) -> set[str]:
    """Return the styles of the drawn glyph cells on the three digit rows."""
    text = big.render()
    digits_end = text.plain.rindex("\n")
    return {
        str(span.style)
        for span in text.spans
        if span.start < digits_end and text.plain[span.start : span.end].strip()
    }


_NIGHT = datetime(2026, 10, 3, 21, 30, tzinfo=UTC)


@pytest.mark.parametrize(
    ("scenario", "at", "colour"),
    [
        ("ok-two-positions", _FROZEN, "#C8FBFF"),
        ("ok-two-positions", _NIGHT, "#E6ECF5"),
        ("down-stale", _FROZEN, "#C8FBFF"),
    ],
    ids=["neon", "night", "stale"],
)
def test_big_number_digits_are_one_full_contrast_colour(
    scenario: str, at: datetime, colour: str
) -> None:
    """Neon, night (23:30 Madrid) and a stale total all draw the digits in bold hero-digits.

    Under the digits is one row of the underline glyph, as wide as the digits.
    """

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[set[str], str, int]:
        del pilot
        big = app.query_one("#portfolio-total", BigNumber)
        rows = big.render().plain.split("\n")
        return _digit_styles(big), rows[3], len(rows[0])

    styles, underline, width = _run(_fixture(scenario), probe, at=at)
    assert (styles, underline) == ({f"bold {colour}"}, HERO_RULE_GLYPH * width)


def test_compact_width_keeps_the_plain_bold_total() -> None:
    """At 80x40 (tall but compact) the big number hides; the text line leads with the total."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[bool, str]:
        del pilot
        first = app.plain_text("portfolio").splitlines()[0]
        return app.query_one("#portfolio-total", BigNumber).display, first

    shown, first = _run(_fixture(), probe, size=(80, 40))
    assert (shown, first.startswith("€1,021.06   ▲")) == (False, True)


# ----------------------------------------------------------------------
# Panels
# ----------------------------------------------------------------------


def test_empty_positions_show_the_radar_and_the_next_look() -> None:
    """The empty scenario watches no pair yet, so it names the market and the next bar check."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> list[str]:
        del pilot
        return app.plain_text("positions").splitlines()

    lines = _run(_fixture("empty"), probe)
    assert (lines[0], lines[2]) == (
        "⠁ No open positions",
        "hermes is watching the market and will look again at 18:00",
    )


def test_empty_state_names_the_watched_coins() -> None:
    """With pairs in the market watch the empty state lists them and their soonest next check."""
    inner = FixtureReadModel.load("ok-two-positions")

    class _NoPositions:
        def positions(self) -> tuple[()]:
            return ()

        def __getattr__(self, name: str) -> object:
            return getattr(inner, name)

    async def probe(app: HomeApp, pilot: Pilot[None]) -> str:
        del pilot
        return app.plain_text("positions").splitlines()[2]

    line = _run(cast("ConsoleReadModel", _NoPositions()), probe)
    assert line == "hermes is watching BTC ETH SOL and will look again at 18:00"


def test_exposure_meter_uses_the_cap_given_to_the_app() -> None:
    """A 0.15 cap reads '23.1% of 15% cap'; no cap says it is not known."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> str:
        del pilot
        return app.plain_text("portfolio-meters").splitlines()[-1].strip()

    with_cap = _run(_fixture(), probe)
    without = _run(_fixture(), probe, cap=None)
    assert (with_cap.endswith("23.1% of 15% cap"), without) == (
        True,
        "exposure 23.1% · cap unknown",
    )


def test_log_tail_shows_the_journal_at_ultra_width() -> None:
    """Live data at 240 columns: the tail asks for 20 lines and prints them in Madrid time."""
    asked: list[int] = []
    line = LogLine(
        at=datetime(2026, 10, 3, 12, 0, 5, tzinfo=UTC),
        level="info",
        event="serve.bar.processed",
        fields=(("pair", "BTC/USDT"),),
    )

    def journal(count: int) -> list[LogLine]:
        asked.append(count)
        return [line]

    async def probe(app: HomeApp, pilot: Pilot[None]) -> str:
        del pilot
        return app.plain_text("log-tail").strip()

    text = _run(cast("ConsoleReadModel", _Live()), probe, size=(240, 60), journal=journal)
    assert (text.startswith("14:00:05  INFO     serve.bar.processed"), asked[0]) == (True, 20)


def test_log_tail_says_when_no_journal_is_readable() -> None:
    """Live data without a journal on this machine: the tail says where to look."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> str:
        del pilot
        return app.plain_text("log-tail").strip()

    text = _run(
        cast("ConsoleReadModel", _Live()), probe, size=(240, 60), journal=lambda _count: None
    )
    assert text == LOGS_UNAVAILABLE


def test_engine_brief_is_the_ai_variant_with_no_brief_yet() -> None:
    """The Engine brief panel is labelled for the AI supervisor and has no brief yet."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[str, bool, str]:
        del pilot
        panel = app.query_one("#brief-panel", HudPanel)
        return panel.label, panel.ai, app.plain_text("brief").strip()

    assert _run(_fixture(), probe) == ("✦ AI supervisor", True, "brief not available yet")


def test_app_only_reads_at_every_width() -> None:
    """At 240 columns (every panel drawn) the app touches nothing but read methods."""
    spy = _Spy()

    async def probe(app: HomeApp, pilot: Pilot[None]) -> None:
        del app
        await pilot.press("enter")

    _run(cast("ConsoleReadModel", spy), probe, size=(240, 60))
    assert spy.touched <= _READ_METHODS
    assert "position" in spy.touched


# ----------------------------------------------------------------------
# Snapshots
# ----------------------------------------------------------------------


def _svg(model: ConsoleReadModel, size: tuple[int, int]) -> str:
    async def probe(app: HomeApp, pilot: Pilot[None]) -> str:
        del pilot
        return _repo_clean(app.export_screenshot())

    return _run(model, probe, size=size)


@pytest.mark.parametrize("size", _SIZES, ids=lambda s: f"{s[0]}x{s[1]}")
def test_svg_snapshot_empty(size: tuple[int, int], snapshot: SnapshotAssertion) -> None:
    """The reviewed rendering of the empty demo scenario at each breakpoint."""
    assert _svg(_fixture("empty"), size) == snapshot(extension_class=SVGImageSnapshotExtension)


@pytest.mark.parametrize("size", _SIZES, ids=lambda s: f"{s[0]}x{s[1]}")
def test_svg_snapshot_empty_live_ledger(
    size: tuple[int, int], snapshot: SnapshotAssertion, tmp_path: Path
) -> None:
    """The reviewed rendering of a fresh, empty live ledger at each breakpoint."""
    path = tmp_path / "hermes.sqlite3"
    LedgerBuilder(path).close()
    model = LedgerReadModel(path, clock=lambda: _FROZEN)
    assert _svg(model, size) == snapshot(extension_class=SVGImageSnapshotExtension)
