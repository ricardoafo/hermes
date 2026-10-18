"""The Cockpit console chrome (issue #711), driven with Textual's pilot.

Invariants:

- every key has one action; 1-9 jump to screens, Tab / Shift+Tab cycle them,
  Esc goes back, Ctrl+P opens the command palette, D W M Y still set the range;
- screens not built yet name the issue that builds them (System is built, #717);
- every home card sits in a HudPanel labelled "computed";
- the live dot is drawn in the live colour (not dim) when OK; late is amber
  and down is red, as before;
- the terminal tab title follows the heartbeat and is written as OSC 2;
- the clock and the countdown tick every second from the injected clock;
- Auto switches to the night theme at 23:00 local; a hand-picked theme stays;
- T opens the theme picker and the pick is saved (to a tmp path, never home);
- with motion off no timer-driven effect runs;
- the design-conformance block at the end walks every screen key, the
  picker and palette paths, Esc in each context, every range key, the dot
  per theme and state, and both night edges (``design-check.md``).

Every app here gets a frozen clock; none reads or writes the real home folder.
"""

from __future__ import annotations

import asyncio
import io
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from rich.console import Console, RenderableType
from rich.text import Text
from textual.binding import Binding
from textual.command import CommandPalette
from textual.pilot import Pilot
from textual.widgets import ContentSwitcher, OptionList, Static

from hermes.adapters.console import FixtureReadModel
from hermes.application.console import Heartbeat, HeartbeatState, Surface
from hermes.entrypoints import tui as tui_module
from hermes.entrypoints.commands.tui import run_tui
from hermes.entrypoints.tui import (
    TABS,
    HomeApp,
    countdown_text,
    osc_title,
    terminal_title,
    title_total,
)
from hermes.entrypoints.tui_settings import ThemeChoice, load_settings
from hermes.entrypoints.tui_theme import BASE, CONTROL_ROOM, MOTION, NEON, NIGHT, pulse_steps
from hermes.entrypoints.tui_widgets import HudPanel, TabBar, ThemePicker, WindowBar

_MADRID = ZoneInfo("Europe/Madrid")
# The fixture's read time: 14:12 Madrid, next check 18:00 Madrid.
_DAY = datetime(2026, 10, 3, 12, 12, tzinfo=UTC)
_NIGHT = datetime(2026, 10, 3, 21, 30, tzinfo=UTC)  # 23:30 Madrid


class _Clock:
    """A clock the test moves by hand."""

    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def _run[T](
    probe: Callable[[HomeApp, Pilot[None]], Awaitable[T]],
    *,
    scenario: str = "ok-two-positions",
    size: tuple[int, int] = (120, 36),
    clock: Callable[[], datetime] = lambda: _DAY,
    motion: bool = False,
    settings_path: Path | None = None,
) -> T:
    async def drive() -> T:
        app = HomeApp(
            FixtureReadModel.load(scenario),
            clock=clock,
            motion=motion,
            settings_path=settings_path,
            refresh_seconds=3600,
        )
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            return await probe(app, pilot)

    return asyncio.run(drive())


async def _press(pilot: Pilot[None], keys: Sequence[str]) -> None:
    for key in keys:
        await pilot.press(key)
        await pilot.pause()


def _screen_text(app: HomeApp) -> str:
    return "\n".join(strip.text.rstrip() for strip in app.screen._compositor.render_strips())


def _bound_keys() -> list[str]:
    return [
        key.strip()
        for binding in HomeApp.BINDINGS
        if isinstance(binding, Binding)
        for key in binding.key.split(",")
    ]


# ----------------------------------------------------------------------
# Key map
# ----------------------------------------------------------------------


def test_no_key_is_bound_twice() -> None:
    """Every key of the console has exactly one action, Ctrl+P included."""
    keys = [*_bound_keys(), HomeApp.COMMAND_PALETTE_BINDING]
    assert sorted({key for key in keys if keys.count(key) > 1}) == []


def test_number_keys_one_to_nine_each_name_a_screen() -> None:
    """Keys 1-9 are bound, one per tab, and nothing binds 0 yet."""
    keys = set(_bound_keys())
    assert ({str(n) for n in range(1, 10)} <= keys, {"0"} & keys) == (True, set())


def test_number_keys_switch_screens() -> None:
    """Pressing 3 shows Positions, pressing 1 returns Home."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[int, str, int]:
        await _press(pilot, ["3"])
        shown, text = app.tab, _screen_text(app)
        await _press(pilot, ["1"])
        return shown, text, app.tab

    shown, text, back = _run(probe)
    assert (shown, "Positions · coming next · issue #715" in text, back) == (2, True, 0)


def test_every_unbuilt_screen_names_its_issue() -> None:
    """Each screen not built yet says "coming next · issue #N" with its own issue."""
    unbuilt = [tab for tab in TABS[1:] if tab.issue is not None]

    async def probe(app: HomeApp, pilot: Pilot[None]) -> list[bool]:
        seen = []
        for tab in unbuilt:
            await _press(pilot, [tab.key])
            seen.append(f"{tab.name} · coming next · issue #{tab.issue}" in _screen_text(app))
        return seen

    assert _run(probe) == [True] * len(unbuilt)


def test_tab_and_shift_tab_cycle_the_screens() -> None:
    """Tab moves Home to Signals; Shift+Tab from Home wraps to Logs."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[int, int]:
        await _press(pilot, ["tab"])
        forward = app.tab
        await _press(pilot, ["1", "shift+tab"])
        return forward, app.tab

    assert _run(probe) == (1, 8)


