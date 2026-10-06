"""The Shadow test screen, tab 5 (design spec screen 5, issue #718).

Invariants:

- the hero's figure is ``N / 90`` with the day capped at 90, and none during
  the warm-up (whose days never get the big number);
- the hero bar is the shared run bar: the same cells as the bottom 90-day
  line for the same day, and its fill never shrinks as the day grows;
- month ticks mark each first of a month inside the run; the warm-up marks
  days 30, 60 and 90;
- each check row shows a glyph, its name and only pass, fail or pending, in
  the data colours (pass up blue, fail down orange), then the mechanical note;
- the screen draws no money: no euro sign on any scenario, the only bps is the
  M-02 threshold in a check name;
- tab 5 opens with the key 5, shows each check state per scenario, and Esc
  returns to the previous tab;
- the big day number keeps the hero-digits colour (the legible big-number rule);
- the bottom 90-day line on every tab counts from the same day 0 as tab 5.
"""

from __future__ import annotations

import asyncio
import io
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from hypothesis import given
from hypothesis import strategies as st
from rich.console import Console, RenderableType
from textual.pilot import Pilot

from hermes.adapters.console import FixtureReadModel
from hermes.application.console.render import to_plain_text
from hermes.application.console.shadow_screen import (
    MECHANICAL_NOTE,
    CertificationCheck,
    CheckState,
    DayZeroSource,
    ShadowScreen,
    ShadowSources,
)
from hermes.entrypoints.tui import HomeApp
from hermes.entrypoints.tui_home import counted_line, run_bar
from hermes.entrypoints.tui_settings import ThemeChoice
from hermes.entrypoints.tui_shadow import (
    WARMUP_HEADLINE,
    checks_table,
    day_figure,
    hero_text,
    month_ticks,
    notebook_text,
)
from hermes.entrypoints.tui_theme import BASE, NEON
from hermes.entrypoints.tui_widgets import BigNumber

_MADRID = ZoneInfo("Europe/Madrid")
_FROZEN = datetime(2026, 10, 3, 12, 12, tzinfo=UTC)
_DAY0 = datetime(2026, 10, 15, tzinfo=UTC)


def _screen(day: int | None, *, day_zero_at: datetime | None = _DAY0) -> ShadowScreen:
    return ShadowScreen(
        source=DayZeroSource.SHADOW_START_RECORD,
        day_zero_at=day_zero_at if day is not None else None,
        day=day,
        seeded_at=datetime(2026, 10, 4, 8, tzinfo=UTC),
        warmup_day=3,
        record=None,
        checks=(),
        settings=(),
        notebook=None,
    )


def _plain(renderable: RenderableType, width: int = 100) -> str:
    return to_plain_text(renderable, width=width)


def _colours(renderable: RenderableType, width: int = 60) -> dict[str, str]:
    """Return each word segment's foreground colour, lower-case hex."""
    console = Console(width=width, color_system="truecolor", file=io.StringIO())
    out: dict[str, str] = {}
    for seg in console.render(renderable):
        colour = seg.style.color if seg.style else None
        if seg.text.strip() and colour is not None and colour.triplet is not None:
            out[seg.text.strip()] = colour.triplet.hex
    return out


@pytest.mark.parametrize(
    ("day", "expected"), [(None, None), (0, "0 / 90"), (45, "45 / 90"), (120, "90 / 90")]
)
def test_day_figure(day: int | None, expected: str | None) -> None:
    """Warm-up has no figure; a counting run shows N / 90 with N held at 90."""
    assert day_figure(_screen(day)) == expected


def test_hero_bar_is_the_shared_run_bar() -> None:
    """Day 45 draws exactly the run bar the bottom line draws for day 45."""
    hero = hero_text(_screen(45), BASE, width=65, tz=_MADRID, big=True)
    bar = run_bar(45, 90, BASE, width=60)
    assert _plain(hero).splitlines()[0] == f"{bar.plain}  50%"


def test_warm_up_hero_says_nothing_counts() -> None:
    """No record: the headline, the warm-up days in dim words, an empty bar, the start hint."""
    lines = _plain(hero_text(_screen(None), BASE, width=65, tz=_MADRID, big=False)).splitlines()
    assert (lines[0], lines[1], lines[2].count("█"), lines[4][:39]) == (
        WARMUP_HEADLINE,
        "3 warm-up days since the seed on 04 Oct · none of them count (ADR-021 §3)",
        0,
        "Day 0 starts with hermes shadow-start (",
    )


