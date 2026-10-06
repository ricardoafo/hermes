"""``hermes tui``: the read-only operator console (Cockpit, design spec issue #711).

A Textual app over a :class:`~hermes.application.console.read_model.ConsoleReadModel`.
It embeds the same Rich renderers the static ``hermes`` home screen prints, so
the two can never disagree. The app never writes to the ledger: it only calls
the read methods of the read model, so opening it while ``hermes serve`` runs
neither blocks nor touches the ledger. The only file it writes is the
operator's theme choice (``~/.config/hermes/console.toml``).

Chrome (top to bottom): the window bar ("▐ HERMES // SHADOW CONSOLE", a ticking
binary strip, the clock), the screen tabs, the status row (demo tag, release,
heartbeat with the pulsing live dot, next-bar countdown, theme chip), the
screen, the 90-day line and the footer key chips. Every panel sits in a
:class:`~hermes.entrypoints.tui_widgets.HudPanel`. Tokens, themes, breakpoints
and timings live in :mod:`hermes.entrypoints.tui_theme`.

Screens: ``1`` Home re-hosts the home screen with unchanged content (its full
width layout is issue #712); ``7`` System is view only (issue #717, see
:mod:`hermes.entrypoints.tui_system`); the others name the issue that builds
them next.

Home layout: below 120 columns everything stacks (portfolio, positions,
market watch, activity; the position detail opens as a
:class:`~hermes.entrypoints.tui_drilldown.DetailScreen`). From 120 columns the
portfolio and activity sit left and the positions (with the market watch under
them) and the :class:`~hermes.entrypoints.tui_drilldown.DetailPane` right. The full-width layout per
breakpoint is issue #712. The portfolio
total is drawn three rows tall on terminals 30 rows or higher.

Every card is drawn on its own: a view the read model cannot serve yet (or a
ledger read that fails) turns that card into a plain "not available yet" (or
"could not read the ledger") line and leaves the others standing, so the live
screen never stops on a missing figure (change ``live-home-screen`` D-6).

Refresh: the read model is polled every 5 s; the clock and the countdown tick
every 1 s locally. Animations (the live-dot pulse and the binary strip) stop
with ``HERMES_CONSOLE_MOTION=off``.

Keys: ``1``-``8`` screens, Tab / Shift+Tab cycle them, Ctrl+P command palette,
``D`` ``W`` ``M`` ``Y`` range (day, week, month, year), up and down (or ``j``
``k``) select a position, Enter opens it, Esc or Backspace goes back, ``[``
``]`` previous / next inside a detail screen, ``L`` logs, ``T`` theme, ``?``
help, ``Q`` quit.

Reference: openspec/changes/readonly-tui-dashboard/change.md;
openspec/changes/live-home-screen/change.md (D-6);
openspec/changes/711-cockpit-theme/change.md;
openspec/changes/730-drill-down/change.md;
openspec/changes/717-system-screen/change.md.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable, Sequence
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta, tzinfo
from decimal import Decimal
from functools import partial
from pathlib import Path
from typing import ClassVar, Final

from rich.console import Group, RenderableType
from rich.text import Text
from textual import events
from textual.app import App, ComposeResult, SystemCommand
from textual.binding import Binding, BindingType
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.timer import Timer
from textual.widgets import ContentSwitcher, Static

from hermes.application.console import (
    ConsoleReadModel,
    Heartbeat,
    HeartbeatState,
    PortfolioRange,
    Surface,
    ViewNotAvailableError,
)
from hermes.application.console.logs import LogLine, render_log_line
from hermes.application.console.render import (
    DEMO_TAG,
    RenderContext,
    render_activity,
    render_heartbeat,
    render_market_watch,
    render_portfolio,
    render_position_detail,
    render_positions,
    render_shadow_test,
    render_tax_footnote,
    to_plain_text,
)
from hermes.application.console.system import (
    MachineStats,
    ReleaseHistory,
    ServiceStatus,
    SystemSources,
)
from hermes.domain.errors import LedgerError
from hermes.entrypoints.tui_drilldown import (
    DetailMode,
    DetailPane,
    DetailScreen,
    ListState,
    detail_mode,
)
from hermes.entrypoints.tui_settings import (
    DEFAULT_SETTINGS_PATH,
    ConsoleSettings,
    load_settings,
    save_settings,
)
from hermes.entrypoints.tui_system import (
    BAR_CHECKS,
    RELEASES_TRAIL,
    SERVICES_TRAIL,
    actions_text,
    machine_text,
    release_detail,
    releases_text,
    service_detail,
    services_text,
    timing_text,
)
from hermes.entrypoints.tui_theme import (
    BASE,
    COMPACT_BELOW,
    DOWN_GLYPH,
    HORIZONTAL_BREAKPOINTS,
    LATE_GLYPH,
    LIVE_GLYPH,
    MOTION,
    NIGHT_THEME,
    TALL_FROM,
    THEMES,
    TWO_COLUMNS_FROM,
    VERTICAL_BREAKPOINTS,
    ThemeChoice,
    data_tokens,
    motion_enabled,
    pulse_steps,
    resolve_theme,
    startup_logo,
    terminal_theme,
    theme_variables,
    tokens_for,
)
from hermes.entrypoints.tui_widgets import (
    LABEL_AI,
    HudPanel,
    Tab,
    TabBar,
    ThemeChip,
    ThemePicker,
    WindowBar,
    key_chips,
    theme_chip_text,
)

__all__ = [
    "REFRESH_SECONDS",
    "TABS",
    "HomeApp",
    "countdown_text",
    "osc_title",
    "run_home",
    "terminal_title",
]

REFRESH_SECONDS: Final = MOTION.poll
_LEFT_COLUMN: Final = 48
_PANEL_PADDING: Final = 2
_ACTIVITY_ROWS: Final = 4
_ACTIVITY_ROWS_SHORT: Final = 3
_LOGS_LINES: Final = 50
_LOGS_DEMO: Final = "Logs come from the VPS journal; demo data has none."
_LOGS_UNAVAILABLE: Final = "The journal is not readable here; run hermes logs on the VPS."
_READ_ONLY: Final = "read-only"
# Spaces the status row's widgets add around their text (padding, margins).
_STATUS_ROW_SLACK: Final = 8

TABS: Final = (
    Tab("1", "Home"),
    Tab("2", "Signals", 714),
    Tab("3", "Positions", 715),
    Tab("4", "Tax", 719),
    Tab("5", "Shadow test", 718),
    Tab("6", "Briefs", 720),
    Tab("7", "System", 717),
    Tab("8", "Logs", 716),
)
_HOME: Final = 0
_BRIEFS: Final = 5
_SYSTEM: Final = 6
_LOGS: Final = 7
_SERVICE: Final = "service:"
_RELEASE: Final = "release:"
_SERVICE_JOURNAL_LINES: Final = 12

_FOOTER_CHIPS: Final = (
    ("D W M Y", "range"),
    ("1-8", "screens"),
    ("↵", "detail"),
    ("L", "logs"),
    ("T", "theme"),
    ("?", "help"),
    ("^P", "palette"),
    ("Q", "quit"),
)
_DETAIL_CHIPS: Final = (
    ("Esc", "back"),
    ("[ ]", "prev/next"),
    ("1-8", "screens"),
    ("^P", "palette"),
    ("Q", "quit"),
)
_POSITIONS_TRAIL: Final = ("Home", "Positions")
_FOOTER_CHIPS_COMPACT: Final = (
    ("D W M Y", "range"),
    ("1-8", ""),
    ("↵", ""),
    ("T", ""),
    ("?", ""),
    ("^P", ""),
    ("Q", "quit"),
)

_CARD_ERRORS: Final = (ViewNotAvailableError, LedgerError)
_OSC_TITLE: Final = "\x1b]2;{}\x07"


def _unavailable(title: str, exc: Exception) -> Text:
    """Return the one plain line a card shows instead of its content."""
    if isinstance(exc, LedgerError):
        return Text(f"{title} · could not read the ledger: {exc.msg}", style="yellow")
    return Text(f"{title} · not available yet", style="dim")


def _card(title: str, build: Callable[[], RenderableType]) -> RenderableType:
    """Return ``build()``, or the line naming why the card has nothing to show."""
    try:
        return build()
    except _CARD_ERRORS as exc:
        return _unavailable(title, exc)


def _clock(at: datetime, tz: tzinfo) -> str:
    return at.astimezone(tz).strftime("%H:%M")


def terminal_title(heartbeat: Heartbeat | None, tz: tzinfo) -> str:
    """Return the terminal tab title for a heartbeat.

    Args:
        heartbeat: The last heartbeat read, or ``None`` when it could not be read.
        tz: Display timezone.

    Returns:
        ``hermes ● live · next 16:00``, ``hermes ◐ check late · 16:00``,
        ``hermes ✖ down since 18:04``, or ``hermes``.
    """
    if heartbeat is None:
        return "hermes"
    if heartbeat.state is HeartbeatState.DOWN and heartbeat.down_since is not None:
        return f"hermes {DOWN_GLYPH} down since {_clock(heartbeat.down_since, tz)}"
    if heartbeat.state is HeartbeatState.LATE:
        return f"hermes {LATE_GLYPH} check late · {_clock(heartbeat.next_check_at, tz)}"
    return f"hermes {LIVE_GLYPH} live · next {_clock(heartbeat.next_check_at, tz)}"


def osc_title(text: str) -> str:
    """Return the OSC 2 escape that sets the terminal tab title.

    Textual 8 has no API for the terminal title, so the app writes this through
    its driver. Control characters are dropped so a title can never end the
    escape early or start another one.

    Args:
        text: The title.

    Returns:
        ``ESC ] 2 ; text BEL``.
    """
    clean = "".join(char for char in text if char.isprintable())
    return _OSC_TITLE.format(clean)


def countdown_text(heartbeat: Heartbeat | None, now: datetime) -> str:
    """Return the time left to the next bar check, ticking every second.

    Args:
        heartbeat: The last heartbeat read.
        now: The current time.

    Returns:
        ``in 3h 48m 05s`` (hours left out under one hour), ``due`` once the
        check time has passed, or empty when hermes is late, down or unknown.
    """
    if heartbeat is None or heartbeat.state is not HeartbeatState.OK:
        return ""
    left = heartbeat.next_check_at - now
    if left <= timedelta(0):
        return "due"
    seconds = int(left.total_seconds())
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"in {hours}h {minutes:02d}m {secs:02d}s" if hours else f"in {minutes}m {secs:02d}s"


_RANGE_KEYS: Final[dict[str, PortfolioRange]] = {
    "today": PortfolioRange.TODAY,
    "week": PortfolioRange.WEEK,
    "month": PortfolioRange.MONTH,
    "year": PortfolioRange.YEAR,
}


class _PanelScreen(Screen[None]):
    """A full-screen read-only panel (detail, logs, help) closed with Esc or Enter."""

    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape,enter", "close", "back")]

    def __init__(self, body: Callable[[], RenderableType]) -> None:
        """Remember how to build the panel content.

        Args:
            body: Builds the content; called again on every refresh.
        """
        super().__init__()
        self._body = body

    def compose(self) -> ComposeResult:
        """Lay out the content and the footer."""
        yield Static(id="panel")
        yield Static("Esc back · Q quit · read-only", id="panel-footer")

    def on_mount(self) -> None:
        """Fill the panel."""
        self.refresh_panel()

    def refresh_panel(self) -> None:
        """Rebuild the content from the read model.

        The refresh timer can fire between ``push_screen`` and the panel's
        compose; the panel is then skipped here and filled by ``on_mount``.
        """
        for panel in self.query("#panel").results(Static):
            panel.update(self._body())

    def action_close(self) -> None:
        """Return to the console."""
        self.dismiss(None)


def _system_time() -> datetime:
    return datetime.now(UTC)


def _range_bindings() -> list[BindingType]:
    return [
        Binding(f"{key},{key.upper()}", f"range('{name}')", name, show=False)
        for key, name in (("d", "today"), ("w", "week"), ("m", "month"), ("y", "year"))
    ]


def _tab_bindings() -> list[BindingType]:
    return [
        Binding(tab.key, f"show_tab({index})", tab.name, show=False)
        for index, tab in enumerate(TABS)
    ]


def _placeholder(tab: Tab) -> Text:
    """Return what a screen that is not built yet shows."""
    return Text.assemble(
        (tab.name, "bold"),
        (f" · coming next · issue #{tab.issue}", "dim"),
    )


class HomeApp(App[None]):
    """The read-only Cockpit console.

    Args:
        model: Where every figure comes from; only read methods are called.
        refresh_seconds: How often the screen re-reads the model.
    """

    TITLE = "hermes"
    HORIZONTAL_BREAKPOINTS = HORIZONTAL_BREAKPOINTS
    VERTICAL_BREAKPOINTS = VERTICAL_BREAKPOINTS
    CSS = """
    Screen { background: $hud-void; }
    #window-bar, #tabs, #top { height: 1; }
    #demo-tag { width: auto; }
    #release { width: auto; color: $hud-muted; }
    #heartbeat { width: 1fr; }
    #countdown { width: auto; color: $hud-muted; }
    #theme-chip { width: auto; margin-left: 1; }
    #down-row { display: none; height: auto; }
    #down-row.-down { display: block; }
    #screens { height: 1fr; }
    #tab-1 { height: 1fr; }
    #body {
        layout: grid;
        grid-size: 1;
        grid-gutter: 0 2;
        grid-rows: auto;
        height: 1fr;
        overflow-y: auto;
    }
    #body.-split { grid-size: 2; grid-columns: 48 1fr; grid-gutter: 1 2; }
    .cell { height: auto; }
    #watch-column { height: auto; }
    #shadow, #footer-row, #panel-footer { height: 1; }
    #footer { width: 1fr; }
    #footer-status, #panel-footer { width: auto; color: $hud-muted; }
    #panel { height: 1fr; padding: 0 1; overflow-y: auto; }
    #system-body { layout: grid; grid-size: 1; grid-gutter: 0 2; grid-rows: auto; height: auto; }
    #system-body.-split { grid-size: 2; grid-gutter: 1 2; }
    .sys-column { height: auto; }
    """
    BINDINGS: ClassVar[list[BindingType]] = [
        *_range_bindings(),
        *_tab_bindings(),
        Binding("tab", "cycle_tab(1)", "next screen", show=False, priority=True),
        Binding("shift+tab", "cycle_tab(-1)", "previous screen", show=False, priority=True),
        Binding("up,k", "select(-1)", "previous", show=False),
        Binding("down,j", "select(1)", "next", show=False),
        Binding("enter", "open_detail", "detail", show=False),
        Binding("escape,backspace", "go_back", "back", show=False),
        Binding("l,L", "logs", "logs", show=False),
        Binding("t,T", "pick_theme", "theme", show=False),
        Binding("question_mark", "help", "help", show=False),
        Binding("q,Q", "quit", "quit", show=False),
    ]

    def __init__(
        self,
        model: ConsoleReadModel,
        *,
        refresh_seconds: float = REFRESH_SECONDS,
        journal: Callable[[int], Sequence[LogLine] | None] | None = None,
        release: Callable[[], str] | None = None,
        clock: Callable[[], datetime] = _system_time,
        motion: bool | None = None,
        settings_path: Path | None = None,
        theme: ThemeChoice | None = None,
        system: SystemSources | None = None,
    ) -> None:
        """Hold the read model, the journal source and the screen state.

        Args:
            model: The read model every panel draws from.
            refresh_seconds: Seconds between redraws.
            journal: Returns the last N journal lines, or ``None`` when the
                journal is not readable here; ``None`` means no journal at all.
            release: Returns the running release's name, re-read on every
                redraw; ``None`` leaves the top line without a release.
            clock: Returns the current time; the clock, the countdown and night
                mode tick from it.
            motion: Run the animations; ``None`` reads ``HERMES_CONSOLE_MOTION``.
            settings_path: Where the theme choice is read and saved; ``None``
                neither reads nor writes a file.
            theme: A choice from ``--theme``; it wins over the saved one and is saved.
            system: The System screen's read-only sources; ``None`` shows every
                System panel as "not available here".
        """
        super().__init__()
        self._model = model
        self._journal = journal
        self._release = release
        self._refresh_seconds = refresh_seconds
        self._clock = clock
        self._motion = motion_enabled(os.environ) if motion is None else motion
        self._settings_path = settings_path
        saved = ConsoleSettings() if settings_path is None else load_settings(settings_path)
        self._choice = theme or saved.theme or ThemeChoice.AUTO
        self._save_choice = theme is not None and theme is not saved.theme
        self._range = PortfolioRange.TODAY
        self._selected: str | None = None
        self._detail_open = False
        self._tab = _HOME
        self._tab_history: list[int] = []
        self._content: dict[str, RenderableType] = {}
        self._heartbeat: Heartbeat | None = None
        self._ctx = RenderContext(surface=Surface.OPERATOR_TUI, now=clock())
        self._pulse = 0
        self._title_sent: str | None = None
        self._system = system or SystemSources()
        self._sys_cursor: str | None = None
        self._service_open = False
        self._services: tuple[ServiceStatus, ...] | None = None
        self._releases: ReleaseHistory | None = None
        self._machine: MachineStats | None = None
        self._bar_seconds: tuple[Decimal, ...] | None = None
        self.effect_timers: dict[str, Timer] = {}
        for hermes_theme in THEMES:
            self.register_theme(hermes_theme)

    def get_theme_variable_defaults(self) -> dict[str, str]:
        """Give TCSS the ``hud-*`` variables before a hermes theme is applied."""
        return theme_variables(BASE)

    def compose(self) -> ComposeResult:
        """Lay out the chrome, the eight screens, the 90-day line and the footer."""
        yield WindowBar(id="window-bar")
        yield TabBar(TABS, id="tabs")
        with Horizontal(id="top"):
            yield Static(id="demo-tag")
            yield Static(id="release")
            yield Static(id="heartbeat")
            yield Static(id="countdown")
            yield ThemeChip(id="theme-chip")
        yield Static(id="down-row")
        with ContentSwitcher(initial="tab-1", id="screens"):
            with Container(id="tab-1"), Container(id="body"):
                yield HudPanel(
                    Static(id="portfolio"),
                    title="Portfolio",
                    subtitle="D W M Y range",
                    id="portfolio-panel",
                    classes="cell",
                )
                with Vertical(id="watch-column", classes="cell"):
                    yield HudPanel(
                        Static(id="positions"),
                        title="Positions",
                        subtitle="↑↓ select · ↵ detail",
                        id="positions-panel",
                    )
                    yield HudPanel(Static(id="market"), title="Market watch", id="market-panel")
                yield HudPanel(
                    Static(id="activity"), title="Activity", id="activity-panel", classes="cell"
                )
                yield DetailPane(
                    Static(id="detail"),
                    title="Position detail",
                    subtitle="Esc close",
                    id="detail-panel",
                    classes="cell",
                )
            yield from self._compose_system()
            for index, tab in enumerate(TABS[1:], start=1):
                if index == _SYSTEM:
                    continue
                ai = index == _BRIEFS
                yield VerticalScroll(
                    HudPanel(
                        Static(_placeholder(tab), id=f"screen-{tab.key}"),
                        title=tab.name,
                        label=LABEL_AI if ai else None,
                        subtitle="never sees profit" if ai else "",
                        ai=ai,
                    ),
                    id=f"tab-{tab.key}",
                )
        yield Static(id="shadow")
        with Horizontal(id="footer-row"):
            yield Static(id="footer")
            yield Static(id="footer-status")

    def _compose_system(self) -> ComposeResult:
        """Lay out the System screen: lists left, the service pane and figures right."""
        with (
            VerticalScroll(id=f"tab-{TABS[_SYSTEM].key}", can_focus=False),
            Container(id="system-body"),
        ):
            with Vertical(classes="sys-column"):
                yield HudPanel(
                    Static(id="sys-services"),
                    title="Services",
                    subtitle="↑↓ select · ↵ detail",
                    id="sys-services-panel",
                )
                yield HudPanel(
                    Static(id="sys-releases"),
                    title="Releases",
                    subtitle="↑↓ select · ↵ detail",
                    id="sys-releases-panel",
                )
                yield HudPanel(
                    Static(id="sys-actions"),
                    title="Actions",
                    subtitle="copy · nothing runs from here",
                    id="sys-actions-panel",
                )
            with Vertical(classes="sys-column"):
                yield DetailPane(
                    Static(id="sys-service-detail"),
                    title="Service detail",
                    id="sys-service-pane",
                )
                yield HudPanel(Static(id="sys-timing"), title="Data and timing")
                yield HudPanel(Static(id="sys-machine"), title="Machine")

    def on_mount(self) -> None:
        """Apply the theme, draw, and start the poll, the clock and the effects."""
        if self._save_choice:
            self._save_theme()
        self._redraw()
        self._tick()
        self.set_interval(self._refresh_seconds, self._redraw)
        self.set_interval(MOTION.clock_tick, self._tick)
        self._start_effects()

    def on_resize(self, event: events.Resize) -> None:
        """Re-flow when the terminal size changes."""
        del event
        self._redraw()
        self._tick()

    # ------------------------------------------------------------------
    # Effects
    # ------------------------------------------------------------------

    @property
    def motion(self) -> bool:
        """Return whether the animations run."""
        return self._motion

    @property
    def is_night(self) -> bool:
        """Return whether the night theme is showing."""
        return self.theme == NIGHT_THEME

    def _start_effects(self) -> None:
        """Start the pulse and the binary strip; nothing runs with motion off."""
        for timer in self.effect_timers.values():
            timer.stop()
        self.effect_timers = {}
        self._pulse = 0
        if not self._motion:
            return
        period = MOTION.pulse_period_night if self.is_night else MOTION.pulse_period
        self.effect_timers["pulse"] = self.set_interval(
            period / MOTION.pulse_steps, self._step_pulse
        )
        if not self.is_night:
            self.effect_timers["strip"] = self.set_interval(MOTION.strip_tick, self._step_strip)

    def _step_pulse(self) -> None:
        self._pulse = (self._pulse + 1) % MOTION.pulse_steps
        self._paint_heartbeat()

    def _step_strip(self) -> None:
        self._home.query_one(WindowBar).tick += 1

    @property
    def live_dot_colour(self) -> str:
        """Return the live dot's colour at the current pulse step."""
        return pulse_steps(tokens_for(self.theme))[self._pulse]

    # ------------------------------------------------------------------
    # Clock, theme and terminal title
    # ------------------------------------------------------------------

    def _tick(self) -> None:
        """Advance the clock and the countdown; switch night mode; set the tab title."""
        if not self.is_running or not self.screen_stack:
            return
        now = self._clock()
        self._apply_theme(now)
        self._home.query_one(WindowBar).clock = now.astimezone(self._ctx.tz).strftime("%H:%M:%S")
        countdown = countdown_text(self._heartbeat, now)
        on_row, full_chip = self._status_row_layout(countdown)
        self._paint_chip(compact=not full_chip)
        self._set("countdown", Text(f" {countdown}" if on_row else ""))
        short = countdown.removeprefix("in ")
        status = f"next {short} · {_READ_ONLY}" if countdown and not on_row else _READ_ONLY
        self._set("footer-status", Text(status))
        self._write_title(terminal_title(self._heartbeat, self._ctx.tz))

    def _status_row_layout(self, countdown: str) -> tuple[bool, bool]:
        """Return whether the countdown and the full theme chip fit on the status row.

        The heartbeat is never cut: at compact width, or when a demo tag and a
        release leave too little room, the countdown moves to the footer and,
        if still needed, the chip shrinks to its glyph.
        """
        width = self.size.width
        if width < COMPACT_BELOW:
            return False, False
        used = _STATUS_ROW_SLACK + sum(
            len(to_plain_text(self._content.get(part, Text("")), width=width).strip())
            for part in ("demo-tag", "release", "heartbeat")
        )
        chip = self._chip_text(compact=False).cell_len
        on_row = bool(countdown) and used + chip + len(countdown) + 1 <= width
        return on_row, used + chip <= width

    def _chip_text(self, *, compact: bool) -> Text:
        return theme_chip_text(
            self._choice, tokens_for(self.theme), night=self.is_night, compact=compact
        )

    def _paint_chip(self, *, compact: bool) -> None:
        self._home.query_one(ThemeChip).update(self._chip_text(compact=compact))

    def _apply_theme(self, now: datetime) -> None:
        name = resolve_theme(self._choice, now.astimezone(self._ctx.tz).time())
        night_changed = (name == NIGHT_THEME) != self.is_night
        if self.theme != name:
            self.ansi_theme_dark = terminal_theme(data_tokens(name))
            self.theme = name
        self._home.query_one(WindowBar).strip = name != NIGHT_THEME
        if night_changed and self.effect_timers:
            self._start_effects()

    @property
    def theme_choice(self) -> ThemeChoice:
        """Return the operator's theme choice."""
        return self._choice

    def choose_theme(self, choice: ThemeChoice) -> None:
        """Switch to a theme choice and save it.

        Args:
            choice: Neon, Control room or Auto.
        """
        self._choice = choice
        self._save_theme()
        self._tick()
        self._paint_heartbeat()

    def _save_theme(self) -> None:
        if self._settings_path is None:
            return
        try:
            save_settings(self._settings_path, ConsoleSettings(theme=self._choice))
        except OSError as exc:
            self.notify(f"Could not save the theme: {exc}", severity="warning")

    def _write_title(self, title: str) -> None:
        """Set the terminal tab title when it changed (OSC 2 through the driver)."""
        if title == self._title_sent:
            return
        driver = self._driver
        if driver is not None:
            driver.write(osc_title(title))
            driver.flush()
        self._title_sent = title

    # ------------------------------------------------------------------
    # Drawing
    # ------------------------------------------------------------------

    @property
    def is_wide(self) -> bool:
        """Return whether the home screen splits into two columns."""
        return self.size.width >= TWO_COLUMNS_FROM

    @property
    def tab(self) -> int:
        """Return the index of the shown screen tab."""
        return self._tab

    def plain_text(self, widget_id: str) -> str:
        """Return the text of one widget without styles (for tests and review).

        Args:
            widget_id: ``demo-tag``, ``release``, ``heartbeat``, ``down-row``, ``portfolio``,
                ``positions``, ``market``, ``activity``, ``detail``, ``shadow``,
                ``countdown``, ``footer`` or ``footer-status``.

        Returns:
            The rendered text, one line per row.
        """
        content = self._content.get(widget_id, Text(""))
        return to_plain_text(content, width=max(self._panel_width(), 20))

    def _panel_width(self) -> int:
        width = self.size.width
        column = width - _LEFT_COLUMN - 2 if self.is_wide else width
        return column - _PANEL_PADDING

    def _render_context(self) -> RenderContext:
        try:
            ctx = RenderContext.for_model(
                self._model,
                surface=Surface.OPERATOR_TUI,
                width=self._panel_width(),
                down_hint="press L for why",
            )
        except _CARD_ERRORS:
            return RenderContext(
                surface=Surface.OPERATOR_TUI, now=datetime.now(UTC), width=self._panel_width()
            )
        return replace(ctx, demo_scenario=None)

    def _set(self, widget_id: str, content: RenderableType) -> None:
        self._content[widget_id] = content
        self._home.query_one(f"#{widget_id}", Static).update(content)

    @property
    def _home(self) -> Screen[object]:
        return self.screen_stack[0]

    def _positions_coins(self) -> list[str]:
        try:
            return [row.coin for row in self._model.positions()]
        except _CARD_ERRORS:
            return []

    def _paint_heartbeat(self) -> None:
        heartbeat = self._heartbeat
        if heartbeat is None or not self.screen_stack:
            return
        ctx = replace(self._ctx, live_dot=f"bold {self.live_dot_colour}")
        down = heartbeat.state is HeartbeatState.DOWN
        self._set("heartbeat", Text("") if down else render_heartbeat(heartbeat, ctx))
        self._set("down-row", render_heartbeat(heartbeat, ctx) if down else Text(""))
        self._home.query_one("#down-row").set_class(down, "-down")

    def _redraw(self) -> None:
        if not self.is_running or not self.screen_stack:
            return
        model = self._model
        ctx = self._render_context()
        self._ctx = ctx
        scenario = model.demo_scenario
        tag = (
            Text(f" {DEMO_TAG} · scenario {scenario} ", style="bold black on yellow")
            if scenario
            else Text("")
        )
        self._set("demo-tag", tag)
        self._set(
            "release", Text("") if self._release is None else Text(f"release {self._release()}")
        )
        try:
            self._heartbeat = model.heartbeat()
        except _CARD_ERRORS as exc:
            self._heartbeat = None
            self._set("heartbeat", _unavailable("Heartbeat", exc))
            self._set("down-row", Text(""))
            self._home.query_one("#down-row").set_class(False, "-down")
        else:
            self._paint_heartbeat()

        coins = self._positions_coins()
        if self._selected not in coins:
            self._selected = coins[0] if coins else None
        self._home.query_one("#body").set_class(self.is_wide, "-split")
        big = self.size.height >= TALL_FROM
        rows = _ACTIVITY_ROWS if big else _ACTIVITY_ROWS_SHORT
        selected = self._selected
        self._set(
            "portfolio",
            _card(
                "Portfolio",
                lambda: render_portfolio(model.portfolio(self._range), ctx, big_total=big),
            ),
        )
        self._set(
            "positions",
            _card(
                "Positions",
                lambda: render_positions(model.positions(), ctx, selected=selected),
            ),
        )
        self._set(
            "market",
            _card(
                "Market watch",
                lambda: render_market_watch(model.market_watch(), ctx),
            ),
        )
        self._set(
            "activity",
            _card("Activity", lambda: render_activity(model.activity(), ctx, limit=rows)),
        )
        self._set(
            "shadow", _card("Shadow test", lambda: render_shadow_test(model.shadow_test(), ctx))
        )
        self._redraw_detail(ctx)
        self._redraw_chrome()
        if self._tab == _SYSTEM:
            self._read_system()
            self._paint_system()
        if self._tab == _LOGS:
            logs = TABS[_LOGS]
            self._set(
                f"screen-{logs.key}", Group(_placeholder(logs), Text(""), self._logs_content())
            )
        screen = self.screen
        if isinstance(screen, _PanelScreen | DetailScreen):
            screen.refresh_panel()

    def _redraw_chrome(self) -> None:
        compact = self.size.width < COMPACT_BELOW
        tokens = tokens_for(self.theme)
        self._set("footer", key_chips(_FOOTER_CHIPS_COMPACT if compact else _FOOTER_CHIPS, tokens))
        tabs = self._home.query_one(TabBar)
        tabs.compact = compact
        tabs.active = self._tab
        on_home = self._tab == _HOME
        self._home.query_one("#positions-panel", HudPanel).active = (
            on_home and not self._detail_open
        )
        self._home.query_one("#detail-panel", HudPanel).active = on_home and self._detail_open

    def _detail_content(self, coin: str | None, ctx: RenderContext | None = None) -> RenderableType:
        ctx = ctx or self._render_context()
        model = self._model
        return _card(
            "Position",
            lambda: render_position_detail(model.position(coin) if coin else None, ctx),
        )

    def _redraw_detail(self, ctx: RenderContext) -> None:
        shown = self._detail_open and self.is_wide and self._selected is not None
        self._home.query_one("#detail-panel", DetailPane).is_open = shown
        if shown:
            self._set("detail", self._detail_content(self._selected, ctx))

    # ------------------------------------------------------------------
    # System (view only: nothing here runs a command)
    # ------------------------------------------------------------------

    def _read_system(self) -> None:
        """Re-read the System sources; a missing source stays ``None``."""
        source = self._system
        self._services = None if source.services is None else source.services.services()
        self._releases = None if source.releases is None else source.releases.releases()
        self._machine = None if source.machine is None else source.machine.machine()
        self._bar_seconds = None
        if source.bar_timing is not None:
            try:
                self._bar_seconds = source.bar_timing.bar_check_seconds(BAR_CHECKS)
            except LedgerError:
                self._bar_seconds = None

    def _service_names(self) -> list[str]:
        return [status.unit.name for status in self._services or ()]

    def _release_names(self) -> list[str]:
        return [] if self._releases is None else list(self._releases.names())

    def _system_keys(self) -> list[str]:
        return [f"{_SERVICE}{name}" for name in self._service_names()] + [
            f"{_RELEASE}{name}" for name in self._release_names()
        ]

    def _system_selected(self, prefix: str) -> str | None:
        cursor = self._sys_cursor
        if cursor is None or not cursor.startswith(prefix):
            return None
        return cursor.removeprefix(prefix)

    def _eur_rate_date(self) -> date | None:
        try:
            return self._model.portfolio(self._range).eur_rate_date
        except _CARD_ERRORS:
            return None

    def _paint_system(self) -> None:
        """Draw the System panels from the last read; moving the cursor reads nothing."""
        keys = self._system_keys()
        if self._sys_cursor not in keys:
            self._sys_cursor = keys[0] if keys else None
        tokens = tokens_for(self.theme)
        tz = self._ctx.tz
        now = self._clock()
        service = self._system_selected(_SERVICE)
        release = self._system_selected(_RELEASE)
        self._home.query_one("#system-body").set_class(self.is_wide, "-split")
        self._set("sys-services", services_text(self._services, service, tz, tokens))
        self._set("sys-releases", releases_text(self._releases, release, tz, tokens))
        self._set("sys-actions", actions_text(tokens))
        self._set(
            "sys-timing",
            timing_text(
                self._heartbeat,
                eur_rate_date=self._eur_rate_date(),
                bar_seconds=self._bar_seconds,
                now=now,
                tz=tz,
                tokens=tokens,
            ),
        )
        self._set("sys-machine", machine_text(self._machine, now, tz, tokens))
        on_system = self._tab == _SYSTEM
        services_panel = self._home.query_one("#sys-services-panel", HudPanel)
        services_panel.active = on_system and service is not None
        releases_panel = self._home.query_one("#sys-releases-panel", HudPanel)
        releases_panel.active = on_system and release is not None
        shown = (
            self._service_open
            and service is not None
            and detail_mode(self.size.width, beside=self.is_wide) is DetailMode.PANE
        )
        self._home.query_one("#sys-service-pane", DetailPane).is_open = shown
        if shown and service is not None:
            self._set("sys-service-detail", self._service_content(service))

    def _service_content(self, name: str) -> RenderableType:
        unit = next((s.unit for s in self._services or () if s.unit.name == name), None)
        reader = self._system.unit_journal
        journal = (
            None if unit is None or reader is None else reader(unit.unit, _SERVICE_JOURNAL_LINES)
        )
        return service_detail(
            self._services,
            name,
            journal=journal,
            now=self._clock(),
            tz=self._ctx.tz,
            tokens=tokens_for(self.theme),
        )

    def _release_content(self, name: str) -> RenderableType:
        return release_detail(
            self._releases, name, self._clock(), self._ctx.tz, tokens_for(self.theme)
        )

    def _restore_system_cursor(self, prefix: str, state: ListState | None) -> None:
        if state is not None and state.cursor is not None:
            self._sys_cursor = f"{prefix}{state.cursor}"
        self._paint_system()

    def _open_system_detail(self) -> None:
        """Open the selected service (pane, or screen when compact) or release (screen)."""
        service = self._system_selected(_SERVICE)
        release = self._system_selected(_RELEASE)
        if service is not None:
            if detail_mode(self.size.width, beside=self.is_wide) is DetailMode.PANE:
                self._service_open = True
                self._paint_system()
                return
            self.push_screen(
                DetailScreen(
                    trail=SERVICES_TRAIL,
                    keys=self._service_names,
                    current=service,
                    body=self._service_content,
                    title="Service detail",
                    state=ListState(cursor=service),
                    chips=_DETAIL_CHIPS,
                ),
                partial(self._restore_system_cursor, _SERVICE),
            )
        elif release is not None:
            self.push_screen(
                DetailScreen(
                    trail=RELEASES_TRAIL,
                    keys=self._release_names,
                    current=release,
                    body=self._release_content,
                    title="Release detail",
                    state=ListState(cursor=release),
                    chips=_DETAIL_CHIPS,
                ),
                partial(self._restore_system_cursor, _RELEASE),
            )

    def _step_system(self, step: int) -> None:
        keys = self._system_keys()
        if not keys:
            return
        current = keys.index(self._sys_cursor) if self._sys_cursor in keys else 0
        self._sys_cursor = keys[(current + step) % len(keys)]
        self._paint_system()

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    @property
    def _on_home(self) -> bool:
        """Return whether no panel screen covers the console."""
        return len(self.screen_stack) <= 1

    @property
    def _home_shown(self) -> bool:
        return self._on_home and self._tab == _HOME

    def _switch_to(self, index: int) -> None:
        self._tab = index
        self._home.query_one("#screens", ContentSwitcher).current = f"tab-{TABS[index].key}"
        self._redraw()

    def action_show_tab(self, index: int) -> None:
        """Show a screen tab, closing any panel screen above the console.

        Args:
            index: Tab index (``0`` is Home).
        """
        while len(self.screen_stack) > 1:
            self.pop_screen()
        if index != self._tab:
            self._tab_history.append(self._tab)
        self._switch_to(index)

    def action_cycle_tab(self, step: int) -> None:
        """Show the next (``1``) or previous (``-1``) tab; ignored under a dialog.

        Args:
            step: Direction.
        """
        if isinstance(self.screen, ModalScreen):
            return
        self.action_show_tab((self._tab + step) % len(TABS))

    def action_go_back(self) -> None:
        """Close the detail pane, else return to the previous tab."""
        if not self._on_home:
            return
        if self._detail_open and self._tab == _HOME:
            self._detail_open = False
            self._redraw()
        elif self._service_open and self._tab == _SYSTEM:
            self._service_open = False
            self._paint_system()
        elif self._tab_history:
            self._switch_to(self._tab_history.pop())

    def action_range(self, name: str) -> None:
        """Switch the change line and the sparkline to a range.

        Args:
            name: ``today``, ``week``, ``month`` or ``year``.
        """
        if not self._on_home:
            return
        self._range = _RANGE_KEYS[name]
        self._redraw()

    def action_select(self, step: int) -> None:
        """Move the position cursor (on System, the service / release cursor), wrapping around.

        Args:
            step: ``-1`` for the previous position, ``1`` for the next.
        """
        if self._on_home and self._tab == _SYSTEM:
            self._step_system(step)
            return
        coins = self._positions_coins()
        if not self._home_shown or not coins:
            return
        current = coins.index(self._selected) if self._selected in coins else 0
        self._selected = coins[(current + step) % len(coins)]
        self._redraw()

    def list_state(self) -> ListState:
        """Return the positions list's state; it has no filters or sort yet, only a cursor."""
        return ListState(cursor=self._selected)

    def restore_list_state(self, state: ListState | None) -> None:
        """Put the positions cursor back where a detail screen left it.

        Args:
            state: The state the detail screen returned; ``None`` changes nothing.
        """
        if state is None:
            return
        self._selected = state.cursor
        self._redraw()

    def action_open_detail(self) -> None:
        """Open the selected position: a pane beside the list, else a detail screen.

        Home places the pane beside the list only from 120 columns (two
        columns until issue #712), so between 100 and 119 it opens a screen too.
        On System it opens the selected service or release instead.
        """
        if self._on_home and self._tab == _SYSTEM:
            self._open_system_detail()
            return
        selected = self._selected
        if not self._home_shown or selected is None:
            return
        if detail_mode(self.size.width, beside=self.is_wide) is DetailMode.PANE:
            self._detail_open = True
            self._redraw()
            return
        self.push_screen(
            DetailScreen(
                trail=_POSITIONS_TRAIL,
                keys=self._positions_coins,
                current=selected,
                body=self._detail_content,
                title="Position detail",
                state=self.list_state(),
                chips=_DETAIL_CHIPS,
            ),
            self.restore_list_state,
        )

    def action_pick_theme(self) -> None:
        """Offer Neon, Control room or Auto."""
        if isinstance(self.screen, ModalScreen):
            return
        self.push_screen(ThemePicker(self._choice), self._picked_theme)

    def _picked_theme(self, choice: ThemeChoice | None) -> None:
        if choice is not None:
            self.choose_theme(choice)

    def on_theme_chip_pressed(self, message: ThemeChip.Pressed) -> None:
        """Open the theme picker from the chip."""
        del message
        self.action_pick_theme()

    def on_tab_bar_picked(self, message: TabBar.Picked) -> None:
        """Show the clicked tab."""
        self.action_show_tab(message.index)

    def get_system_commands(self, screen: Screen[object]) -> Iterable[SystemCommand]:
        """List the screens and the three theme choices in the command palette.

        Textual's own theme command is left out: it lists every built-in theme,
        and the console offers only its own three choices.

        Args:
            screen: The screen the palette was opened from.

        Yields:
            The palette's commands.
        """
        for index, tab in enumerate(TABS):
            yield SystemCommand(
                f"Go to {tab.name}", f"Screen {tab.key}", partial(self.action_show_tab, index)
            )
        for choice, title in (
            (ThemeChoice.NEON, "Neon"),
            (ThemeChoice.CONTROL, "Control room"),
            (ThemeChoice.AUTO, "Auto (night 23:00-08:00)"),
        ):
            yield SystemCommand(
                f"Theme: {title}", "Switch and save the theme", partial(self.choose_theme, choice)
            )
        for command in super().get_system_commands(screen):
            if command.title != "Theme":
                yield command

    def action_logs(self) -> None:
        """Show the last journal lines, as ``hermes logs`` prints them."""
        if self._on_home:
            self.push_screen(_PanelScreen(self._logs_content))

    def _logs_content(self) -> RenderableType:
        if self._model.demo_scenario is not None:
            return Text(_LOGS_DEMO, style="dim")
        lines = None if self._journal is None else self._journal(_LOGS_LINES)
        if lines is None:
            return Text(_LOGS_UNAVAILABLE, style="dim")
        if not lines:
            return Text("No journal lines yet.", style="dim")
        tz = self._render_context().tz
        return Group(Text("Logs", style="bold"), *(render_log_line(line, tz) for line in lines))

    def action_help(self) -> None:
        """Show the keys and the tax footnote."""
        if self._on_home:
            self.push_screen(_PanelScreen(self._help_content))

    def _help_content(self) -> RenderableType:
        live = tokens_for(self.theme).live
        keys = [
            *(Text(row, style=f"bold {live}") for row in startup_logo()),
            Text(""),
            Text("Keys", style="bold"),
            Text("  1-8 screens · Tab / Shift+Tab next / previous · Ctrl+P command palette"),
            Text("  D day · W week · M month · Y year"),
            Text("  ↑ ↓ choose a position · Enter open its detail · Esc or Backspace back"),
            Text("  [ ] previous / next item inside a detail screen"),
            Text("  L logs · T theme · ? this help · Q quit"),
            Text("  7 System is view only: each VPS action is a command to copy; no key runs one."),
            Text(
                "This screen is read-only: nothing here changes a position, a stop or the ledger."
            ),
            Text(""),
            Text("Tax", style="bold"),
            _card("Tax", lambda: render_tax_footnote(self._model.portfolio(self._range))),
            Text(""),
            Text("Prices and money are shown in EUR.", style="dim"),
        ]
        return Group(*keys)


def run_home(
    model: ConsoleReadModel,
    journal: Callable[[int], Sequence[LogLine] | None] | None = None,
    release: Callable[[], str] | None = None,
    *,
    theme: ThemeChoice | None = None,
    settings_path: Path | None = DEFAULT_SETTINGS_PATH,
    system: SystemSources | None = None,
) -> None:
    """Run the console until the operator quits.

    Args:
        model: The read model to show.
        journal: Source of the logs panel's lines; see :class:`HomeApp`.
        release: Source of the running release's name; see :class:`HomeApp`.
        theme: A ``--theme`` choice; saved, and wins over the saved one.
        settings_path: Where the theme choice is read and saved.
        system: The System screen's read-only sources; see :class:`HomeApp`.
    """
    HomeApp(
        model,
        journal=journal,
        release=release,
        theme=theme,
        settings_path=settings_path,
        system=system,
    ).run()