def test_escape_goes_back_to_the_previous_screen() -> None:
    """After 2 then 7, Esc returns to Signals, then to Home."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[int, int]:
        await _press(pilot, ["2", "7", "escape"])
        first = app.tab
        await _press(pilot, ["escape"])
        return first, app.tab

    assert _run(probe) == (1, 0)


def test_ctrl_p_opens_the_command_palette() -> None:
    """Ctrl+P opens Textual's command palette."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> bool:
        await _press(pilot, ["ctrl+p"])
        return isinstance(app.screen, CommandPalette)

    assert _run(probe) is True


def test_palette_offers_screens_and_the_three_themes_only() -> None:
    """The palette lists Go to <screen> and the hermes theme choices, not Textual's theme list."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> list[str]:
        del pilot
        return [command.title for command in app.get_system_commands(app.screen)]

    titles = _run(probe)
    assert (
        [t for t in titles if t.startswith("Go to")],
        [t for t in titles if t.startswith("Theme")],
    ) == (
        [f"Go to {tab.name}" for tab in TABS],
        ["Theme: Neon", "Theme: Control room", "Theme: Auto (night 23:00-08:00)"],
    )


def test_range_keys_still_set_the_range_on_another_screen() -> None:
    """W on the Signals screen switches the home range to this week."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> str:
        await _press(pilot, ["2", "w", "1"])
        return app.plain_text("portfolio")

    assert "+€15.20 +1.51% this week" in _run(probe)


# ----------------------------------------------------------------------
# Chrome
# ----------------------------------------------------------------------


def test_every_home_card_sits_in_a_computed_hud_panel() -> None:
    """Portfolio, positions, market, activity and detail are each framed and labelled computed.

    Issue #712 renamed the market panel "Market" (the design spec's name).
    """

    async def probe(app: HomeApp, pilot: Pilot[None]) -> dict[str, tuple[str, str]]:
        del pilot
        cards = ("portfolio", "positions", "market", "activity", "detail")
        found = {}
        for card in cards:
            parent = app.query_one(f"#{card}").parent
            assert isinstance(parent, HudPanel)
            found[card] = (parent.panel_title, parent.label)
        return found

    assert _run(probe) == {
        "portfolio": ("Portfolio", "computed"),
        "positions": ("Positions", "computed"),
        "market": ("Market", "computed"),
        "activity": ("Activity", "computed"),
        "detail": ("Position detail", "computed"),
    }


def test_briefs_screen_is_the_ai_variant() -> None:
    """The Briefs screen's panel is the AI supervisor variant."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[bool, str]:
        del pilot
        panel = app.query_one("#screen-7").parent
        assert isinstance(panel, HudPanel)
        return panel.ai, panel.label

    assert _run(probe) == (True, "✦ AI supervisor")


def test_window_bar_and_tabs_lead_the_screen() -> None:
    """Row 0 is the window bar with the clock; row 1 the tabs with names at 120 columns."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> list[str]:
        del pilot
        return _screen_text(app).splitlines()[:2]

    bar, tabs = _run(probe)
    assert (bar.startswith("▐ HERMES // SHADOW CONSOLE"), bar.endswith("14:12:00"), tabs) == (
        True,
        True,
        "  1 Home · 2 Signals · 3 Positions · 4 Tax · 5 Lab · 6 Shadow test"
        " · 7 Briefs · 8 System · 9 Logs",
    )


def test_compact_tabs_are_numbers_only_and_the_countdown_is_in_the_footer() -> None:
    """At 80 columns the tabs drop their names and the footer ends with the countdown."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[str, str]:
        del pilot
        lines = _screen_text(app).splitlines()
        return lines[1], lines[-1]

    tabs, footer = _run(probe, size=(80, 24))
    assert (tabs, footer.endswith("next 3h 48m 00s · read-only")) == (
        "  1   2   3   4   5   6   7   8   9",
        True,
    )


def test_wide_status_row_shows_the_countdown() -> None:
    """At 120 columns the countdown follows the heartbeat on the status row."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[str, str]:
        del pilot
        return app.plain_text("countdown").strip(), app.plain_text("footer-status").strip()

    assert _run(probe) == ("in 3h 48m 00s", "read-only")


