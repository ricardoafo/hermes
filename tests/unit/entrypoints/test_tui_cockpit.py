"""The Cockpit console chrome (issue #711), driven with Textual's pilot.

Invariants:

- every key has one action; 1-8 jump to screens, Tab / Shift+Tab cycle them,
  Esc goes back, Ctrl+P opens the command palette, D W M Y still set the range;
- screens not built yet name the issue that builds them (System is built, #717);
- every home card sits in a HudPanel labelled "computed";
- the live dot is drawn in the live colour (not dim) when OK; late is amber
  and down is red, as before;
- the terminal tab title follows the heartbeat and is written as OSC 2;
- the clock and the countdown tick every second from the injected clock;
- Auto switches to the night theme at 23:00 local; a hand-picked theme stays;
- T opens the theme picker and the pick is saved (to a tmp path, never home);
- with motion off no timer-driven effect runs.

Every app here gets a frozen clock; none reads or writes the real home folder.
"""

from __future__ import annotations

import asyncio
import io
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from rich.console import Console, RenderableType
from rich.text import Text
from textual.binding import Binding
from textual.command import CommandPalette
from textual.pilot import Pilot

from hermes.adapters.console import FixtureReadModel
from hermes.application.console import Heartbeat, HeartbeatState
from hermes.entrypoints.commands.tui import run_tui
from hermes.entrypoints.tui import (
    TABS,
    HomeApp,
    countdown_text,
    osc_title,
    terminal_title,
)
from hermes.entrypoints.tui_settings import ThemeChoice, load_settings
from hermes.entrypoints.tui_theme import BASE, NEON, NIGHT, pulse_steps
from hermes.entrypoints.tui_widgets import HudPanel, ThemePicker, WindowBar

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


def test_number_keys_one_to_eight_each_name_a_screen() -> None:
    """Keys 1-8 are bound, one per tab, and nothing binds 9 or 0 yet."""
    keys = set(_bound_keys())
    assert ({str(n) for n in range(1, 9)} <= keys, {"9", "0"} & keys) == (True, set())


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
    """Screens 2-8 except System each say "coming next · issue #N" with their own issue.

    System (7) is built by issue #717 and no longer shows the placeholder.
    """

    async def probe(app: HomeApp, pilot: Pilot[None]) -> dict[str, bool]:
        seen = {}
        for tab in TABS[1:]:
            await _press(pilot, [tab.key])
            seen[tab.name] = f"{tab.name} · coming next · issue #{tab.issue}" in _screen_text(app)
        return seen

    assert _run(probe) == {
        "Signals": True,
        "Positions": True,
        "Tax": True,
        "Shadow test": True,
        "Briefs": True,
        "System": False,
        "Logs": True,
    }


def test_tab_and_shift_tab_cycle_the_screens() -> None:
    """Tab moves Home to Signals; Shift+Tab from Home wraps to Logs."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[int, int]:
        await _press(pilot, ["tab"])
        forward = app.tab
        await _press(pilot, ["1", "shift+tab"])
        return forward, app.tab

    assert _run(probe) == (1, 7)


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
    """Portfolio, positions, market, activity and detail are each framed and labelled computed."""

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
        "market": ("Market watch", "computed"),
        "activity": ("Activity", "computed"),
        "detail": ("Position detail", "computed"),
    }


def test_briefs_screen_is_the_ai_variant() -> None:
    """The Briefs screen's panel is the AI supervisor variant."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[bool, str]:
        del pilot
        panel = app.query_one("#screen-6").parent
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
        "  1 Home · 2 Signals · 3 Positions · 4 Tax · 5 Shadow test · 6 Briefs · 7 System · 8 Logs",
    )


def test_compact_tabs_are_numbers_only_and_the_countdown_is_in_the_footer() -> None:
    """At 80 columns the tabs drop their names and the footer ends with the countdown."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[str, str]:
        del pilot
        lines = _screen_text(app).splitlines()
        return lines[1], lines[-1]

    tabs, footer = _run(probe, size=(80, 24))
    assert (tabs, footer.endswith("next 3h 48m 00s · read-only")) == (
        "  1   2   3   4   5   6   7   8",
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

    assert _run(probe) == ["\x1b]2;hermes ● live · next 18:00\x07"]


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
    """With motion off there is no pulse or strip timer; the strip and the dot stay still."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[dict[str, object], int, str]:
        await pilot.pause(1.3)
        return dict(app.effect_timers), app.query_one(WindowBar).tick, app.live_dot_colour

    assert _run(probe, motion=False) == ({}, 0, NEON.live)


def test_motion_on_runs_the_pulse_and_the_strip() -> None:
    """With motion on by day both effects run and the strip advances."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[list[str], bool]:
        await pilot.pause(1.3)
        return sorted(app.effect_timers), app.query_one(WindowBar).tick > 0

    assert _run(probe, motion=True) == (["pulse", "strip"], True)


def test_night_runs_only_the_slow_pulse() -> None:
    """At night there is no binary strip, and the pulse steps every 0.8 s instead of 0.4 s."""

    async def probe(app: HomeApp, pilot: Pilot[None]) -> tuple[list[str], float, bool]:
        del pilot
        pulse = app.effect_timers["pulse"]
        return sorted(app.effect_timers), pulse._interval, app.query_one(WindowBar).strip

    assert _run(probe, motion=True, clock=lambda: _NIGHT) == (["pulse"], 0.8, False)


def test_motion_env_off_reaches_the_app(monkeypatch: pytest.MonkeyPatch) -> None:
    """HERMES_CONSOLE_MOTION=off with no explicit motion argument turns motion off."""
    monkeypatch.setenv("HERMES_CONSOLE_MOTION", "off")
    app = HomeApp(FixtureReadModel.load("ok-two-positions"), clock=lambda: _DAY)
    assert app.motion is False