def test_hero_without_the_big_number_leads_with_a_bold_day_line() -> None:
    """Compact or short terminals: ``day 12 / 90`` in hero-digits leads the hero."""
    hero = hero_text(_screen(12), BASE, width=65, tz=_MADRID, big=False)
    assert _plain(hero).splitlines()[0] == "day 12 / 90"


def test_month_ticks_mark_each_first_of_a_month() -> None:
    """Day 0 on 15 Oct: Nov at day 17, Dec at day 47, Jan at day 78 of a 91-cell row."""
    ticks = month_ticks(date(2026, 10, 15), 90, 91, BASE).plain
    assert [(ticks.index(label) - 1, label) for label in ("Nov", "Dec", "Jan")] == [
        (17, "Nov"),
        (47, "Dec"),
        (78, "Jan"),
    ]


def test_warm_up_ticks_mark_days_30_60_90() -> None:
    """Without a day 0 the ticks are day marks; the last label sits before its tick."""
    assert month_ticks(None, 90, 91, BASE).plain.split() == ["╵30d", "╵60d", "90d╵"]


def test_check_rows_use_the_data_colours_and_only_three_words() -> None:
    """Pass is up blue, fail down orange, pending muted; then the mechanical note."""
    checks = (
        CertificationCheck("a", CheckState.PASS, "x"),
        CertificationCheck("b", CheckState.FAIL, "y"),
        CertificationCheck("c", CheckState.PENDING, "z"),
    )
    table = checks_table(checks, BASE)
    text = _plain(table, width=60)
    colours = _colours(table)
    assert (
        [line.split()[:3] for line in text.splitlines()[:3]],
        text.splitlines()[-1].strip(),
        (colours["pass"], colours["fail"], colours["pending"]),
    ) == (
        [["✓", "a", "pass"], ["✗", "b", "fail"], ["○", "c", "pending"]],
        MECHANICAL_NOTE,
        (BASE.up.lower(), BASE.down.lower(), BASE.muted.lower()),
    )


def test_notebook_missing_says_not_available_here() -> None:
    """A release without the notebook says so instead of an empty panel."""
    assert _plain(notebook_text(None, BASE, tz=_MADRID, rows=5)).strip() == (
        "not available here (the notebook ships with the release)"
    )


# --- pilot ---------------------------------------------------------------


def _run[T](
    scenario: str,
    probe: Callable[[HomeApp, Pilot[None]], Awaitable[T]],
    *,
    size: tuple[int, int] = (160, 45),
) -> T:
    async def drive() -> T:
        model = FixtureReadModel.load(scenario)
        app = HomeApp(
            model,
            clock=lambda: _FROZEN,
            motion=False,
            settings_path=None,
            theme=ThemeChoice.NEON,
            refresh_seconds=3600,
            shadow=ShadowSources(evidence=model, runs=model, notebook=model, strategy_id="demo"),
        )
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            return await probe(app, pilot)

    return asyncio.run(drive())


def _text(app: HomeApp) -> str:
    return "\n".join(strip.text.rstrip() for strip in app.screen._compositor.render_strips())


async def _open(pilot: Pilot[None]) -> None:
    await pilot.press("5")
    await pilot.pause()


def test_key_5_opens_the_shadow_screen_and_esc_goes_back() -> None:
    """5 shows tab 5 with its panels (not the placeholder); Esc returns Home."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[int, bool, bool, int]:
        await _open(pilot)
        shown, text = app.tab, _text(app)
        await pilot.press("escape")
        await pilot.pause()
        return shown, "CERTIFICATION CHECKS" in text, "coming next" in text, app.tab

    assert _run("ok-two-positions", probe) == (4, True, False, 0)


@pytest.mark.parametrize(
    ("scenario", "states"),
    [
        ("ok-two-positions", ["pending"] * 5),
        ("empty", ["pending"] * 5),
        ("down-stale", ["fail", "pending", "pending", "pending", "pending"]),
        ("losing-year", ["pass", "fail", "fail", "fail", "pass"]),
    ],
)
def test_each_scenario_shows_its_check_states(scenario: str, states: list[str]) -> None:
    """The check rows on screen carry the evaluated state of each of the five checks."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> list[str]:
        await _open(pilot)
        return [check.state.value for check in app.shadow_screen().checks]

    assert _run(scenario, probe) == states