def test_clock_and_countdown_tick_every_second() -> None:
    """One second later the window bar clock and the countdown have both moved."""
    clock = _Clock(_DAY)

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[str, str]:
        clock.now = _DAY + timedelta(seconds=1)
        await pilot.pause(1.3)
        return app.query_one(WindowBar).clock, app.plain_text("countdown").strip()

    assert _run(probe, clock=clock) == ("14:12:01", "in 3h 47m 59s")


def test_footer_keys_are_reverse_video_chips() -> None:
    """The footer's first cell is a reversed key chip."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[str, bool | None]:
        del pilot
        footer = app._content["footer"]
        assert isinstance(footer, Text)
        return footer.plain[:14], footer.get_style_at_offset(Console(), 1).reverse

    assert _run(probe) == (" D W M Y range", True)


# ----------------------------------------------------------------------
# Live dot and terminal title
# ----------------------------------------------------------------------


def _dot_and_text_styles(content: RenderableType, dot: str) -> tuple[str, str]:
    """Render to segments and return the style of the dot and of the text after it."""
    console = Console(width=120, file=io.StringIO(), color_system="truecolor")
    segments = [s for s in console.render(content) if s.text.strip()]
    index = next(i for i, s in enumerate(segments) if s.text.lstrip().startswith(dot))
    after = segments[index + 1] if segments[index].text.strip() == dot else segments[index]
    return str(segments[index].style), str(after.style)


def test_ok_dot_is_live_coloured_and_the_words_stay_dim() -> None:
    """OK: the dot is bold in Neon's live colour, not dim; the words are dim (#711)."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[str, str]:
        del pilot
        return _dot_and_text_styles(app._content["heartbeat"], "●")

    dot, words = _run(probe)
    assert (dot, words) == ("bold #00f0ff", "dim")


def test_late_dot_stays_amber() -> None:
    """Late: the ◐ line keeps the amber (yellow) style."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> str:
        del pilot
        return _dot_and_text_styles(app._content["heartbeat"], "◐")[0]

    assert _run(probe, scenario="late") == "yellow"


def test_down_row_stays_red() -> None:
    """Down: the ✖ row keeps the bold red style."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> str:
        del pilot
        return _dot_and_text_styles(app._content["down-row"], "✖")[0]

    assert _run(probe, scenario="down-stale") == "bold red"


def test_pulse_cycles_the_dot_through_four_colours() -> None:
    """Each pulse step redraws the dot in the next of the four pulse colours."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> list[str]:
        del pilot
        seen = []
        for _ in range(4):
            app._step_pulse()
            seen.append(_dot_and_text_styles(app._content["heartbeat"], "●")[0])
        return seen

    steps = pulse_steps(NEON)
    assert _run(probe) == [f"bold {colour.lower()}" for colour in (*steps[1:], steps[0])]


def test_terminal_title_names_each_state() -> None:
    """OK, late and down titles, and a bare "hermes" without a heartbeat."""
    base = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
    ok = Heartbeat(HeartbeatState.OK, base, base, base + timedelta(hours=4))
    late = Heartbeat(HeartbeatState.LATE, base, base, base)
    down = Heartbeat(HeartbeatState.DOWN, base, base, base, down_since=base)
    assert [terminal_title(h, _MADRID) for h in (ok, late, down, None)] == [
        "hermes ● live · next 18:00",
        "hermes ◐ check late · 14:00",
        "hermes ✖ down since 14:00",
        "hermes",
    ]


def test_terminal_title_carries_the_total_when_money_may_show() -> None:
    """With a total, OK and late titles carry it; the down title never does."""
    base = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
    ok = Heartbeat(HeartbeatState.OK, base, base, base + timedelta(hours=4))
    late = Heartbeat(HeartbeatState.LATE, base, base, base)
    down = Heartbeat(HeartbeatState.DOWN, base, base, base, down_since=base)
    assert [terminal_title(h, _MADRID, "€4,456") for h in (ok, late, down)] == [
        "hermes ● live · €4,456 · next 18:00",
        "hermes ◐ check late · €4,456 · 14:00",
        "hermes ✖ down since 14:00",
    ]


# SEV-1: Security axis S3 — money must never reach a surface pnl_visible blocks
@pytest.mark.parametrize(
    ("surface", "expected"),
    [
        (Surface.OPERATOR_TUI, "€4,456"),
        (Surface.OPERATOR_CLI, "€4,456"),
        (Surface.TELEGRAM_PUSHED, None),
        (Surface.SLACK, None),
        (Surface.SUPERVISOR_DESK, None),
    ],
)
def test_title_total_follows_pnl_visible(surface: Surface, expected: str | None) -> None:
    """The title total is whole euros on operator surfaces and absent wherever P&L is hidden."""
    assert title_total(Decimal("4455.50"), surface) == expected


def test_title_without_a_total_has_no_figure() -> None:
    """When pnl_visible withholds the total, the OK title is the money-free one."""
    base = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
    ok = Heartbeat(HeartbeatState.OK, base, base, base + timedelta(hours=4))
    total = title_total(Decimal("4455.50"), Surface.TELEGRAM_PUSHED)
    assert terminal_title(ok, _MADRID, total) == "hermes ● live · next 18:00"


def test_osc_title_is_one_escape_without_control_characters() -> None:
    """The title escape is ESC ] 2 ; text BEL, and control characters in the text are dropped."""
    assert osc_title("hermes\x07\x1b]0;evil ● live") == "\x1b]2;hermes]0;evil ● live\x07"


def test_app_writes_the_title_through_its_driver_once() -> None:
    """The OK fixture's title is written as OSC 2, and not again while it is unchanged."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> list[str]:
        del pilot
        written: list[str] = []
        driver = app._driver
        assert driver is not None
        driver.write = written.append  # type: ignore[method-assign,assignment]
        app._title_sent = None
        app._tick()
        app._tick()
        return written

    assert _run(probe) == ["\x1b]2;hermes ● live · €1,021 · next 18:00\x07"]


