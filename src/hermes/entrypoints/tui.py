"""``hermes tui``: the read-only operator console (Cockpit, design spec issue #711).

A Textual app over a :class:`~hermes.application.console.read_model.ConsoleReadModel`.
It embeds the same Rich renderers the static ``hermes`` home screen prints, so
the two can never disagree. The app never writes to the ledger: it only calls
the read methods of the read model, so opening it while ``hermes serve`` runs
neither blocks nor touches the ledger. The only file it writes is the
operator's theme choice (``~/.config/hermes/console.toml``).

Chrome (top to bottom): the window bar ("▐ HERMES // SHADOW CONSOLE", the
live-feed pulse or the next-bar countdown, the mainnet badge, the clock; Neon
only), the screen tabs, the status row (the mainnet badge outside Neon, demo
tag, version and release, heartbeat with the pulsing live dot, next-bar
countdown and its meter, theme chip), the screen, the 90-day line and the
footer key chips. The mainnet badge (issue #833) follows the config's
``allow_mainnet``: ``SHADOW · mainnet off``, ``LIVE · mainnet ON`` in the down
colour, or an amber ``mainnet ?`` when the config could not be read; a demo
scenario always reads mainnet off. Every panel sits in a
:class:`~hermes.entrypoints.tui_widgets.HudPanel`. Tokens, themes, breakpoints
and timings live in :mod:`hermes.entrypoints.tui_theme`.

Screens: ``1`` Home (issue #712); ``4`` Tax (issue #719, view only:
:mod:`hermes.entrypoints.tui_tax`); ``5`` Shadow test (issue #718); ``6``
Briefs; ``7`` System (issue #717, view only:
:mod:`hermes.entrypoints.tui_system`); ``8`` Logs (issue #716: the
journal as a queryable list, :class:`~hermes.entrypoints.tui_logs.LogsView`); the
others name the issue that builds them next.

The Shadow test screen: the big "N / 90" over the shared 90-day run bar with
month ticks (or "warm-up · not counting" while no ``hermes shadow-start``
record exists), the five certification checks (pass, fail or pending only), the
day-0 record with its Ready-rule verdicts, the frozen settings and the lab
notebook. It shows no money. Its sources come in a
:class:`~hermes.application.console.shadow_screen.ShadowSources`.

Home layout, per breakpoint token (:func:`~hermes.entrypoints.tui_home.home_layout`):
under 100 columns one column (Portfolio, Positions, Market, Activity, Engine
brief) with sparklines for charts, and the position detail opens as a
:class:`~hermes.entrypoints.tui_drilldown.DetailScreen`; from 100 two columns
(Portfolio left; Positions, the position detail as a
:class:`~hermes.entrypoints.tui_drilldown.DetailPane`, Market, Activity and the
Engine brief right); from 160 the mock layout, Market beside
Activity; from 220 a third column with a Signals placeholder and a log tail.
Under 30 rows the curve and the big total hide; from 45 rows the curve is
twice as tall. The bottom line is the 90-day run bar.

The Portfolio panel: the big gradient total (counts up and shimmers with
motion on), change and net, the braille curve, cash / invested meters, the
exposure meter against the roster's cap, the Today / This year waterfall and
the open line. Every figure goes through ``pnl_visible``.

Every card is drawn on its own: a view the read model cannot serve yet (or a
ledger read that fails) turns that card into a plain "not available yet" (or
"could not read the ledger") line and leaves the others standing, so the live
screen never stops on a missing figure (change ``live-home-screen`` D-6).

Refresh: the read model is polled every 5 s; the clock and the countdown tick
every 1 s locally. Animations (the live-dot pulse, the feed pulse's bright newest glyph, the
radar, the count-up, the shimmer and the tick flash) stop with
``HERMES_CONSOLE_MOTION=off``.

Live between bars (issue #713): with a ticker, the 1 s tick starts a thread
worker every 10 s that reads BTC, ETH and SOL from the ticker port, so the UI
never blocks. The ticks drive the Market rows, each open position's now price,
change, net, stop room and trend, the big total and the range change, all
tagged ``live`` and kept apart from the booked values ("booked as of 16:00").
The views read on the last poll are cached, so a tick repaints those cards
without reading the ledger. Live values stay in this process: nothing here
writes the ledger or reaches the engine, the supervisor or a notification.

When the release the console started on is no longer the current one, a
persistent notice offers ``R`` to restart onto the new release (issue #652).
It is an amber ``warn`` row on the panel surface, under the status row and
above the screens, so it shows on every tab.

Keys: ``1``-``8`` screens, Tab / Shift+Tab cycle them, Ctrl+P command palette,
``D`` ``W`` ``M`` ``Y`` range (day, week, month, year), up and down (or ``j``
``k``) select a position, Enter opens it, Esc or Backspace goes back, ``[``
``]`` previous / next inside a detail screen, ``L`` logs, ``T`` theme, ``?``
help, ``R`` restart (only while the notice shows), ``Q`` quit. On the Tax screen
``[`` ``]`` switch its sub-tabs, ``S`` shows or masks the taxpayer profile and
``E`` says editing waits for MVP2. On the Logs screen ``/`` goes to the query line (Tab
completes a field there), ``T`` opens the selected line's trace, ``F``
follows, ``C`` copies, ``E`` exports and ``D`` ``W`` ``M`` ``Y`` zoom the
volume histogram.

Reference: openspec/changes/readonly-tui-dashboard/change.md;
openspec/changes/live-home-screen/change.md (D-6);
openspec/changes/711-cockpit-theme/change.md;
openspec/changes/712-cockpit-home/change.md;
openspec/changes/713-live-ticker/change.md;
openspec/changes/719-tax-screen/change.md;
openspec/changes/730-drill-down/change.md;
openspec/changes/717-system-screen/change.md;
openspec/changes/716-logs-screen/change.md.
"""

from __future__ import annotations

import os
import sys
from collections import deque
from collections.abc import Callable, Iterable, Sequence
from contextlib import suppress
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta, tzinfo
from decimal import ROUND_HALF_UP, Decimal
from functools import partial
from pathlib import Path
from typing import ClassVar, Final

from rich.console import Group, RenderableType
from rich.text import Text
from textual import events
from textual.app import App, ComposeResult, SystemCommand
from textual.binding import Binding, BindingType
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.screen import ModalScreen, Screen
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import ContentSwitcher, Static