def test_counting_hero_shows_the_big_day_and_the_record() -> None:
    """A recorded day 0: the big 12 / 90 in hero-digits, and the run id in the Day 0 panel."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[str, str, bool]:
        await _open(pilot)
        big = app.query_one("#shadow-day", BigNumber)
        rendered = big.render()
        colours = {str(span.style) for span in rendered.spans if "bold" in str(span.style)}
        return big.figure, colours.pop(), "swing-donchian-4h-20260921T000000Z" in _text(app)

    assert _run("ok-two-positions", probe) == ("12 / 90", f"bold {NEON.hero_digits}", True)


def test_warm_up_has_no_big_number_and_says_not_counting() -> None:
    """No record: the big number is hidden and the headline and start hint show."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[bool, bool, bool]:
        await _open(pilot)
        text = _text(app)
        big = app.query_one("#shadow-day", BigNumber)
        return big.display, WARMUP_HEADLINE in text, "No day-0 record yet." in text

    assert _run("empty", probe) == (False, True, True)


@pytest.mark.parametrize("scenario", ["ok-two-positions", "losing-year", "empty", "tax-pending"])
def test_the_shadow_screen_shows_no_money(scenario: str) -> None:
    """No euro, no dollar, and bps only in the M-02 check's name: the screen is P&L-blind."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[bool, int]:
        await _open(pilot)
        text = "\n".join(line for line in _text(app).splitlines()[3:-2] if line.strip())
        return ("€" in text or "$" in text), text.count("bps")

    assert _run(scenario, probe) == (False, 1)


def test_unreadable_record_turns_the_hero_into_one_line() -> None:
    """A broken day-0 file is said on the hero, and the other panels go blank."""
    from hermes.application.console.shadow_screen import ShadowRecordError  # noqa: PLC0415

    class _Broken:
        def latest_run(self, strategy_id: str) -> None:
            del strategy_id
            msg = "x.json: field 'day_zero_at' has no timezone offset"
            raise ShadowRecordError(msg)

    async def drive() -> str:
        model = FixtureReadModel.load("empty")
        app = HomeApp(
            model,
            clock=lambda: _FROZEN,
            motion=False,
            settings_path=None,
            refresh_seconds=3600,
            shadow=ShadowSources(evidence=model, runs=_Broken(), strategy_id="s"),
        )
        async with app.run_test(size=(120, 36)) as pilot:
            await _open(pilot)
            return app.plain_text("shadow-hero").strip()

    assert asyncio.run(drive()) == (
        "Shadow test · could not read the day-0 record: "
        "x.json: field 'day_zero_at' has no timezone offset"
    )


def test_day_counts_up_and_ninety_plus_holds_at_ninety() -> None:
    """Days past 90 never show more than 90 / 90 on the hero."""
    assert day_figure(_screen(90 + 4, day_zero_at=_DAY0 - timedelta(days=94))) == "90 / 90"


@given(
    st.integers(min_value=-10, max_value=200),
    st.integers(min_value=0, max_value=200),
    st.integers(min_value=1, max_value=200),
)
def test_run_bar_fill_is_monotonic_in_the_day(day: int, step: int, width: int) -> None:
    """A later day never fills fewer cells of the hero bar, and the bar keeps its width."""
    earlier = run_bar(day, 90, BASE, width=width).plain
    later = run_bar(day + step, 90, BASE, width=width).plain
    assert (len(earlier), len(later)) == (width, width)
    assert earlier.count("█") <= later.count("█") <= width


@pytest.mark.parametrize(
    ("scenario", "line"),
    [
        ("ok-two-positions", "Shadow test · day 12 of 90  "),
        ("empty", "Shadow test · warm-up · not counting"),
    ],
)
def test_bottom_line_counts_from_the_same_day_zero_on_every_tab(scenario: str, line: str) -> None:
    """Home and tab 5 show the same day: the bottom line reads the screen's day 0."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[str, str]:
        home = app.plain_text("shadow")
        await _open(pilot)
        return home, app.plain_text("shadow")

    home, shadow = _run(scenario, probe)
    assert (home.startswith(line), home == shadow) == (True, True)


def test_counted_line_without_a_day_is_words_only() -> None:
    """No counted day draws no bar: the warm-up line is its words alone."""
    line = counted_line("Shadow test · warm-up · not counting", None, 90, BASE, width=120)
    assert line.plain == "Shadow test · warm-up · not counting"