def test_countdown_reads_due_once_the_check_time_passed() -> None:
    """After the next check time the countdown says due; under an hour it drops the hours."""
    base = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
    ok = Heartbeat(HeartbeatState.OK, base, base, base + timedelta(hours=4))
    assert (
        countdown_text(ok, base + timedelta(hours=5)),
        countdown_text(ok, base + timedelta(hours=3, minutes=10)),
    ) == ("due", "in 50m 00s")


# ----------------------------------------------------------------------
# Themes
# ----------------------------------------------------------------------


def test_auto_is_neon_by_day() -> None:
    """At 14:12 Madrid Auto shows the Neon theme."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> str:
        del pilot
        return app.theme

    assert _run(probe) == "hermes-neon"


def test_auto_switches_to_night_at_23() -> None:
    """When the clock passes 23:00 Madrid, the next tick switches Auto to the night theme."""
    clock = _Clock(datetime(2026, 10, 3, 20, 59, 59, tzinfo=UTC))

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[str, str]:
        before = app.theme
        clock.now = datetime(2026, 10, 3, 21, 0, 0, tzinfo=UTC)
        await pilot.pause(1.3)
        return before, app.theme

    assert _run(probe, clock=clock) == ("hermes-neon", "hermes-night")


def test_night_data_palette_is_the_darker_one() -> None:
    """At night Rich content's green (gains) is the night up colour."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> str:
        del pilot
        return app.ansi_theme_dark.ansi_colors[2].hex.upper()

    assert _run(probe, clock=lambda: _NIGHT) == NIGHT.up


def test_day_data_palette_is_the_colour_blind_blue() -> None:
    """By day Rich content's green (gains) is blue (70,140,230)."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> str:
        del pilot
        return app.ansi_theme_dark.ansi_colors[2].hex.upper()

    assert _run(probe) == BASE.up


def test_a_hand_picked_theme_ignores_the_night(tmp_path: Path) -> None:
    """A saved Neon choice stays Neon at 23:30."""
    path = tmp_path / "console.toml"
    path.write_text('theme = "neon"\n', encoding="utf-8")

    async def probe(app: HomeApp, pilot: Pilot[None]) -> str:
        del pilot
        return app.theme

    assert _run(probe, clock=lambda: _NIGHT, settings_path=path) == "hermes-neon"


def test_t_opens_the_picker_and_the_pick_is_saved(tmp_path: Path) -> None:
    """T, up (from Auto to Control room), Enter: Control room shows and is saved."""
    path = tmp_path / "console.toml"

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[bool, str]:
        await _press(pilot, ["t"])
        opened = isinstance(app.screen, ThemePicker)
        await _press(pilot, ["up", "enter"])
        return opened, app.theme

    opened, theme = _run(probe, settings_path=path)
    assert (opened, theme, load_settings(path).theme) == (
        True,
        "hermes-control-room",
        ThemeChoice.CONTROL,
    )


def test_escape_closes_the_picker_without_a_change(tmp_path: Path) -> None:
    """Esc in the picker keeps Auto and writes nothing."""
    path = tmp_path / "console.toml"

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[str, ThemeChoice]:
        await _press(pilot, ["t", "up", "escape"])
        return app.theme, app.theme_choice

    assert (_run(probe, settings_path=path), path.exists()) == (
        ("hermes-neon", ThemeChoice.AUTO),
        False,
    )


def test_cli_theme_flag_saves_the_choice(tmp_path: Path) -> None:
    """`hermes tui --theme control` saves the choice where the app reads it."""
    path = tmp_path / "console.toml"
    rc = run_tui(
        ["--theme", "control"],
        io.StringIO(),
        demo_flag="ok-two-positions",
        runner=lambda model: None,
        settings_path=path,
    )
    assert (rc, load_settings(path).theme) == (0, ThemeChoice.CONTROL)


def test_cli_rejects_an_unknown_theme(tmp_path: Path) -> None:
    """An unknown theme name is a usage error and nothing is saved."""
    path = tmp_path / "console.toml"
    err = io.StringIO()
    rc = run_tui(
        ["--theme=sepia"],
        io.StringIO(),
        demo_flag="ok-two-positions",
        runner=lambda model: None,
        err=err,
        settings_path=path,
    )
    assert (rc, path.exists(), "--theme neon|control|auto" in err.getvalue()) == (2, False, True)


def test_cli_accepts_db_and_theme_together(tmp_path: Path) -> None:
    """`--theme=auto --db PATH` parses both; the db path reaches the read-model resolver."""
    path = tmp_path / "console.toml"
    err = io.StringIO()
    rc = run_tui(
        ["--theme=auto", "--db", str(tmp_path / "absent.db")],
        io.StringIO(),
        runner=lambda model: None,
        err=err,
        settings_path=path,
    )
    assert (rc, "bad arguments" in err.getvalue(), path.exists()) == (2, False, False)


# ----------------------------------------------------------------------
# Motion
# ----------------------------------------------------------------------


def test_motion_off_runs_no_effect_timer() -> None:
    """With motion off there is no effect timer and the live dot stays still."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[dict[str, object], str]:
        await pilot.pause(1.3)
        return dict(app.effect_timers), app.live_dot_colour

    assert _run(probe, motion=False) == ({}, NEON.live)