from hermes.application.console import (
    ConsoleReadModel,
    Heartbeat,
    HeartbeatState,
    PortfolioRange,
    PortfolioSummary,
    PositionDetail,
    PositionRow,
    Surface,
    WatchedPair,
    pnl_visible,
)
from hermes.application.console.format import MINUS, fmt_level
from hermes.application.console.live import (
    LiveBoard,
    LivePosition,
    LiveQuote,
    LiveState,
    LiveTickerPort,
    LiveTotal,
    TickerPoller,
    TickOutcome,
    live_position,
    live_total,
    poll_succeeded,
)
from hermes.application.console.logs import LogLine, render_log_line
from hermes.application.console.render import (
    DEMO_TAG,
    RenderContext,
    render_activity,
    render_heartbeat,
    render_market_watch,
    render_portfolio_books,
    render_portfolio_head,
    render_position_detail,
    render_tax_footnote,
    to_plain_text,
)
from hermes.application.console.shadow_figures import SHADOW_TEST_DAYS
from hermes.application.console.shadow_screen import (
    ShadowRecordError,
    ShadowScreen,
    ShadowSources,
    run_words,
    sources_from,
)
from hermes.application.console.system import (
    MachineStats,
    ReleaseHistory,
    ServiceStatus,
    SystemSources,
)
from hermes.application.console.views import ActivityDay, ActivityItem, TaxPending
from hermes.application.tax.screen import TaxpayerProfileView, TaxScreen, TaxScreenSource
from hermes.domain.errors import LedgerError
from hermes.entrypoints.tui_boot import BootScreen, boot_checks
from hermes.entrypoints.tui_briefs import briefs_screen
from hermes.entrypoints.tui_drilldown import (
    DetailMode,
    DetailPane,
    DetailScreen,
    ListState,
    detail_mode,
)
from hermes.entrypoints.tui_home import (
    LOGS_DEMO,
    LOGS_UNAVAILABLE,
    HomeLayout,
    LiveRows,
    counted_line,
    empty_positions,
    home_layout,
    log_tail,
    portfolio_curve,
    portfolio_meters,
    position_rows,
    total_text,
)
from hermes.entrypoints.tui_live import (
    LiveLook,
    eur_rate,
    live_detail_line,
    live_portfolio_lines,
    market_rows,
)
from hermes.entrypoints.tui_logs import READ_LINES, LogsView
from hermes.entrypoints.tui_notices import bar_checked_notice, beep_enabled, should_beep
from hermes.entrypoints.tui_positions import PositionsView, closed_items
from hermes.entrypoints.tui_query import QueryInput
from hermes.entrypoints.tui_reads import (
    CARD_ERRORS,
    SHADOW_ERRORS,
    ConsoleReads,
    Failed,
    ReadRequest,
    ReadSources,
    ShadowFailed,
    SystemReads,
    read_console,
    read_shadow,
)
from hermes.entrypoints.tui_settings import (
    DEFAULT_SETTINGS_PATH,
    ConsoleSettings,
    load_settings,
    save_settings,
)
from hermes.entrypoints.tui_shadow import (
    checks_table,
    day_figure,
    hero_text,
    notebook_rows,
    notebook_text,
    record_text,
    settings_text,
)
from hermes.entrypoints.tui_signals import signals_screen
from hermes.entrypoints.tui_system import (
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
from hermes.entrypoints.tui_tax import EDIT_LATER, PROFILE, TaxPackStatus, TaxTab
from hermes.entrypoints.tui_theme import (
    BASE,
    COMPACT_BELOW,
    DOWN_GLYPH,
    FEED_WIDTH,
    HORIZONTAL_BREAKPOINTS,
    LATE_GLYPH,
    LEFT_COLUMN_STANDARD,
    LEFT_COLUMN_WIDE,
    LIVE_GLYPH,
    MARKET_COLUMN_WIDE,
    METER_CELLS,
    MOTION,
    NEON_THEME,
    NIGHT_THEME,
    THEMES,
    VERTICAL_BREAKPOINTS,
    ThemeChoice,
    Tokens,
    blend,
    data_tokens,
    motion_enabled,
    pulse_steps,
    resolve_theme,
    startup_logo,
    terminal_theme,
    theme_variables,
    tokens_for,
)
from hermes.entrypoints.tui_topbar import bar_elapsed_share, mainnet_text, version_text
from hermes.entrypoints.tui_widgets import (
    LABEL_AI,
    BigNumber,
    HudPanel,
    Meter,
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
    "next_bar_text",
    "osc_title",
    "run_home",
    "terminal_title",
    "title_total",
]

REFRESH_SECONDS: Final = MOTION.poll
_PANEL_PADDING: Final = 2
_SCROLLBAR_CELLS: Final = 1
_NEW_VERSION_NOTICE: Final = "new version deployed · press R to restart"
_UNKNOWN_RELEASE: Final = "unknown"
_LOGS_LINES: Final = 50
_LOGS_READING: Final = "Reading the journal…"
_READ_ONLY: Final = "read-only"
_BRIEF_PENDING: Final = "brief not available yet"
_SIGNALS_PENDING: Final = "Heat grid · coming next · issue #714"
_LEFT_IDS: Final = frozenset(
    {"portfolio", "portfolio-curve", "portfolio-meters", "portfolio-books"}
)
_THIRD_IDS: Final = frozenset({"signals", "log-tail"})
_FULL_WIDTH_IDS: Final = frozenset(
    {
        "shadow",
        "footer",
        "footer-status",
        "countdown",
        "countdown-meter",
        "mainnet",
        "notice",
        "shadow-hero",
        "shadow-checks",
        "shadow-record",
        "shadow-settings",
        "shadow-notebook",
    }
)
_SHADOW_PARTS: Final = ("shadow-checks", "shadow-record", "shadow-settings", "shadow-notebook")
_WIDTH_CLASSES: Final = ("-compact", "-standard", "-wide", "-ultra")
_HEIGHT_CLASSES: Final = ("-short", "-tall", "-towering")
# Spaces the status row's widgets add around their text (padding, margins).
_STATUS_ROW_SLACK: Final = 8
# How far toward the live colour a new activity row's background starts (issue #721).
_FADE_PEAK: Final = 0.4
# The countdown meter: a space, the end caps and the cells (issue #833).
_COUNTDOWN_METER_CELLS: Final = METER_CELLS + 3
# The margins the theme chip and the mainnet badge keep on the status row.
_CHIP_MARGIN: Final = 1
_MAINNET_MARGIN: Final = 1

TABS: Final = (
    Tab("1", "Home"),
    Tab("2", "Signals"),
    Tab("3", "Positions"),
    Tab("4", "Tax"),
    Tab("5", "Shadow test"),
    Tab("6", "Briefs"),
    Tab("7", "System"),
    Tab("8", "Logs"),
)
_HOME: Final = 0
_SHADOW: Final = 4
_SIGNALS: Final = 1
_POSITIONS: Final = 2
_TAX: Final = 3
_BRIEFS: Final = 5
_SYSTEM: Final = 6
_LOGS: Final = 7
_NOTICE_SECONDS: Final = 6
# The big estimated-tax figure shows from the height the big total shows (30 rows).
_BIG_FROM_ROWS: Final = 30
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
_LOGS_FOOTER_CHIPS: Final = (
    ("/", "query"),
    ("↵", "line"),
    ("T", "trace"),
    ("F", "follow"),
    ("C", "copy"),
    ("E", "export"),
    ("D W M Y", "window"),
    ("Q", "quit"),
)
_LOGS_FOOTER_CHIPS_COMPACT: Final = (
    ("/", ""),
    ("↵", ""),
    ("T", "trace"),
    ("F", "follow"),
    ("C", ""),
    ("E", ""),
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

_OSC_TITLE: Final = "\x1b]2;{}\x07"


def _unavailable(title: str, exc: Exception) -> Text:
    """Return the one plain line a card shows instead of its content."""
    if isinstance(exc, LedgerError):
        return Text(f"{title} · could not read the ledger: {exc.msg}", style="yellow")
    if isinstance(exc, ShadowRecordError):
        return Text(f"{title} · could not read the day-0 record: {exc}", style="yellow")
    return Text(f"{title} · not available yet", style="dim")


def _day_count(value: float) -> str:
    """Return the Shadow test day mid count-up (``12 / 90``)."""
    return f"{round(value)} / {SHADOW_TEST_DAYS}"


def _card(title: str, build: Callable[[], RenderableType]) -> RenderableType:
    """Return ``build()``, or the line naming why the card has nothing to show."""
    try:
        return build()
    except CARD_ERRORS as exc:
        return _unavailable(title, exc)


def _clock(at: datetime, tz: tzinfo) -> str:
    return at.astimezone(tz).strftime("%H:%M")


def title_total(total_value: Decimal, surface: Surface) -> str | None:
    """Return the portfolio total for the terminal tab title, in whole euros.

    Args:
        total_value: Cash plus open positions, EUR.
        surface: Where the title is shown; money only where ``pnl_visible`` allows it.

    Returns:
        ``€1,021`` on a surface that may show money, else ``None``.
    """
    if not pnl_visible(surface):
        return None
    whole = total_value.quantize(Decimal(1), rounding=ROUND_HALF_UP)
    sign = MINUS if whole < 0 else ""
    return f"{sign}€{abs(whole):,}"


def terminal_title(heartbeat: Heartbeat | None, tz: tzinfo, total: str | None = None) -> str:
    """Return the terminal tab title for a heartbeat.

    Args:
        heartbeat: The last heartbeat read, or ``None`` when it could not be read.
        tz: Display timezone.
        total: The portfolio total from :func:`title_total`; ``None`` leaves money out.

    Returns:
        ``hermes ● live · €4,456 · next 16:00``, ``hermes ◐ check late · €4,456 · 16:00``,
        ``hermes ✖ down since 18:04``, or ``hermes`` (no total without a heartbeat or when down).
    """
    if heartbeat is None:
        return "hermes"
    if heartbeat.state is HeartbeatState.DOWN and heartbeat.down_since is not None:
        return f"hermes {DOWN_GLYPH} down since {_clock(heartbeat.down_since, tz)}"
    money = f"{total} · " if total else ""
    if heartbeat.state is HeartbeatState.LATE:
        return f"hermes {LATE_GLYPH} check late · {money}{_clock(heartbeat.next_check_at, tz)}"
    return f"hermes {LIVE_GLYPH} live · {money}next {_clock(heartbeat.next_check_at, tz)}"


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


def next_bar_text(heartbeat: Heartbeat | None, now: datetime) -> str:
    """Return the Neon window bar's countdown to the next 4h check, ticking every second.

    Shown in place of the live-feed pulse while there is no ticker (demo data,
    or before the first poll), Code Owner 2026-10-06.

    Args:
        heartbeat: The last heartbeat read.
        now: The current time.

    Returns:
        ``next bar 01:12:05``, ``next bar due`` once the check time has passed,
        or empty when hermes is late, down or unknown.
    """
    if heartbeat is None or heartbeat.state is not HeartbeatState.OK:
        return ""
    left = heartbeat.next_check_at - now
    if left <= timedelta(0):
        return "next bar due"
    hours, rest = divmod(int(left.total_seconds()), 3600)
    minutes, seconds = divmod(rest, 60)
    return f"next bar {hours:02d}:{minutes:02d}:{seconds:02d}"


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


class _LogsPanel(_PanelScreen):
    """The logs panel; while it is open, every read takes the journal's last lines."""


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


def _known_release(name: str | None) -> str | None:
    """Return ``name``, or ``None`` when the release is unreadable or reads as unknown."""
    return None if name == _UNKNOWN_RELEASE else name


class HomeApp(App[None]):
    """The read-only Cockpit console.

    Args:
        model: Where every figure comes from; only read methods are called.
        refresh_seconds: How often the screen re-reads the model.
    """

    TITLE = "hermes"
    HORIZONTAL_BREAKPOINTS = HORIZONTAL_BREAKPOINTS
    VERTICAL_BREAKPOINTS = VERTICAL_BREAKPOINTS
    CSS = f"""
    Screen {{ background: $hud-void; }}
    #screens, #screens > Container, #screens > VerticalScroll, #body,
    #left-column, #right-column, #third-column, #mid-row, #top, #footer-row {{
        background: $hud-void;
    }}
    #window-bar, #tabs, #top {{ height: 1; }}
    #demo-tag {{ width: auto; }}
    #release {{ width: auto; color: $hud-muted; }}
    #heartbeat {{ width: 1fr; }}
    #countdown {{ width: auto; color: $hud-muted; }}
    #mainnet {{ width: auto; margin-right: 1; }}
    #countdown-meter {{ width: auto; }}
    #theme-chip {{ width: auto; margin-left: 1; }}
    #down-row {{ display: none; height: auto; }}
    #down-row.-down {{ display: block; }}
    #notice {{
        display: none; height: 1; padding: 0 1;
        background: $hud-panel; color: $hud-warn; text-style: bold;
        border-left: outer $hud-warn;
    }}
    #notice.-on {{ display: block; }}
    #screens {{ height: 1fr; }}
    #tab-1 {{ height: 1fr; }}
    #body {{
        height: 1fr; layout: vertical; overflow-y: auto;
        scrollbar-gutter: stable; scrollbar-size-vertical: 1;
    }}
    #body.-standard, #body.-wide, #body.-ultra {{ layout: horizontal; }}
    #left-column, #right-column, #third-column, #mid-row {{ height: auto; }}
    #left-column, #right-column {{ width: 1fr; }}
    #body.-standard #left-column {{ width: {LEFT_COLUMN_STANDARD}; }}
    #body.-wide #left-column, #body.-ultra #left-column {{ width: {LEFT_COLUMN_WIDE}; }}
    #body.-standard #right-column, #body.-wide #right-column, #body.-ultra #right-column {{
        margin-left: 1;
    }}
    #third-column {{ display: none; width: 2fr; margin-left: 1; }}
    #body.-ultra #third-column {{ display: block; }}
    #body.-ultra #right-column {{ width: 3fr; }}
    #mid-row {{ layout: vertical; }}
    #body.-wide #mid-row, #body.-ultra #mid-row {{ layout: horizontal; }}
    #body.-wide #market-panel, #body.-ultra #market-panel {{
        width: {MARKET_COLUMN_WIDE}; margin-right: 1;
    }}
    #portfolio-curve {{ margin-top: 1; }}
    #shadow, #footer-row, #panel-footer {{ height: 1; }}
    #footer {{ width: 1fr; }}
    #footer-status, #panel-footer {{ width: auto; color: $hud-muted; }}
    #panel {{ height: 1fr; padding: 0 1; overflow-y: auto; }}
    #shadow-body {{ height: auto; layout: vertical; }}
    #tab-5 {{ scrollbar-gutter: stable; scrollbar-size-vertical: 1; }}
    #shadow-body.-wide, #shadow-body.-ultra {{ layout: horizontal; }}
    #shadow-left, #shadow-right {{ height: auto; width: 1fr; }}
    #shadow-body.-wide #shadow-right, #shadow-body.-ultra #shadow-right {{ margin-left: 1; }}
    #system-body {{ layout: grid; grid-size: 1; grid-gutter: 0 2; grid-rows: auto; height: auto; }}
    #system-body.-split {{ grid-size: 2; grid-gutter: 1 2; }}
    .sys-column {{ height: auto; }}
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
        Binding("a,A", "toggle_quiet", "checks", show=False),
        Binding("left_square_bracket", "tax_subtab(-1)", "previous sub-tab", show=False),
        Binding("right_square_bracket", "tax_subtab(1)", "next sub-tab", show=False),
        Binding("s,S", "tax_show_profile", "show profile", show=False),
        Binding("e,E", "tax_edit_profile", "edit profile", show=False),
        Binding("l,L", "logs", "logs", show=False),
        Binding("t,T", "pick_theme", "theme", show=False),
        Binding("question_mark", "help", "help", show=False),
        Binding("r,R", "restart", "restart", show=False),
        Binding("q,Q", "quit", "quit", show=False),
    ]

    def __init__(
        self,
        model: ConsoleReadModel,
        *,
        refresh_seconds: float = REFRESH_SECONDS,
        journal: Callable[[int], Sequence[LogLine] | None] | None = None,
        release: Callable[[], str] | None = None,
        version: str | None = None,
        allow_mainnet: bool | None = None,
        clock: Callable[[], datetime] = _system_time,
        motion: bool | None = None,
        settings_path: Path | None = None,
        theme: ThemeChoice | None = None,
        exposure_cap: Decimal | None = None,
        shadow: ShadowSources | None = None,
        ticker: LiveTickerPort | None = None,
        tax: TaxScreenSource | None = None,
        taxpayer_profile: TaxpayerProfileView | None = None,
        tax_pack: Callable[[int], TaxPackStatus | None] | None = None,
        boot: bool = False,
        background_reads: bool = False,
        system: SystemSources | None = None,
        exports: Path | None = None,
    ) -> None:
        """Hold the read model, the journal source and the screen state.

        Args:
            model: The read model every panel draws from.
            refresh_seconds: Seconds between redraws.
            journal: Returns the last N journal lines, or ``None`` when the
                journal is not readable here; ``None`` means no journal at all.
            release: Returns the running release's name, re-read on every
                redraw; ``None`` leaves the top line without a release.
            version: The package version in its release form
                (``v0.2.0``, issue #815), shown before the release;
                ``None`` shows the release alone, as ``release <name>``.
            allow_mainnet: The config's ``allow_mainnet``, read the way
                ``hermes serve`` reads it (issue #833); ``None`` when the
                config could not be read, which a live console shows as an
                amber ``mainnet ?`` (never a default of "off"). A demo
                scenario cannot trade, so it always shows mainnet off.
            clock: Returns the current time; the clock, the countdown and night
                mode tick from it.
            motion: Run the animations; ``None`` reads ``HERMES_CONSOLE_MOTION``.
            settings_path: Where the theme choice is read and saved; ``None``
                neither reads nor writes a file.
            theme: A choice from ``--theme``; it wins over the saved one and is saved.
            exposure_cap: The roster's pre-registered exposure cap as a share
                (``0.15``); ``None`` makes the exposure meter say it is unknown.
            shadow: Where the Shadow test screen and the 90-day line read
                from; ``None`` takes what ``model`` itself serves.
            ticker: The live public ticker (issue #713); ``None`` shows booked
                values only and the Market panel without prices.
            tax: The Tax screen's source (issue #719); ``None`` makes it say the
                estimate is not available.
            taxpayer_profile: What the Tax screen may say about the profile;
                read once by the caller, never over ``--vps``.
            tax_pack: Returns the year's tax pack state.
            boot: Open on the cold start screen (#1020); shown only with motion on.
            background_reads: Read the sources in a worker thread and paint
                when the reads arrive (issue #1001), so a slow read never
                holds a key or an animation; ``False`` reads and paints at once.
            system: The System screen's read-only sources; ``None`` shows every
                System panel as "not available here".
            exports: Where the Logs screen exports a line; ``None`` (over
                ``--vps``) says export works from the PC instead.
        """
        super().__init__()
        self._exports = exports
        # The checks need the time zone, which the model only gives once mounted.
        self._boot_checks = (
            partial(boot_checks, model, journal=journal, ticker=ticker) if boot else None
        )
        self._model = model
        self._journal = journal
        self._release = release
        self._version = version
        self._release_name: str | None = None
        self._demo_tag = Text("")
        self._allow_mainnet = allow_mainnet
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
        self._show_quiet = False
        self._tab = _HOME
        self._tab_history: list[int] = []
        self._content: dict[str, RenderableType] = {}
        self._started_release = _known_release(self._read_release())
        self._release_is_new = False
        self.restart_requested = False
        self._heartbeat: Heartbeat | None = None
        # The last heartbeat read, kept across a failed read so the bell never rings twice.
        self._last_read: Heartbeat | None = None
        self._ctx = RenderContext(surface=Surface.OPERATOR_TUI, now=clock())
        self._pulse = self._radar = 0
        self._exposure_cap = exposure_cap
        self._init_reads(
            ReadSources(
                model,
                shadow or sources_from(model),
                journal=journal,
                release=release,
                tax=tax,
                tax_pack=tax_pack,
                system=system or SystemSources(),
            ),
            taxpayer_profile,
            background=background_reads,
        )
        self._layout: HomeLayout = home_layout(80, 24)
        self._empty_facts: tuple[tuple[str, ...], datetime | None] | None = None
        self._title_sent: str | None = None
        self._title_total: str | None = None
        self._init_system()
        self.effect_timers: dict[str, Timer] = {}
        self._init_live_feed(ticker, clock)
        self._summary: PortfolioSummary | None = None
        self._rows: tuple[PositionRow, ...] = ()
        self._details: dict[str, PositionDetail] = {}
        self._watch: tuple[WatchedPair, ...] | None = None
        for hermes_theme in THEMES:
            self.register_theme(hermes_theme)

    def _init_system(self) -> None:
        self._system = self._sources.system
        self._sys_cursor: str | None = None
        self._service_open = False
        self._services: tuple[ServiceStatus, ...] | None = None
        self._releases: ReleaseHistory | None = None
        self._machine: MachineStats | None = None
        self._bar_seconds: tuple[Decimal, ...] | None = None

    def _init_reads(
        self,
        sources: ReadSources,
        taxpayer_profile: TaxpayerProfileView | None,
        *,
        background: bool,
    ) -> None:
        self._sources = sources
        self._shadow = sources.shadow
        self._taxpayer_profile = taxpayer_profile
        self._background_reads = background
        self._reads: ConsoleReads | None = None
        self._reading = False
        self._read_again = False

    def _init_live_feed(self, ticker: LiveTickerPort | None, clock: Callable[[], datetime]) -> None:
        self._poller = None if ticker is None else TickerPoller(ticker, clock)
        self._board = LiveBoard()
        self._fetching = False
        self._flashes: dict[str, int] = {}
        self._flash_timer: Timer | None = None
        self._activity_seen: frozenset[ActivityItem] | None = None
        self._fresh_activity: frozenset[ActivityItem] = frozenset()
        self._fade_step = 0
        self._fade_timer: Timer | None = None
        self._polls: deque[bool] = deque(maxlen=FEED_WIDTH)
        self._feed_timer: Timer | None = None
        self._live_state = LiveState.WAITING

    def get_theme_variable_defaults(self) -> dict[str, str]:
        """Give TCSS the ``hud-*`` variables before a hermes theme is applied."""
        return theme_variables(BASE)

    def compose(self) -> ComposeResult:
        """Lay out the chrome, the eight screens, the 90-day line and the footer."""
        yield WindowBar(id="window-bar")
        yield TabBar(TABS, id="tabs")
        with Horizontal(id="top"):
            yield Static(id="mainnet")
            yield Static(id="demo-tag")
            yield Static(id="release")
            yield Static(id="heartbeat")
            yield Static(id="countdown")
            yield Static(id="countdown-meter")
            yield ThemeChip(id="theme-chip")
        yield Static(id="down-row")
        yield Static(id="notice")
        with ContentSwitcher(initial="tab-1", id="screens"):
            with Container(id="tab-1"), Container(id="body"):
                with Vertical(id="left-column"):
                    yield HudPanel(
                        BigNumber(motion=self._motion, id="portfolio-total"),
                        Static(id="portfolio"),
                        Static(id="portfolio-curve"),
                        Static(id="portfolio-meters"),
                        Static(id="portfolio-books"),
                        title="Portfolio",
                        subtitle="D W M Y range",
                        id="portfolio-panel",
                    )
                with Vertical(id="right-column"):
                    yield HudPanel(
                        Static(id="positions"),
                        title="Positions",
                        subtitle="↑↓ select · ↵ detail",
                        id="positions-panel",
                    )
                    yield DetailPane(
                        Static(id="detail"),
                        title="Position detail",
                        subtitle="Esc close",
                        id="detail-panel",
                    )
                    with Container(id="mid-row"):
                        yield HudPanel(Static(id="market"), title="Market", id="market-panel")
                        yield HudPanel(Static(id="activity"), title="Activity", id="activity-panel")
                    yield HudPanel(
                        Static(id="brief"),
                        title="Engine brief",
                        subtitle="never sees profit",
                        ai=True,
                        id="brief-panel",
                    )
                with Vertical(id="third-column"):
                    yield HudPanel(Static(id="signals"), title="Signals", id="signals-panel")
                    yield HudPanel(
                        Static(id="log-tail"), title="Log tail", subtitle="L logs", id="log-panel"
                    )
            yield self._compose_shadow()
            yield from self._compose_system()
            for index, tab in enumerate(TABS[1:], start=1):
                if index == _POSITIONS:
                    yield PositionsView(
                        detail_body=self._detail_content, now=self._clock, id=f"tab-{tab.key}"
                    )
                    continue
                if index == _TAX:
                    yield TaxTab(now=self._clock, motion=self._motion, id=f"tab-{tab.key}")
                    continue
                if index == _LOGS:
                    with Container(id=f"tab-{tab.key}"):
                        yield LogsView(
                            now=self._clock,
                            tz=lambda: self._ctx.tz,
                            exports=self._exports,
                            demo=self._model.demo_scenario is not None,
                            refresh=self._redraw,
                            id="logs-view",
                        )
                    continue
                if index in (_SHADOW, _SYSTEM):
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

    def _compose_shadow(self) -> VerticalScroll:
        """Return tab 5: the hero, then checks and day 0 beside settings and notebook."""
        return VerticalScroll(
            HudPanel(
                BigNumber(motion=self._motion, counting=_day_count, id="shadow-day"),
                Static(id="shadow-hero"),
                title="Shadow test",
                subtitle="booked · read-only",
                id="shadow-hero-panel",
            ),
            Container(
                Vertical(
                    HudPanel(
                        Static(id="shadow-checks"),
                        title="Certification checks",
                        subtitle="pass · fail · pending",
                        id="shadow-checks-panel",
                    ),
                    HudPanel(Static(id="shadow-record"), title="Day 0", id="shadow-record-panel"),
                    id="shadow-left",
                ),
                Vertical(
                    HudPanel(
                        Static(id="shadow-settings"),
                        title="Frozen settings",
                        subtitle="never edited here",
                        id="shadow-settings-panel",
                    ),
                    HudPanel(
                        Static(id="shadow-notebook"),
                        title="Lab notebook",
                        subtitle="newest first",
                        id="shadow-notebook-panel",
                    ),
                    id="shadow-right",
                ),
                id="shadow-body",
            ),
            id=f"tab-{TABS[_SHADOW].key}",
        )

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
        if self._boot_checks is not None and self._motion:
            self.push_screen(
                BootScreen(
                    self._boot_checks(tz=self._render_context().tz),
                    lambda: tokens_for(self.theme),
                    release=self._read_release(),
                )
            )

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
        """Start the pulse, the shimmer and the radar; nothing runs with motion off."""
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
            self.effect_timers["shimmer"] = self.set_interval(MOTION.shimmer, self._start_shimmer)
            self.effect_timers["radar"] = self.set_interval(MOTION.radar_tick, self._step_radar)

    def _start_shimmer(self) -> None:
        """Start a shimmer pass on every shown big number (Home total, Shadow day)."""
        if not self.screen_stack:
            return
        for big in self._home.query(BigNumber).results(BigNumber):
            big.start_shimmer()

    def _step_radar(self) -> None:
        """Turn the empty-positions radar one step (only while there is no position)."""
        self._radar += 1
        if self._empty_facts is not None:
            coins, next_at = self._empty_facts
            self._set(
                "positions",
                empty_positions(coins, next_at, self._ctx, self._data, self._radar),
            )

    def _step_pulse(self) -> None:
        self._pulse = (self._pulse + 1) % MOTION.pulse_steps
        self._paint_heartbeat()
        if self._live_state is LiveState.LIVE:
            self._paint_live()

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
        bar = self._alive(WindowBar)
        if bar is not None:
            bar.clock = now.astimezone(self._ctx.tz).strftime("%H:%M:%S")
            if not self._polls:
                bar.show_next_bar(next_bar_text(self._heartbeat, now))
        self._paint_status_badges()
        countdown = countdown_text(self._heartbeat, now)
        on_row, full_chip = self._status_row_layout(countdown)
        self._paint_chip(compact=not full_chip)
        self._set("countdown", Text(f" {countdown}" if on_row else ""))
        meter = self._countdown_meter(now) if on_row and self._meter_fits(countdown) else None
        self._set("countdown-meter", Text("") if meter is None else Text(" ").append_text(meter))
        short = countdown.removeprefix("in ")
        status = f"next {short} · {_READ_ONLY}" if countdown and not on_row else _READ_ONLY
        self._set("footer-status", Text(status))
        self._write_title(terminal_title(self._heartbeat, self._ctx.tz, self._title_total))
        self._tick_live(now)

    def _status_row_layout(self, countdown: str) -> tuple[bool, bool]:
        """Return whether the countdown and the full theme chip fit on the status row.

        The heartbeat is never cut: at compact width, or when a demo tag and a
        release leave too little room, the countdown moves to the footer and,
        if still needed, the chip shrinks to its glyph.
        """
        width = self.size.width
        if width < COMPACT_BELOW:
            return False, False
        used = self._status_row_used()
        chip = self._chip_text(compact=False).cell_len
        on_row = bool(countdown) and used + chip + len(countdown) + 1 <= width
        return on_row, used + chip <= width

    def _status_row_used(self, *, release: bool = True) -> int:
        """Return the cells the status row's fixed parts take, slack included."""
        width = self.size.width
        parts = ["demo-tag", "heartbeat"]
        if release:
            parts.append("release")
        if self.theme != NEON_THEME:
            parts.append("mainnet")
        return _STATUS_ROW_SLACK + sum(
            len(to_plain_text(self._content.get(part, Text("")), width=width).strip())
            for part in parts
        )

    def _paint_status_badges(self) -> None:
        """Draw the mainnet badge, then fit the version line around it and the heartbeat."""
        self._paint_mainnet()
        self._fit_release()

    def _fit_release(self) -> None:
        """Shorten the version line, then the demo tag, until the heartbeat fits whole.

        With a version (issue #815) the line drops the release first, then
        itself; without one the ``release <name>`` line is kept as before.
        If the heartbeat still does not fit, the demo tag drops its scenario
        name. The mainnet badge and the heartbeat are never cut.
        Measured in real cells (no slack), so a row that fits is left as it is.
        """
        full = version_text(self._version, self._release_name)
        candidates = (full, self._version, "") if self._version else (full,)
        scenario = self._model.demo_scenario
        short_tag = Text(f" {DEMO_TAG} ", style=self._demo_tag_style()) if scenario else Text("")
        width = self.size.width
        fixed = self._chip_text(compact=True).cell_len + _CHIP_MARGIN
        fixed += len(to_plain_text(self._content.get("heartbeat", Text("")), width=width).strip())
        if self.theme != NEON_THEME:
            mainnet = self._content.get("mainnet", Text(""))
            fixed += len(to_plain_text(mainnet, width=width).strip()) + _MAINNET_MARGIN
        fitted: str | None = None
        tag = self._demo_tag
        for tag in (self._demo_tag, short_tag):
            room = width - fixed - tag.cell_len
            fitted = next((text for text in candidates if len(text) <= room), None)
            if fitted is not None:
                break
        self._set("demo-tag", tag)
        self._set("release", Text(candidates[-1] if fitted is None else fitted))

    def _meter_fits(self, countdown: str) -> bool:
        """Return whether the countdown meter fits after the countdown and the full chip."""
        chip = self._chip_text(compact=False).cell_len
        needed = self._status_row_used() + chip + len(countdown) + 1 + _COUNTDOWN_METER_CELLS
        return needed <= self.size.width

    def _countdown_meter(self, now: datetime) -> Text | None:
        """Return the meter of the time passed since the last bar check, or ``None``."""
        share = bar_elapsed_share(self._heartbeat, now)
        if share is None:
            return None
        return Meter(share, width=METER_CELLS, caps=True).render(tokens_for(self.theme))

    @property
    def mainnet_state(self) -> bool | None:
        """Return the mainnet state the badge shows.

        A demo scenario cannot trade, so it reads off whatever the config
        says; a live console shows the config's ``allow_mainnet``, ``None``
        when the config could not be read.
        """
        if self._model.demo_scenario is not None:
            return False
        return self._allow_mainnet

    def _paint_mainnet(self) -> None:
        """Draw the mainnet badge: in the window bar in Neon, on the status row otherwise."""
        neon = self.theme == NEON_THEME
        allow_mainnet = self.mainnet_state
        bar = self._alive(WindowBar)
        if bar is not None:
            bar.show_mainnet(allow_mainnet)
        badge = mainnet_text(allow_mainnet, tokens_for(self.theme))
        self._set("mainnet", Text("") if neon else badge)
        slot = self._alive(Widget, "#mainnet")
        if slot is not None:
            slot.display = not neon

    def _chip_text(self, *, compact: bool) -> Text:
        return theme_chip_text(
            self._choice, tokens_for(self.theme), night=self.is_night, compact=compact
        )

    def _paint_chip(self, *, compact: bool) -> None:
        chip = self._alive(ThemeChip)
        if chip is not None:
            chip.update(self._chip_text(compact=compact))

    def _apply_theme(self, now: datetime) -> None:
        name = resolve_theme(self._choice, now.astimezone(self._ctx.tz).time())
        night_changed = (name == NIGHT_THEME) != self.is_night
        if self.theme != name:
            self.ansi_theme_dark = terminal_theme(data_tokens(name))
            self.theme = name
        bar = self._alive(WindowBar)
        if bar is not None:
            bar.display = name == NEON_THEME
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
    # Live ticker (issue #713)
    # ------------------------------------------------------------------

    @property
    def board(self) -> LiveBoard:
        """Return the live ticks received so far (in memory only)."""
        return self._board

    @property
    def flashing(self) -> dict[str, int]:
        """Return the coins whose cells flash now: ``1`` up, ``-1`` down."""
        return dict(self._flashes)

    def _tick_live(self, now: datetime) -> None:
        """Start a ticker poll when one is due; repaint when the live state changes."""
        state = self._board.status(now).state
        if state is not self._live_state:
            self._live_state = state
            self._paint_live()
        poller = self._poller
        if poller is None or self._fetching or not poller.due(now):
            return
        poller.begin(now)
        self._fetching = True
        self.run_worker(
            partial(self._fetch_ticks, poller),
            name="ticker",
            group="ticker",
            thread=True,
            exit_on_error=False,
        )

    def _fetch_ticks(self, poller: TickerPoller) -> None:
        """Fetch one tick per coin (worker thread) and hand them to the UI thread."""
        outcomes = poller.fetch()
        # The app may be closing while the fetch ran; its ticks are then dropped.
        with suppress(RuntimeError):
            self.call_from_thread(self.apply_ticks, outcomes)

    def apply_ticks(self, outcomes: Sequence[TickOutcome]) -> None:
        """Record fetched ticks, flash the moved cells and repaint the live cards.

        Runs on the UI thread. The ticks go to the in-memory board only.

        Args:
            outcomes: One tick per coin from :meth:`TickerPoller.fetch`.
        """
        self._fetching = False
        self._polls.append(poll_succeeded(outcomes))
        self._show_feed(bright=self._motion)
        moved = self._board.apply(outcomes)
        self._live_state = self._board.status(self._clock()).state
        if moved and self._motion:
            self._flashes = moved
            if self._flash_timer is not None:
                self._flash_timer.stop()
            self._flash_timer = self.set_timer(MOTION.flash, self._end_flash)
        self._paint_live()

    @property
    def feed(self) -> tuple[bool, ...]:
        """Return the recent poll results the window bar's feed pulse shows, oldest first."""
        return tuple(self._polls)

    def _show_feed(self, *, bright: bool) -> None:
        """Draw the feed pulse; a bright newest glyph dims again after ``MOTION.flash``."""
        bar = self._alive(WindowBar)
        if bar is not None:
            bar.show_feed(self._polls, bright=bright)
        if self._feed_timer is not None:
            self._feed_timer.stop()
            self._feed_timer = None
        if bright:
            self._feed_timer = self.set_timer(MOTION.flash, self._dim_feed)

    def _dim_feed(self) -> None:
        self._feed_timer = None
        self._show_feed(bright=False)

    def _end_flash(self) -> None:
        self._flashes = {}
        self._flash_timer = None
        self._paint_live()

    def _live_look(self) -> LiveLook | None:
        """Return the live layer's look, or ``None`` with no ticker."""
        if self._poller is None:
            return None
        heartbeat = self._heartbeat
        return LiveLook(
            status=self._board.status(self._clock()),
            tokens=self._data,
            dot=self.live_dot_colour,
            ctx=self._ctx,
            flashes=self._flashes,
            booked_at=None if heartbeat is None else heartbeat.last_check_at,
        )

    def _live_positions(self, look: LiveLook | None) -> dict[str, LivePosition]:
        if look is None or not look.showing:
            return {}
        prices = self._board.prices()
        return {
            row.coin: live_position(row, self._details.get(row.coin), prices[row.coin])
            for row in self._rows
            if row.coin in prices
        }

    def _live_total(self, look: LiveLook | None) -> LiveTotal | None:
        if look is None or not look.showing or self._summary is None:
            return None
        return live_total(self._summary, self._rows, self._board.prices())

    def _paint_live(self) -> None:
        """Repaint the cards that show live values, from the views of the last poll."""
        if not self.is_running or not self.screen_stack or self._poller is None:
            return
        self._paint_portfolio()
        self._paint_positions()
        self._paint_market()
        if self._detail_open and self.is_wide and self._selected is not None:
            self._set("detail", self._detail_content(self._selected))

    # ------------------------------------------------------------------
    # Drawing
    # ------------------------------------------------------------------

    @property
    def is_wide(self) -> bool:
        """Return whether the home screen has more than one column."""
        return self.size.width >= COMPACT_BELOW

    @property
    def layout(self) -> HomeLayout:
        """Return the home layout of the current terminal size."""
        return self._layout

    @property
    def _data(self) -> Tokens:
        """Return the token sheet the data is coloured from (up, down, warn)."""
        return data_tokens(self.theme)

    @property
    def tab(self) -> int:
        """Return the index of the shown screen tab."""
        return self._tab

    def plain_text(self, widget_id: str) -> str:
        """Return the text of one widget without styles (for tests and review).

        Args:
            widget_id: ``mainnet``, ``demo-tag``, ``release``, ``heartbeat``, ``down-row``,
                ``portfolio``, ``positions``, ``market``, ``activity``, ``detail``,
                ``shadow``, ``countdown``, ``countdown-meter``, ``footer`` or
                ``footer-status``.

        Returns:
            The rendered text, one line per row.
        """
        content = self._content.get(widget_id, Text(""))
        return to_plain_text(content, width=max(self._inner(widget_id), 20))

    def _inner(self, widget_id: str = "positions") -> int:
        """Return the text width inside the panel that holds ``widget_id``."""
        layout = home_layout(self.size.width, self.size.height)
        if widget_id in _FULL_WIDTH_IDS:
            return self.size.width
        if widget_id in _LEFT_IDS:
            column = layout.left
        elif widget_id in _THIRD_IDS:
            column = layout.third
        elif layout.market_beside_activity and widget_id == "market":
            column = MARKET_COLUMN_WIDE
        elif layout.market_beside_activity and widget_id == "activity":
            column = layout.right - MARKET_COLUMN_WIDE - 1
        else:
            column = layout.right
        return column - _PANEL_PADDING

    def _render_context(self) -> RenderContext:
        try:
            ctx = RenderContext.for_model(
                self._model,
                surface=Surface.OPERATOR_TUI,
                width=self._inner(),
                down_hint="press L for why",
            )
        except CARD_ERRORS:
            return RenderContext(
                surface=Surface.OPERATOR_TUI, now=datetime.now(UTC), width=self._inner()
            )
        return replace(ctx, demo_scenario=None)

    def _set(self, widget_id: str, content: RenderableType) -> None:
        self._content[widget_id] = content
        static = self._alive(Static, f"#{widget_id}")
        if static is not None:
            static.update(content)

    @property
    def _home(self) -> Screen[object]:
        return self.screen_stack[0]

    # The clock, the poll and the effect timers keep firing while the app
    # tears its widgets down at exit; a widget that is gone then has nothing
    # left to draw, so its lookup gives ``None`` instead of raising.
    def _alive[W: Widget](self, kind: type[W], selector: str | None = None) -> W | None:
        """Return a home-screen widget, or ``None`` once it is torn down.

        Args:
            kind: The widget class to find (and the type to expect).
            selector: A CSS selector such as ``#portfolio-total``; ``None``
                matches on ``kind`` alone.

        Returns:
            The first matching widget, or ``None`` when the home screen or the
            widget is gone.
        """
        if not self.screen_stack:
            return None
        home = self._home
        try:
            return home.query_one(kind) if selector is None else home.query_one(selector, kind)
        except NoMatches:
            return None

    def _positions_coins(self) -> list[str]:
        return [row.coin for row in self._rows]

    def _paint_heartbeat(self) -> None:
        heartbeat = self._heartbeat
        if heartbeat is None or not self.screen_stack:
            return
        ctx = replace(self._ctx, live_dot=f"bold {self.live_dot_colour}")
        down = heartbeat.state is HeartbeatState.DOWN
        self._set("heartbeat", Text("") if down else render_heartbeat(heartbeat, ctx))
        self._set("down-row", render_heartbeat(heartbeat, ctx) if down else Text(""))
        row = self._alive(Widget, "#down-row")
        if row is not None:
            row.set_class(down, "-down")

    def _announce(self, previous: Heartbeat | None, current: Heartbeat, ctx: RenderContext) -> None:
        """Toast a newly checked bar; ring the bell on turning late or down when asked to."""
        notice = bar_checked_notice(previous, current, ctx.tz)
        if notice is not None:
            self.notify(notice, timeout=_NOTICE_SECONDS)
        if beep_enabled(os.environ) and should_beep(previous, current):
            self.bell()

    def _paint_release(self, release: str | None) -> None:
        """Remember the running release for the version line; show the notice once it changed."""
        self._release_name = release
        self._release_is_new = self._release_changed(release)
        self._set("notice", Text(_NEW_VERSION_NOTICE) if self._release_is_new else Text(""))
        notice = self._alive(Widget, "#notice")
        if notice is not None:
            notice.set_class(self._release_is_new, "-on")

    def _redraw(self) -> None:
        """Read what the shown screens need, then paint it.

        With background reads the read runs in a worker and the paint follows
        when it arrives; a redraw asked for meanwhile reads again right after.
        """
        if not self.is_running or not self.screen_stack:
            return
        request = self._read_request()
        if not self._background_reads:
            self._paint(read_console(self._sources, request))
            return
        if self._reading:
            self._read_again = True
            return
        self._reading = True
        self.run_worker(partial(self._read_off_thread, request), group="console-reads", thread=True)

    def _read_request(self) -> ReadRequest:
        layout = home_layout(self.size.width, self.size.height)
        return ReadRequest(
            width=self._inner(),
            range_=self._range,
            include_quiet=self._show_quiet,
            log_tail_rows=layout.log_rows if layout.third else None,
            logs_lines=self._logs_lines_wanted(),
            briefs=self._tab == _BRIEFS,
            signals=self._tab == _SIGNALS,
            tax=self._tab == _TAX,
            system=self._tab == _SYSTEM,
        )

    def _logs_lines_wanted(self) -> int | None:
        """Return how many journal lines the shown Logs screen or panel needs read, if any."""
        if self._tab == _LOGS and any(v.wants_journal for v in self._home.query(LogsView)):
            return READ_LINES
        return _LOGS_LINES if self._logs_panel_open else None

    def _read_off_thread(self, request: ReadRequest) -> None:
        reads = read_console(self._sources, request)
        try:
            self.call_from_thread(self._reads_arrived, reads)
        except RuntimeError:
            # Q can quit the console while a read is in flight; Textual then
            # raises here, and the reads have nowhere to show. A paint that
            # fails while the console still runs is a real error.
            if self.is_running:
                raise

    def _reads_arrived(self, reads: ConsoleReads) -> None:
        self._reading = False
        self._paint(reads)
        if self._read_again:
            self._read_again = False
            self._redraw()

    def _paint(self, reads: ConsoleReads) -> None:
        """Paint every card from one set of reads."""
        if not self.is_running or not self.screen_stack:
            return
        self._reads = reads
        ctx = reads.ctx
        self._ctx = ctx
        scenario = self._model.demo_scenario
        # Drawn by _fit_release once the heartbeat and the badge are known.
        self._demo_tag = (
            Text(f" {DEMO_TAG} · scenario {scenario} ", style=self._demo_tag_style())
            if scenario
            else Text("")
        )
        self._paint_release(reads.release)
        self._paint_heartbeat_read(reads.heartbeat, ctx)
        self._paint_status_badges()
        portfolio = reads.portfolio
        self._title_total = (
            None
            if isinstance(portfolio, CARD_ERRORS)
            else title_total(portfolio.total_value, ctx.surface)
        )

        layout = home_layout(self.size.width, self.size.height)
        self._layout = layout
        body = self._alive(Widget, "#body")
        if body is not None:
            for name in (*_WIDTH_CLASSES, *_HEIGHT_CLASSES):
                body.set_class(name in layout.classes, name)
        self._paint_portfolio_read(portfolio)
        self._paint_positions_read(reads)
        self._paint_market_read(reads.watch)
        self._note_fresh_activity(reads.activity)
        self._paint_activity(reads.activity)
        self._set("brief", Text(_BRIEF_PENDING, style="dim"))
        if layout.third:
            self._paint_third_column(reads, layout)
        self._paint_run_line(reads)
        self._paint_detail()
        self._paint_tab_screen(reads, layout)
        screen = self.screen
        if isinstance(screen, _PanelScreen | DetailScreen):
            screen.refresh_panel()

    def _paint_activity(self, activity: tuple[ActivityDay, ...] | Failed) -> None:
        """Draw the Activity card; a new row wears its fading highlight."""
        if isinstance(activity, CARD_ERRORS):
            self._set("activity", _unavailable("Activity", activity))
            return
        share = 1 - self._fade_step / MOTION.activity_fade_steps
        emphasis = {
            item: f"on {blend(self._data.panel, self._data.live, _FADE_PEAK * share)}"
            for item in self._fresh_activity
        }
        self._set(
            "activity",
            render_activity(
                activity,
                self._ctx,
                limit=self._layout.activity_rows,
                icons=True,
                emphasis=emphasis,
            ),
        )

    def _note_fresh_activity(self, activity: tuple[ActivityDay, ...] | Failed) -> None:
        """Start the highlight fade for rows that were not in the last read.

        The first read of the console highlights nothing, and with motion off
        nothing fades.
        """
        if isinstance(activity, CARD_ERRORS):
            return
        items = frozenset(item for day in activity for item in day.items)
        seen, self._activity_seen = self._activity_seen, items
        if seen is None or not self._motion or not (items - seen):
            return
        self._fresh_activity = items - seen
        self._fade_step = 0
        if self._fade_timer is not None:
            self._fade_timer.stop()
        self._fade_timer = self.set_interval(
            MOTION.activity_fade / MOTION.activity_fade_steps, self._fade_activity
        )

    def _fade_activity(self) -> None:
        """Take one step of the highlight fade; the last step clears it."""
        self._fade_step += 1
        if self._fade_step >= MOTION.activity_fade_steps:
            self._fresh_activity = frozenset()
            self._fade_step = 0
            if self._fade_timer is not None:
                self._fade_timer.stop()
                self._fade_timer = None
        reads = self._reads
        if reads is not None:
            self._paint_activity(reads.activity)

    def _paint_heartbeat_read(self, heartbeat: Heartbeat | Failed, ctx: RenderContext) -> None:
        if isinstance(heartbeat, CARD_ERRORS):
            self._heartbeat = None
            self._set("heartbeat", _unavailable("Heartbeat", heartbeat))
            self._set("down-row", Text(""))
            row = self._alive(Widget, "#down-row")
            if row is not None:
                row.set_class(False, "-down")
            return
        self._heartbeat = heartbeat
        self._announce(self._last_read, heartbeat, ctx)
        self._last_read = heartbeat
        self._paint_heartbeat()

    def _paint_tab_screen(self, reads: ConsoleReads | None, layout: HomeLayout) -> None:
        """Draw the chrome and the screen of the shown tab from the last reads."""
        self._redraw_chrome()
        if reads is None:
            return
        ctx = reads.ctx
        if self._tab == _SHADOW:
            self._paint_shadow(reads.shadow, ctx, layout)
        if self._tab == _BRIEFS and reads.briefs is not None:
            briefs = reads.briefs
            self._set(
                f"screen-{TABS[_BRIEFS].key}",
                _unavailable("Briefs", briefs)
                if isinstance(briefs, CARD_ERRORS)
                else briefs_screen(briefs, ctx, self._data, width=self.size.width),
            )
        if self._tab == _SYSTEM and reads.system is not None:
            self._keep_system(reads.system)
            self._paint_system()
        if self._tab == _LOGS and reads.logs_asked:
            self.logs_view.receive(reads.logs)
        if self._tab == _SIGNALS and reads.rule_checks is not None:
            self._set(f"screen-{TABS[_SIGNALS].key}", self._signals_content(reads))
        if self._tab == _POSITIONS:
            self._paint_positions_tab(reads)
        if self._tab == _TAX and reads.tax is not None:
            self._paint_tax(reads.tax, reads.tax_pack, ctx)

    @property
    def positions_view(self) -> PositionsView:
        """Return the Positions screen."""
        return self._home.query_one(PositionsView)

    def _paint_positions_tab(self, reads: ConsoleReads) -> None:
        """Hand the Positions screen the positions and the recently closed ones of a read."""
        view = self._alive(PositionsView, f"#tab-{TABS[_POSITIONS].key}")
        if view is None:
            return
        positions, activity = reads.positions, reads.activity
        closed = () if isinstance(activity, CARD_ERRORS) else closed_items(activity)
        if isinstance(positions, CARD_ERRORS):
            view.receive(None, closed, reads.ctx, error=f"Positions not available: {positions}")
            return
        view.receive(positions, closed, reads.ctx)

    @property
    def _logs_panel_open(self) -> bool:
        return any(isinstance(screen, _LogsPanel) for screen in self.screen_stack)

    def _signals_content(self, reads: ConsoleReads) -> RenderableType:
        checks, positions = reads.rule_checks, reads.positions
        if checks is None:
            return Text("")
        if isinstance(checks, CARD_ERRORS):
            return _unavailable("Signals", checks)
        if isinstance(positions, CARD_ERRORS):
            return _unavailable("Signals", positions)
        return signals_screen(
            checks,
            reads.ctx,
            self._data,
            width=self.size.width,
            held={row.coin for row in positions},
        )

    def _release_changed(self, current: str | None) -> bool:
        """Return whether ``current`` differs from the release this console started on."""
        if current is None or current == _UNKNOWN_RELEASE:
            return False
        if self._started_release is None:
            self._started_release = current
        return current != self._started_release

    def _read_release(self) -> str | None:
        """Return the current release name, or ``None`` without a source or when it fails."""
        if self._release is None:
            return None
        try:
            return self._release()
        except Exception:
            return None

    def _paint_tax(
        self, screen: TaxScreen | TaxPending, pack: TaxPackStatus | None, ctx: RenderContext
    ) -> None:
        """Draw the Tax screen from one read of the tax service."""
        self._home.query_one(TaxTab).show(
            screen,
            profile=self._taxpayer_profile,
            pack=pack,
            tokens=self._data,
            tz=ctx.tz,
            money=ctx.shows_money,
            width=self.size.width,
            big_number=self.size.height >= _BIG_FROM_ROWS,
        )

    def _show(self, widget_id: str, content: RenderableType, *, shown: bool) -> None:
        """Set a widget's content and show it only when it has something to show."""
        self._set(widget_id, content)
        widget = self._alive(Widget, f"#{widget_id}")
        if widget is not None:
            widget.display = shown

    def _paint_portfolio_read(self, portfolio: PortfolioSummary | Failed) -> None:
        """Draw the total, the head, the curve, the meters and the books from a read."""
        if isinstance(portfolio, CARD_ERRORS):
            self._summary = None
            big = self._alive(BigNumber, "#portfolio-total")
            if big is not None:
                big.show(None, None)
            self._set("portfolio", _unavailable("Portfolio", portfolio))
            for part in ("portfolio-curve", "portfolio-meters", "portfolio-books"):
                self._show(part, Text(""), shown=False)
            return
        self._summary = portfolio
        self._paint_portfolio(curve=True)

    def _paint_portfolio(self, *, curve: bool = False) -> None:
        """Draw the portfolio from the last read: the live total and line when live.

        Args:
            curve: Also redraw the curve, the meters and the books (booked-only,
                so a tick leaves them alone).
        """
        summary = self._summary
        if summary is None:
            return
        ctx, layout = self._ctx, self._layout
        look = self._live_look()
        live = self._live_total(look) if ctx.shows_money else None
        figure = total_text(summary, ctx) if layout.big_total else None
        value = summary.total_value
        if live is not None and figure is not None:
            figure, value = fmt_level(live.total), live.total
        paused = live is not None and look is not None and look.paused
        big = self._alive(BigNumber, "#portfolio-total")
        if big is not None:
            big.show(figure, float(value), stale=ctx.stale_since is not None or paused)
        head = render_portfolio_head(summary, ctx, total=figure is None, charts=layout.sparklines)
        if live is not None and look is not None:
            lines = live_portfolio_lines(summary, live, look, total_shown=figure is not None)
            self._set("portfolio", Group(*lines, head))
        else:
            self._set("portfolio", head)
        if not curve:
            return
        width = self._inner("portfolio")
        data = self._data
        chart = portfolio_curve(summary, ctx, data, width=width, rows=layout.curve_rows)
        self._show("portfolio-curve", chart, shown=bool(chart.plain))
        meters = portfolio_meters(
            summary, ctx, data, width=width, cap=self._exposure_cap, cash=not layout.sparklines
        )
        self._show("portfolio-meters", meters, shown=bool(meters.renderables))
        books = render_portfolio_books(summary, ctx)
        self._show("portfolio-books", books, shown=bool(books.renderables))

    def _watch_facts(
        self, watch: tuple[WatchedPair, ...] | Failed
    ) -> tuple[tuple[str, ...], datetime | None]:
        """Return the coins hermes checks and when it next looks, for the empty state."""
        pairs = () if isinstance(watch, CARD_ERRORS) else watch
        coins = tuple(dict.fromkeys(pair.coin for pair in pairs))
        if pairs:
            return coins, min(pair.next_check_at for pair in pairs)
        heartbeat = self._heartbeat
        if heartbeat is not None and heartbeat.next_check_at > heartbeat.last_check_at:
            return coins, heartbeat.next_check_at
        return coins, None

    def _paint_positions_read(self, reads: ConsoleReads) -> None:
        """Draw the positions and keep their details from a read."""
        self._empty_facts = None
        rows = reads.positions
        if isinstance(rows, CARD_ERRORS):
            self._selected = None
            self._rows, self._details = (), {}
            self._set("positions", _unavailable("Positions", rows))
            return
        coins = [row.coin for row in rows]
        if self._selected not in coins:
            self._selected = coins[0] if coins else None
        self._rows = rows
        details = reads.details
        self._details = {} if not rows or isinstance(details, CARD_ERRORS) else dict(details)
        if not rows:
            self._empty_facts = self._watch_facts(reads.watch)
        self._paint_positions()

    def _paint_positions(self) -> None:
        """Draw the position rows from the last read, live when there are live prices."""
        if self._empty_facts is not None:
            facts = self._empty_facts
            self._set("positions", empty_positions(*facts, self._ctx, self._data, self._radar))
            return
        if not self._rows:
            return
        look = self._live_look()
        live = None
        if look is not None:
            live = LiveRows(
                look=look,
                positions=self._live_positions(look),
                ticks={coin: quote.history for coin, quote in self._quotes().items()},
            )
        self._set(
            "positions",
            position_rows(
                self._rows,
                self._details,
                self._ctx,
                self._data,
                width=self._inner("positions"),
                selected=self._selected,
                live=live,
            ),
        )

    def _quotes(self) -> dict[str, LiveQuote]:
        board = self._board
        return {coin: quote for coin in board.prices() if (quote := board.quote(coin)) is not None}

    def _paint_market_read(self, watch: tuple[WatchedPair, ...] | Failed) -> None:
        """Draw the Market rows from a read of the market watch."""
        if isinstance(watch, CARD_ERRORS):
            self._watch = None
            self._set("market", _unavailable("Market watch", watch))
            return
        self._watch = watch
        self._paint_market()

    def _paint_market(self) -> None:
        """Draw the Market rows: live prices with a ticker, else the checks only."""
        watch = self._watch
        if watch is None:
            return
        look = self._live_look()
        ctx = self._ctx
        if look is None or not ctx.shows_money:
            self._set("market", render_market_watch(watch, ctx, title=False))
            return
        self._set(
            "market",
            market_rows(
                watch,
                self._quotes(),
                look,
                width=self._inner("market"),
                rate=eur_rate(self._rows, self._summary),
            ),
        )

    def _paint_run_line(self, reads: ConsoleReads) -> None:
        """Draw the bottom 90-day line from the same day 0 as tab 5 (one source)."""
        screen = reads.shadow
        if isinstance(screen, SHADOW_ERRORS):
            self._set("shadow", _unavailable("Shadow test", screen))
            return
        line = counted_line(
            run_words(screen),
            None if screen.day is None else min(screen.day, screen.length_days),
            screen.length_days,
            tokens_for(self.theme),
            width=self.size.width,
        )
        self._set("shadow", line)

    def shadow_screen(self) -> ShadowScreen:
        """Return the Shadow test screen read now from the shadow sources.

        Raises:
            LedgerError: The ledger could not be read.
            ShadowRecordError: A day-0 record could not be read.
        """
        return read_shadow(self._shadow, self._ctx)

    def _paint_shadow(
        self, screen: ShadowScreen | ShadowFailed, ctx: RenderContext, layout: HomeLayout
    ) -> None:
        """Draw tab 5: the hero, the checks, the day-0 record, the settings, the notebook."""
        big = self._home.query_one("#shadow-day", BigNumber)
        body = self._home.query_one("#shadow-body")
        for name in _WIDTH_CLASSES:
            body.set_class(name in layout.classes, name)
        if isinstance(screen, SHADOW_ERRORS):
            big.show(None, None)
            self._set("shadow-hero", _unavailable("Shadow test", screen))
            for part in _SHADOW_PARTS:
                self._set(part, Text(""))
            return
        tokens = tokens_for(self.theme)
        figure = day_figure(screen) if layout.big_total else None
        day = screen.day
        big.show(figure, None if day is None else float(min(day, screen.length_days)))
        width = self.size.width - _SCROLLBAR_CELLS - _PANEL_PADDING
        self._set(
            "shadow-hero",
            hero_text(screen, tokens, width=width, tz=ctx.tz, big=figure is not None),
        )
        self._set("shadow-checks", checks_table(screen.checks, tokens))
        self._set("shadow-record", record_text(screen, tokens, tz=ctx.tz))
        self._set("shadow-settings", settings_text(screen.settings, tokens))
        rows = notebook_rows(layout.height_class)
        self._set("shadow-notebook", notebook_text(screen.notebook, tokens, tz=ctx.tz, rows=rows))

    def _paint_third_column(self, reads: ConsoleReads, layout: HomeLayout) -> None:
        """Draw the Signals placeholder and the log tail (ultra width)."""
        self._set("signals", Text(_SIGNALS_PENDING, style="dim"))
        demo = self._model.demo_scenario is not None
        self._set("log-tail", log_tail(reads.log_tail, reads.ctx, demo=demo, count=layout.log_rows))

    def _demo_tag_style(self) -> str:
        tokens = tokens_for(self.theme)
        return f"bold {tokens.void} on {tokens.warn}"

    def _redraw_chrome(self) -> None:
        compact = self.size.width < COMPACT_BELOW
        tokens = tokens_for(self.theme)
        if self._tab == _LOGS:
            chips = _LOGS_FOOTER_CHIPS_COMPACT if compact else _LOGS_FOOTER_CHIPS
        else:
            chips = _FOOTER_CHIPS_COMPACT if compact else _FOOTER_CHIPS
        self._set("footer", key_chips(chips, tokens))
        tabs = self._alive(TabBar)
        if tabs is not None:
            tabs.compact = compact
            tabs.active = self._tab
        on_home = self._tab == _HOME
        positions = self._alive(HudPanel, "#positions-panel")
        if positions is not None:
            positions.active = on_home and not self._detail_open
        detail = self._alive(HudPanel, "#detail-panel")
        if detail is not None:
            detail.active = on_home and self._detail_open

    def _detail_content(self, coin: str | None) -> RenderableType:
        """Return one position's detail from the last read, with its live line when live.

        Args:
            coin: The position's coin; ``None`` draws the empty detail.
        """
        ctx = self._ctx
        details = None if self._reads is None else self._reads.details
        booked: RenderableType
        if isinstance(details, CARD_ERRORS):
            booked = _unavailable("Position", details)
        else:
            booked = render_position_detail((details or {}).get(coin or ""), ctx)
        look = self._live_look()
        live = self._live_positions(look).get(coin or "")
        if look is None or live is None or not ctx.shows_money:
            return booked
        return Group(live_detail_line(live, look), Text(""), booked)

    def _paint_detail(self) -> None:
        shown = self._detail_open and self.is_wide and self._selected is not None
        panel = self._alive(DetailPane, "#detail-panel")
        if panel is not None:
            panel.is_open = shown
        if shown:
            self._set("detail", self._detail_content(self._selected))

    # ------------------------------------------------------------------
    # System (view only: nothing here runs a command)
    # ------------------------------------------------------------------

    def _keep_system(self, reads: SystemReads) -> None:
        """Keep the System screen's last reads for its cursor and detail screens."""
        self._services = reads.services
        self._releases = reads.releases
        self._machine = reads.machine
        self._bar_seconds = reads.bar_seconds

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
        return None if self._summary is None else self._summary.eur_rate_date

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

    @property
    def logs_view(self) -> LogsView:
        """Return the Logs screen."""
        return self._home.query_one(LogsView)

    def _switch_to(self, index: int) -> None:
        self._tab = index
        self._home.query_one("#screens", ContentSwitcher).current = f"tab-{TABS[index].key}"
        if index == _POSITIONS:
            self.call_after_refresh(self.positions_view.table.focus)
        if self._background_reads:
            # The tab shows its last reads at once; the fresh ones follow.
            self._paint_tab_screen(self._reads, self._layout)
        if index == _LOGS:
            self.logs_view.open()
            self.logs_view.table.focus()
        else:
            self.set_focus(None)
        self._redraw()

    def _repaint_positions(self) -> None:
        """Repaint the cursor, the detail and the panel borders from the last reads."""
        self._redraw_chrome()
        self._paint_positions()
        self._paint_detail()

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
        focused = self.focused
        if step > 0 and isinstance(focused, QueryInput):
            focused.action_complete()
            return
        self.action_show_tab((self._tab + step) % len(TABS))

    def action_tax_subtab(self, step: int) -> None:
        """Show the Tax screen's next (``1``) or previous (``-1``) sub-tab.

        Args:
            step: Direction.
        """
        if self._on_home and self._tab == _TAX:
            self._home.query_one(TaxTab).switch(step)

    def action_tax_show_profile(self) -> None:
        """``S`` on Your profile: show or mask the taxpayer figures."""
        if not (self._on_home and self._tab == _TAX):
            return
        tax = self._home.query_one(TaxTab)
        if tax.sub == PROFILE:
            tax.toggle_profile()
            self._redraw()

    def action_tax_edit_profile(self) -> None:
        """``E`` on Your profile: say that editing waits for MVP2."""
        if self._on_home and self._tab == _TAX and self._home.query_one(TaxTab).sub == PROFILE:
            self.notify(EDIT_LATER, timeout=_NOTICE_SECONDS)

    def action_go_back(self) -> None:
        """Close the detail pane, else return to the previous tab."""
        if not self._on_home:
            return
        if self._detail_open and self._tab == _HOME:
            self._detail_open = False
            self._repaint_positions()
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
        self._repaint_positions()

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
        self._repaint_positions()

    def action_open_detail(self) -> None:
        """Open the selected position: a pane beside the list, else a detail screen.

        On System it opens the selected service or release instead.
        """
        if self._on_home and self._tab == _SYSTEM:
            self._open_system_detail()
            return
        selected = self._selected
        if not self._home_shown or selected is None:
            return
        if detail_mode(self.size.width) is DetailMode.PANE:
            self._detail_open = True
            self._repaint_positions()
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

    def action_toggle_quiet(self) -> None:
        """Show or fold the one-line 4h checks in the activity card."""
        if not self._on_home:
            return
        self._show_quiet = not self._show_quiet
        self._redraw()

    def action_logs(self) -> None:
        """Show the last journal lines, as ``hermes logs`` prints them."""
        if self._on_home:
            self.push_screen(_LogsPanel(self._logs_content))
            self._redraw()

    def _logs_content(self) -> RenderableType:
        if self._model.demo_scenario is not None:
            return Text(LOGS_DEMO, style="dim")
        reads = self._reads
        if reads is None or not reads.logs_asked:
            return Text(_LOGS_READING, style="dim")
        lines = reads.logs
        if lines is None:
            return Text(LOGS_UNAVAILABLE, style="dim")
        if not lines:
            return Text("No journal lines yet.", style="dim")
        tz = reads.ctx.tz
        return Group(
            Text("Logs", style="bold"),
            *(render_log_line(line, tz) for line in lines[-_LOGS_LINES:]),
        )

    def action_restart(self) -> None:
        """Quit so the caller can re-exec onto the new release; ignored without the notice."""
        if self._on_home and self._release_is_new:
            self.restart_requested = True
            self.exit()

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
            Text("  A show or fold the 4h check lines in Activity"),
            Text("  L logs · T theme · ? this help · Q quit"),
            Text("  R restart onto a new version (only while its notice shows)"),
            Text("  7 System is view only: each VPS action is a command to copy; no key runs one."),
            Text("  Logs: / query (Tab completes) · Enter line · T trace · F follow"),
            Text("        C copy · E export to the PC · D W M Y histogram window"),
            Text(
                "This screen is read-only: nothing here changes a position, a stop or the ledger."
            ),
            Text(""),
            Text("Tax", style="bold"),
            self._tax_footnote(),
            Text(""),
            Text("Prices and money are shown in EUR.", style="dim"),
        ]
        return Group(*keys)

    def _tax_footnote(self) -> RenderableType:
        portfolio = None if self._reads is None else self._reads.portfolio
        if portfolio is None:
            return Text("")
        if isinstance(portfolio, CARD_ERRORS):
            return _unavailable("Tax", portfolio)
        return render_tax_footnote(portfolio)


def run_home(
    model: ConsoleReadModel,
    journal: Callable[[int], Sequence[LogLine] | None] | None = None,
    release: Callable[[], str] | None = None,
    *,
    version: str | None = None,
    allow_mainnet: bool | None = None,
    exec_fn: Callable[[str, list[str]], object] = os.execv,
    argv: Sequence[str] | None = None,
    platform: str = sys.platform,
    theme: ThemeChoice | None = None,
    settings_path: Path | None = DEFAULT_SETTINGS_PATH,
    exposure_cap: Decimal | None = None,
    shadow: ShadowSources | None = None,
    ticker: LiveTickerPort | None = None,
    tax: TaxScreenSource | None = None,
    taxpayer_profile: TaxpayerProfileView | None = None,
    tax_pack: Callable[[int], TaxPackStatus | None] | None = None,
    system: SystemSources | None = None,
    exports: Path | None = None,
) -> None:
    """Run the console until the operator quits, or restarts onto a new release.

    Args:
        model: The read model to show.
        journal: Source of the logs panel's lines; see :class:`HomeApp`.
        release: Source of the running release's name; see :class:`HomeApp`.
        version: The package version in its release form; see :class:`HomeApp`.
        allow_mainnet: The config's ``allow_mainnet``, ``None`` when unread;
            see :class:`HomeApp`.
        exec_fn: ``os.execv``-shaped replacement of the process; injectable for tests.
        argv: The command line to restart with, after the interpreter; defaults to
            the interpreter's own ``sys.orig_argv``, so ``python -m hermes`` restarts the same way.
        platform: ``sys.platform``; where exec is not meaningful (Windows) the
            console exits with a message instead.
        theme: A ``--theme`` choice; saved, and wins over the saved one.
        settings_path: Where the theme choice is read and saved.
        exposure_cap: The roster's exposure cap; see :class:`HomeApp`.
        shadow: The Shadow test screen's sources; see :class:`HomeApp`.
        ticker: The live public ticker; see :class:`HomeApp`.
        tax: The Tax screen's source; see :class:`HomeApp`.
        taxpayer_profile: What the Tax screen may say about the profile.
        tax_pack: Returns the year's tax pack state.
        system: The System screen's read-only sources; see :class:`HomeApp`.
        exports: Where the Logs screen exports a line; ``None`` over ``--vps``.
    """
    app = HomeApp(
        model,
        background_reads=True,
        journal=journal,
        release=release,
        version=version,
        allow_mainnet=allow_mainnet,
        theme=theme,
        settings_path=settings_path,
        exposure_cap=exposure_cap,
        shadow=shadow,
        ticker=ticker,
        tax=tax,
        taxpayer_profile=taxpayer_profile,
        tax_pack=tax_pack,
        system=system,
        exports=exports,
        boot=True,
    )
    app.run()
    if not app.restart_requested:
        return
    if platform == "win32":
        sys.stdout.write("New version deployed: start hermes again to use it.\n")
        return
    exec_fn(sys.executable, [sys.executable, *(sys.orig_argv[1:] if argv is None else argv)])