def test_motion_on_runs_the_pulse_shimmer_and_radar() -> None:
    """With motion on by day the pulse, the shimmer and the radar run.

    Issue #712 added the shimmer and the radar; issue #713 removed the binary
    strip (Code Owner 2026-10-06), so no strip timer runs.
    """

    async def probe(app: HomeApp, pilot: Pilot[None]) -> list[str]:
        await pilot.pause(1.3)
        return sorted(app.effect_timers)

    assert _run(probe, motion=True) == ["pulse", "radar", "shimmer"]


def test_night_runs_only_the_slow_pulse() -> None:
    """At night only the pulse runs, stepping every 0.8 s instead of 0.4 s."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[list[str], float]:
        del pilot
        pulse = app.effect_timers["pulse"]
        return sorted(app.effect_timers), pulse._interval

    assert _run(probe, motion=True, clock=lambda: _NIGHT) == (["pulse"], 0.8)


@pytest.fixture
def frozen_clock_timer(monkeypatch: pytest.MonkeyPatch) -> None:
    """Push the 1 s clock timer out of the test's reach, so only explicit ticks run."""
    monkeypatch.setattr(tui_module, "MOTION", replace(tui_module.MOTION, clock_tick=3600.0))


def _capture_title(app: HomeApp) -> list[str]:
    written: list[str] = []
    driver = app._driver
    assert driver is not None
    driver.write = written.append  # type: ignore[method-assign,assignment]
    app._title_sent = None
    return written


_OK_TITLE = "\x1b]2;hermes ● live · €1,021 · next 18:00\x07"


@pytest.mark.usefixtures("frozen_clock_timer")
def test_effect_steps_survive_widgets_torn_down() -> None:
    """A pulse step, a feed redraw or a clock tick after the status widgets are gone survives.

    The clock timer is pushed out of reach, so the tick after the teardown is
    the explicit one below, not a race with the 1 s timer.
    """

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[int, bool, list[str]]:
        await app.query_one("#heartbeat").remove()
        await app.query_one(WindowBar).remove()
        app._step_pulse()
        app._dim_feed()
        written = _capture_title(app)
        app._tick()
        await pilot.pause()
        return app._pulse, app.is_running, written

    assert _run(probe) == (1, True, [_OK_TITLE])


# Every widget the clock tick or the theme switch looks up.
_TICKED_WIDGETS = (
    "WindowBar",
    "ThemeChip",
    "#countdown",
    "#footer-status",
    "#heartbeat",
    "#down-row",
)


@pytest.mark.usefixtures("frozen_clock_timer")
@pytest.mark.parametrize("selector", _TICKED_WIDGETS)
def test_tick_and_theme_survive_any_ticked_widget_torn_down(selector: str) -> None:
    """With any one ticked widget gone, a tick and a theme pass still set the title and theme."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[list[str], str, bool]:
        await app.query_one(selector).remove()
        written = _capture_title(app)
        app._tick()
        app._apply_theme(_NIGHT)
        await pilot.pause()
        return written, app.theme, app.is_running

    assert _run(probe) == ([_OK_TITLE], "hermes-night", True)


@pytest.mark.usefixtures("frozen_clock_timer")
def test_radar_shimmer_and_pulse_survive_their_widgets_torn_down() -> None:
    """The radar, the shimmer and the pulse step on after the positions card and big total go."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[int, int, bool]:
        for timer in app.effect_timers.values():
            timer.stop()
        radar, pulse = app._radar, app._pulse
        await app.query_one("#positions").remove()
        await app.query_one("#portfolio-total").remove()
        await app.query_one("#down-row").remove()
        app._step_radar()
        app._start_shimmer()
        app._step_pulse()
        await pilot.pause()
        return app._radar - radar, (app._pulse - pulse) % MOTION.pulse_steps, app.is_running

    assert _run(probe, scenario="empty", motion=True) == (1, 1, True)


def test_motion_env_off_reaches_the_app(monkeypatch: pytest.MonkeyPatch) -> None:
    """HERMES_CONSOLE_MOTION=off with no explicit motion argument turns motion off."""
    monkeypatch.setenv("HERMES_CONSOLE_MOTION", "off")
    app = HomeApp(FixtureReadModel.load("ok-two-positions"), clock=lambda: _DAY)
    assert app.motion is False


# ----------------------------------------------------------------------
# Design conformance: keys, picker, palette, Esc, range, dot, night
# ----------------------------------------------------------------------


def _switcher_and_bar(app: HomeApp) -> tuple[int, str | None, int]:
    switcher = app.query_one("#screens", ContentSwitcher)
    return app.tab, switcher.current, app.query_one(TabBar).active


def test_each_number_key_selects_its_screen_in_the_switcher_and_the_tab_bar() -> None:
    """Keys 1-9 each show their own screen body and highlight their own tab."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> list[tuple[int, str | None, int]]:
        seen = []
        for tab in (*TABS[1:], TABS[0]):
            await _press(pilot, [tab.key])
            seen.append(_switcher_and_bar(app))
        return seen

    assert _run(probe) == [
        (1, "tab-2", 1),
        (2, "tab-3", 2),
        (3, "tab-4", 3),
        (4, "tab-5", 4),
        (5, "tab-6", 5),
        (6, "tab-7", 6),
        (7, "tab-8", 7),
        (8, "tab-9", 8),
        (0, "tab-1", 0),
    ]


def test_tab_walks_every_screen_in_order_and_wraps_home() -> None:
    """Nine Tab presses visit Signals ... Logs, then Home again."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> list[int]:
        seen = []
        for _ in TABS:
            await _press(pilot, ["tab"])
            seen.append(app.tab)
        return seen

    assert _run(probe) == [1, 2, 3, 4, 5, 6, 7, 8, 0]


def test_shift_tab_walks_every_screen_backwards() -> None:
    """Nine Shift+Tab presses visit Logs ... Signals, then Home again."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> list[int]:
        seen = []
        for _ in TABS:
            await _press(pilot, ["shift+tab"])
            seen.append(app.tab)
        return seen

    assert _run(probe) == [8, 7, 6, 5, 4, 3, 2, 1, 0]


def test_tab_does_nothing_while_the_theme_picker_is_open() -> None:
    """Under the picker Tab neither switches the screen nor closes the dialog."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[int, bool]:
        await _press(pilot, ["t", "tab"])
        return app.tab, isinstance(app.screen, ThemePicker)

    assert _run(probe) == (0, True)


def test_picker_highlights_the_current_choice() -> None:
    """T on Auto opens the picker with the third option, Auto, highlighted."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> int | None:
        await _press(pilot, ["t"])
        return app.screen.query_one(OptionList).highlighted

    assert _run(probe) == 2


def test_picking_neon_at_night_keeps_neon_and_saves_it(tmp_path: Path) -> None:
    """At 23:30 Auto shows night; T, up, up, Enter picks Neon, which shows and is saved."""
    path = tmp_path / "console.toml"

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[str, str]:
        before = app.theme
        await _press(pilot, ["t", "up", "up", "enter"])
        return before, app.theme

    assert (_run(probe, clock=lambda: _NIGHT, settings_path=path), load_settings(path).theme) == (
        ("hermes-night", "hermes-neon"),
        ThemeChoice.NEON,
    )


def _title_bar_start(app: HomeApp) -> str:
    top = app.query_one("#portfolio-panel").query_one(".hud-top", Static).content
    assert isinstance(top, Text)
    bgcolor = top.get_style_at_offset(Console(), len("┏━")).bgcolor
    assert bgcolor is not None
    return bgcolor.name


def test_picking_control_room_hides_the_window_bar_and_recolours_the_title_bars(
    tmp_path: Path,
) -> None:
    """After picking Control room the window bar is gone and title bars start on its chrome."""
    path = tmp_path / "console.toml"

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[bool, str, bool, str, str]:
        bar = app.query_one(WindowBar)
        shown_before, start_before = bar.display, _title_bar_start(app)
        await _press(pilot, ["t", "up", "enter"])
        first_row = _screen_text(app).splitlines()[0]
        return shown_before, start_before, bar.display, _title_bar_start(app), first_row

    assert _run(probe, settings_path=path) == (
        True,
        NEON.chrome[0].lower(),
        False,
        CONTROL_ROOM.chrome[0].lower(),
        "  1 Home · 2 Signals · 3 Positions · 4 Tax · 5 Lab · 6 Shadow test"
        " · 7 Briefs · 8 System · 9 Logs",
    )


@pytest.mark.parametrize(
    ("theme", "now", "shown"),
    [
        (ThemeChoice.NEON, _DAY, True),
        (ThemeChoice.CONTROL, _DAY, False),
        (ThemeChoice.AUTO, _DAY, True),
        (ThemeChoice.AUTO, _NIGHT, False),
    ],
    ids=["neon", "control", "auto-day", "auto-night"],
)
def test_window_bar_shows_only_in_neon(theme: ThemeChoice, now: datetime, shown: bool) -> None:
    """The Neon window bar leads the screen in Neon only."""

    async def drive() -> tuple[bool, bool]:
        app = HomeApp(
            FixtureReadModel.load("ok-two-positions"),
            clock=lambda: now,
            motion=False,
            theme=theme,
            refresh_seconds=3600,
        )
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.pause()
            first = _screen_text(app).splitlines()[0]
            return app.query_one(WindowBar).display, first.startswith("▐ HERMES // SHADOW CONSOLE")

    assert asyncio.run(drive()) == (shown, shown)


def test_demo_tag_is_drawn_on_the_warn_amber() -> None:
    """The DEMO DATA tag is bold void text on the warn amber, not raw ANSI yellow."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> str:
        del pilot
        tag = app._content["demo-tag"]
        assert isinstance(tag, Text)
        return str(tag.style)

    assert _run(probe) == f"bold {NEON.void} on {NEON.warn}"


async def _palette_pick(pilot: Pilot[None], query: str) -> None:
    await _press(pilot, ["ctrl+p"])
    await pilot.press(*query)
    await pilot.pause(0.5)
    await _press(pilot, ["enter"])


def test_palette_search_goes_to_a_screen() -> None:
    """Ctrl+P, "Go to Logs", Enter shows the Logs screen and closes the palette."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[int, bool]:
        await _palette_pick(pilot, "Go to Logs")
        return app.tab, isinstance(app.screen, CommandPalette)

    assert _run(probe) == (8, False)


def test_palette_search_switches_and_saves_the_theme(tmp_path: Path) -> None:
    """Ctrl+P, "Theme: Control room", Enter applies Control room and saves it."""
    path = tmp_path / "console.toml"

    async def probe(app: HomeApp, pilot: Pilot[None]) -> str:
        await _palette_pick(pilot, "Theme: Control room")
        return app.theme

    assert (_run(probe, settings_path=path), load_settings(path).theme) == (
        "hermes-control-room",
        ThemeChoice.CONTROL,
    )


def test_escape_closes_the_palette_and_keeps_the_screen() -> None:
    """On Signals, Ctrl+P then Esc closes the palette and Signals stays shown."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[bool, int]:
        await _press(pilot, ["2", "ctrl+p", "escape"])
        return isinstance(app.screen, CommandPalette), app.tab

    assert _run(probe) == (False, 1)


def test_escape_on_home_with_no_history_changes_nothing() -> None:
    """Esc on a fresh Home keeps Home shown and the app running."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[int, bool]:
        await _press(pilot, ["escape", "escape"])
        return app.tab, app.is_running

    assert _run(probe) == (0, True)


def test_escape_closes_the_detail_pane_before_leaving_home() -> None:
    """Wide: 2, 1, Enter opens the detail; Esc closes it on Home; Esc again goes to Signals."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[bool, int, bool, int]:
        await _press(pilot, ["2", "1", "enter"])
        panel = app.query_one("#detail-panel")
        opened = panel.has_class("-open")
        await _press(pilot, ["escape"])
        closed_tab, still_open = app.tab, panel.has_class("-open")
        await _press(pilot, ["escape"])
        return opened, closed_tab, still_open, app.tab

    assert _run(probe, size=(160, 45)) == (True, 0, False, 1)


def test_escape_leaves_the_help_screen_and_keeps_the_tab() -> None:
    """On Tax, ? opens help over the console; Esc closes it and Tax is still shown."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[int, int, int]:
        await _press(pilot, ["4", "question_mark"])
        depth = len(app.screen_stack)
        await _press(pilot, ["escape"])
        return depth, len(app.screen_stack), app.tab

    assert _run(probe) == (2, 1, 3)


@pytest.mark.parametrize(
    ("key", "line"),
    [
        ("d", "+€12.30 +1.22% today"),
        ("w", "+€15.20 +1.51% this week"),
        ("m", "+€19.00 +1.90% this month"),
        ("y", "+€21.06 +2.11% this year"),
        ("D", "+€12.30 +1.22% today"),
        ("M", "+€19.00 +1.90% this month"),
        ("Y", "+€21.06 +2.11% this year"),
    ],
)
def test_range_key_sets_the_range_from_another_screen(key: str, line: str) -> None:
    """On Positions, a range key (either case) sets the range Home shows on return."""
    other = "y" if key.lower() == "d" else "d"

    async def probe(app: HomeApp, pilot: Pilot[None]) -> str:
        await _press(pilot, [other, "3", key, "1"])
        return app.plain_text("portfolio").splitlines()[0]

    assert _run(probe, size=(80, 24)) == f"€1,021.06   ▲ {line}"


def test_range_keys_are_ignored_under_the_help_screen() -> None:
    """With help open, Y does not change the range: Home still shows today."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> str:
        await _press(pilot, ["question_mark", "y", "escape"])
        return app.plain_text("portfolio").splitlines()[0]

    assert _run(probe, size=(80, 24)) == "€1,021.06   ▲ +€12.30 +1.22% today"


def _themed_dot(theme: ThemeChoice, now: datetime) -> tuple[str, str]:
    async def drive() -> tuple[str, str]:
        app = HomeApp(
            FixtureReadModel.load("ok-two-positions"),
            clock=lambda: now,
            motion=False,
            theme=theme,
            refresh_seconds=3600,
        )
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.pause()
            return _dot_and_text_styles(app._content["heartbeat"], "●")

    return asyncio.run(drive())


@pytest.mark.parametrize(
    ("theme", "now", "colour"),
    [
        (ThemeChoice.NEON, _DAY, NEON.live),
        (ThemeChoice.CONTROL, _DAY, CONTROL_ROOM.live),
        (ThemeChoice.AUTO, _NIGHT, NIGHT.live),
    ],
    ids=["neon", "control", "night"],
)
def test_ok_dot_takes_each_themes_live_colour(
    theme: ThemeChoice, now: datetime, colour: str
) -> None:
    """OK: the dot is bold in the shown theme's live colour, and the words stay dim."""
    assert _themed_dot(theme, now) == (f"bold {colour.lower()}", "dim")


def test_ok_dot_on_the_empty_scenario_is_live_coloured() -> None:
    """With no positions the heartbeat is still OK and its dot still the live colour."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> str:
        del pilot
        return _dot_and_text_styles(app._content["heartbeat"], "●")[0]

    assert _run(probe, scenario="empty") == f"bold {NEON.live.lower()}"


def test_late_amber_keeps_the_day_warn_colour_at_night() -> None:
    """At night the ◐ line is still yellow, and yellow is still the full warn amber."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[str, str]:
        del pilot
        style = _dot_and_text_styles(app._content["heartbeat"], "◐")[0]
        return style, app.ansi_theme_dark.ansi_colors[3].hex.upper()

    assert _run(probe, scenario="late", clock=lambda: _NIGHT) == ("yellow", BASE.warn)


def test_late_and_down_have_no_live_dot() -> None:
    """Late shows ◐ and down shows ✖; neither status line carries the live ●."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> str:
        del pilot
        return app.plain_text("heartbeat") + app.plain_text("down-row")

    assert ["●" in _run(probe, scenario=s) for s in ("late", "down-stale")] == [False, False]


def test_auto_leaves_night_at_eight() -> None:
    """When the clock passes 08:00 Madrid, the next tick switches Auto back to Neon."""
    clock = _Clock(datetime(2026, 10, 4, 5, 59, 59, tzinfo=UTC))

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[str, str]:
        before = app.theme
        clock.now = datetime(2026, 10, 4, 6, 0, 0, tzinfo=UTC)
        await pilot.pause(1.3)
        return before, app.theme

    assert _run(probe, clock=clock) == ("hermes-night", "hermes-neon")


def test_a_hand_picked_control_room_ignores_the_night(tmp_path: Path) -> None:
    """A saved Control room choice stays Control room across 23:00."""
    path = tmp_path / "console.toml"
    path.write_text('theme = "control"\n', encoding="utf-8")
    clock = _Clock(datetime(2026, 10, 3, 20, 59, 59, tzinfo=UTC))

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[str, str]:
        before = app.theme
        clock.now = datetime(2026, 10, 3, 21, 0, 1, tzinfo=UTC)
        await pilot.pause(1.3)
        return before, app.theme

    assert _run(probe, clock=clock, settings_path=path) == (
        "hermes-control-room",
        "hermes-control-room",
    )


def test_entering_night_slows_the_pulse_and_stops_the_day_effects() -> None:
    """With motion on, crossing 23:00 restarts the pulse at 0.8 s per step.

    Issue #712's day effects (radar, shimmer) stop at night; the binary strip
    is gone since issue #713.
    """
    clock = _Clock(datetime(2026, 10, 3, 20, 59, 59, tzinfo=UTC))

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[list[str], list[str], float]:
        before = sorted(app.effect_timers)
        clock.now = datetime(2026, 10, 3, 21, 0, 0, tzinfo=UTC)
        await pilot.pause(1.3)
        return before, sorted(app.effect_timers), app.effect_timers["pulse"]._interval

    assert _run(probe, clock=clock, motion=True) == (
        ["pulse", "radar", "shimmer"],
        ["pulse"],
        0.8,
    )
