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

Screens: ``1`` Home (issue #712); ``2``-``8`` name the issue that builds them next.

Home layout, per breakpoint token (:func:`~hermes.entrypoints.tui_home.home_layout`):
under 100 columns one column (Portfolio, Positions, Market, Activity, Engine
brief) with sparklines for charts, and the position detail opens as its own
screen; from 100 two columns (Portfolio left; Positions, the detail, Market,
Activity and the Engine brief right); from 160 the mock layout, Market beside
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

Keys: ``1``-``8`` screens, Tab / Shift+Tab cycle them, Ctrl+P command palette,
``D`` ``W`` ``M`` ``Y`` range (day, week, month, year), up and down (or ``j``
``k``) select a position, Enter opens it, Esc goes back, ``L`` logs, ``T``
theme, ``?`` help, ``Q`` quit.

Reference: openspec/changes/readonly-tui-dashboard/change.md;
openspec/changes/live-home-screen/change.md (D-6);
openspec/changes/711-cockpit-theme/change.md;
openspec/changes/712-cockpit-home/change.md;
openspec/changes/713-live-ticker/change.md.
"""

from __future__ import annotations

import os
from collections import deque
from collections.abc import Callable, Iterable, Sequence
from contextlib import suppress
from dataclasses import replace
from datetime import UTC, datetime, timedelta, tzinfo
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
    ViewNotAvailableError,
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
from hermes.domain.errors import LedgerError
from hermes.entrypoints.tui_briefs import briefs_screen
from hermes.entrypoints.tui_home import (
    LOGS_DEMO,
    LOGS_UNAVAILABLE,
    HomeLayout,
    LiveRows,
    empty_positions,
    home_layout,
    log_tail,
    portfolio_curve,
    portfolio_meters,
    position_rows,
    run_line,
    total_text,
)
from hermes.entrypoints.tui_live import (
    LiveLook,
    eur_rate,
    live_detail_line,
    live_portfolio_lines,
    market_rows,
)
from hermes.entrypoints.tui_notices import bar_checked_notice, beep_enabled, should_beep
from hermes.entrypoints.tui_settings import (
    DEFAULT_SETTINGS_PATH,
    ConsoleSettings,
    load_settings,
    save_settings,
)
from hermes.entrypoints.tui_signals import signals_screen
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
_LOGS_LINES: Final = 50
_READ_ONLY: Final = "read-only"
_BRIEF_PENDING: Final = "brief not available yet"
_SIGNALS_PENDING: Final = "Heat grid · coming next · issue #714"
_LEFT_IDS: Final = frozenset(
    {"portfolio", "portfolio-curve", "portfolio-meters", "portfolio-books"}
)
_THIRD_IDS: Final = frozenset({"signals", "log-tail"})
_FULL_WIDTH_IDS: Final = frozenset(
    {"shadow", "footer", "footer-status", "countdown", "countdown-meter", "mainnet"}
)
_WIDTH_CLASSES: Final = ("-compact", "-standard", "-wide", "-ultra")
_HEIGHT_CLASSES: Final = ("-short", "-tall", "-towering")
# Spaces the status row's widgets add around their text (padding, margins).
_STATUS_ROW_SLACK: Final = 8
# The countdown meter: a space, the end caps and the cells (issue #833).
_COUNTDOWN_METER_CELLS: Final = METER_CELLS + 3
# The margins the theme chip and the mainnet badge keep on the status row.
_CHIP_MARGIN: Final = 1
_MAINNET_MARGIN: Final = 1

TABS: Final = (
    Tab("1", "Home"),
    Tab("2", "Signals"),
    Tab("3", "Positions", 715),
    Tab("4", "Tax", 719),
    Tab("5", "Shadow test", 718),
    Tab("6", "Briefs"),
    Tab("7", "System", 717),
    Tab("8", "Logs", 716),
)
_HOME: Final = 0
_SIGNALS: Final = 1
_BRIEFS: Final = 5
_LOGS: Final = 7
_NOTICE_SECONDS: Final = 6

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
    #detail-panel {{ display: none; }}
    #detail-panel.-open {{ display: block; }}
    #shadow, #footer-row, #panel-footer {{ height: 1; }}
    #footer {{ width: 1fr; }}
    #footer-status, #panel-footer {{ width: auto; color: $hud-muted; }}
    #panel {{ height: 1fr; padding: 0 1; overflow-y: auto; }}
    """
    BINDINGS: ClassVar[list[BindingType]] = [
        *_range_bindings(),
        *_tab_bindings(),
        Binding("tab", "cycle_tab(1)", "next screen", show=False, priority=True),
        Binding("shift+tab", "cycle_tab(-1)", "previous screen", show=False, priority=True),
        Binding("up,k", "select(-1)", "previous", show=False),
        Binding("down,j", "select(1)", "next", show=False),
        Binding("enter", "open_detail", "detail", show=False),
        Binding("escape", "go_back", "back", show=False),
        Binding("a,A", "toggle_quiet", "checks", show=False),
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
        version: str | None = None,
        allow_mainnet: bool | None = None,
        clock: Callable[[], datetime] = _system_time,
        motion: bool | None = None,
        settings_path: Path | None = None,
        theme: ThemeChoice | None = None,
        exposure_cap: Decimal | None = None,
        ticker: LiveTickerPort | None = None,
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
                (``v1.0.0-shadow.1``, issue #815), shown before the release;
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
            ticker: The live public ticker (issue #713); ``None`` shows booked
                values only and the Market panel without prices.
        """
        super().__init__()
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
        self._heartbeat: Heartbeat | None = None
        # The last heartbeat read, kept across a failed read so the bell never rings twice.
        self._last_read: Heartbeat | None = None
        self._ctx = RenderContext(surface=Surface.OPERATOR_TUI, now=clock())
        self._pulse = 0
        self._radar = 0
        self._exposure_cap = exposure_cap
        self._layout: HomeLayout = home_layout(80, 24)
        self._empty_facts: tuple[tuple[str, ...], datetime | None] | None = None
        self._title_sent: str | None = None
        self._title_total: str | None = None
        self.effect_timers: dict[str, Timer] = {}
        self._poller = None if ticker is None else TickerPoller(ticker, clock)
        self._board = LiveBoard()
        self._fetching = False
        self._flashes: dict[str, int] = {}
        self._flash_timer: Timer | None = None
        self._polls: deque[bool] = deque(maxlen=FEED_WIDTH)
        self._feed_timer: Timer | None = None
        self._live_state = LiveState.WAITING
        self._summary: PortfolioSummary | None = None
        self._rows: tuple[PositionRow, ...] = ()
        self._details: dict[str, PositionDetail] = {}
        self._watch: tuple[WatchedPair, ...] | None = None
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
            yield Static(id="mainnet")
            yield Static(id="demo-tag")
            yield Static(id="release")
            yield Static(id="heartbeat")
            yield Static(id="countdown")
            yield Static(id="countdown-meter")
            yield ThemeChip(id="theme-chip")
        yield Static(id="down-row")
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
                    yield HudPanel(
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
            for index, tab in enumerate(TABS[1:], start=1):
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
            big = self._home.query_one(BigNumber)
            self.effect_timers["shimmer"] = self.set_interval(MOTION.shimmer, big.start_shimmer)
            self.effect_timers["radar"] = self.set_interval(MOTION.radar_tick, self._step_radar)

    def _step_radar(self) -> None:
        """Turn the empty-positions radar one step (only while there is no position)."""
        self._radar += 1
        if self._empty_facts is not None and self.screen_stack:
            coins, next_at = self._empty_facts
            with suppress(NoMatches):
                self._set(
                    "positions",
                    empty_positions(coins, next_at, self._ctx, self._data, self._radar),
                )

    # An effect timer can fire while the app is tearing its widgets down; a
    # missing widget then means there is nothing left to animate.
    def _step_pulse(self) -> None:
        self._pulse = (self._pulse + 1) % MOTION.pulse_steps
        with suppress(NoMatches):
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
        bar = self._home.query_one(WindowBar)
        bar.clock = now.astimezone(self._ctx.tz).strftime("%H:%M:%S")
        if not self._polls:
            bar.show_next_bar(next_bar_text(self._heartbeat, now))
        self._paint_mainnet()
        self._fit_release()
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
        self._home.query_one(WindowBar).show_mainnet(allow_mainnet)
        badge = mainnet_text(allow_mainnet, tokens_for(self.theme))
        self._set("mainnet", Text("") if neon else badge)
        self._home.query_one("#mainnet").display = not neon

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
        self._home.query_one(WindowBar).display = name == NEON_THEME
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
        with suppress(NoMatches):
            self._home.query_one(WindowBar).show_feed(self._polls, bright=bright)
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
            self._set("detail", self._detail_content(self._ctx, cached=True))

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
        except _CARD_ERRORS:
            return RenderContext(
                surface=Surface.OPERATOR_TUI, now=datetime.now(UTC), width=self._inner()
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

    def _announce(self, previous: Heartbeat | None, current: Heartbeat, ctx: RenderContext) -> None:
        """Toast a newly checked bar; ring the bell on turning late or down when asked to."""
        notice = bar_checked_notice(previous, current, ctx.tz)
        if notice is not None:
            self.notify(notice, timeout=_NOTICE_SECONDS)
        if beep_enabled(os.environ) and should_beep(previous, current):
            self.bell()

    def _redraw(self) -> None:
        if not self.is_running or not self.screen_stack:
            return
        model = self._model
        ctx = self._render_context()
        self._ctx = ctx
        scenario = model.demo_scenario
        # Drawn by _fit_release once the heartbeat and the badge are known.
        self._demo_tag = (
            Text(f" {DEMO_TAG} · scenario {scenario} ", style=self._demo_tag_style())
            if scenario
            else Text("")
        )
        self._release_name = None if self._release is None else self._release()
        try:
            self._heartbeat = model.heartbeat()
        except _CARD_ERRORS as exc:
            self._heartbeat = None
            self._set("heartbeat", _unavailable("Heartbeat", exc))
            self._set("down-row", Text(""))
            self._home.query_one("#down-row").set_class(False, "-down")
        else:
            self._announce(self._last_read, self._heartbeat, ctx)
            self._last_read = self._heartbeat
            self._paint_heartbeat()
        self._paint_mainnet()
        self._fit_release()

        try:
            self._title_total = title_total(model.portfolio(self._range).total_value, ctx.surface)
        except _CARD_ERRORS:
            self._title_total = None

        layout = home_layout(self.size.width, self.size.height)
        self._layout = layout
        body = self._home.query_one("#body")
        for name in (*_WIDTH_CLASSES, *_HEIGHT_CLASSES):
            body.set_class(name in layout.classes, name)
        self._redraw_portfolio()
        self._redraw_positions()
        self._redraw_market()
        self._set(
            "activity",
            _card(
                "Activity",
                lambda: render_activity(
                    model.activity(include_quiet=self._show_quiet),
                    ctx,
                    limit=layout.activity_rows,
                    icons=True,
                ),
            ),
        )
        self._set("brief", Text(_BRIEF_PENDING, style="dim"))
        if layout.third:
            self._redraw_third_column(ctx, layout)
        self._set(
            "shadow",
            _card(
                "Shadow test",
                lambda: run_line(
                    model.shadow_test(), tokens_for(self.theme), width=self.size.width
                ),
            ),
        )
        self._redraw_detail(ctx)
        self._redraw_chrome()
        if self._tab == _BRIEFS:
            self._set(
                f"screen-{TABS[_BRIEFS].key}",
                _card(
                    "Briefs",
                    lambda: briefs_screen(model.briefs(), ctx, self._data, width=self.size.width),
                ),
            )
        if self._tab == _LOGS:
            logs = TABS[_LOGS]
            self._set(
                f"screen-{logs.key}", Group(_placeholder(logs), Text(""), self._logs_content())
            )
        if self._tab == _SIGNALS:
            self._set(
                f"screen-{TABS[_SIGNALS].key}",
                _card(
                    "Signals",
                    lambda: signals_screen(
                        model.rule_checks(),
                        ctx,
                        self._data,
                        width=self.size.width,
                        held={row.coin for row in model.positions()},
                    ),
                ),
            )
        screen = self.screen
        if isinstance(screen, _PanelScreen):
            screen.refresh_panel()

    def _show(self, widget_id: str, content: RenderableType, *, shown: bool) -> None:
        """Set a widget's content and show it only when it has something to show."""
        self._set(widget_id, content)
        self._home.query_one(f"#{widget_id}").display = shown

    def _redraw_portfolio(self) -> None:
        """Read the portfolio, then draw the total, the head, the curve, the meters, the books."""
        try:
            self._summary = self._model.portfolio(self._range)
        except _CARD_ERRORS as exc:
            self._summary = None
            self._home.query_one("#portfolio-total", BigNumber).show(None, None)
            self._set("portfolio", _unavailable("Portfolio", exc))
            for part in ("portfolio-curve", "portfolio-meters", "portfolio-books"):
                self._show(part, Text(""), shown=False)
            return
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
        big = self._home.query_one("#portfolio-total", BigNumber)
        look = self._live_look()
        live = self._live_total(look) if ctx.shows_money else None
        figure = total_text(summary, ctx) if layout.big_total else None
        value = summary.total_value
        if live is not None and figure is not None:
            figure, value = fmt_level(live.total), live.total
        paused = live is not None and look is not None and look.paused
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

    def _watch_facts(self) -> tuple[tuple[str, ...], datetime | None]:
        """Return the coins hermes checks and when it next looks, for the empty state."""
        try:
            watch = self._model.market_watch()
        except _CARD_ERRORS:
            watch = ()
        coins = tuple(dict.fromkeys(pair.coin for pair in watch))
        if watch:
            return coins, min(pair.next_check_at for pair in watch)
        heartbeat = self._heartbeat
        if heartbeat is not None and heartbeat.next_check_at > heartbeat.last_check_at:
            return coins, heartbeat.next_check_at
        return coins, None

    def _redraw_positions(self) -> None:
        """Read the positions and their details, then draw them."""
        self._empty_facts = None
        try:
            rows = tuple(self._model.positions())
        except _CARD_ERRORS as exc:
            self._selected = None
            self._rows, self._details = (), {}
            self._set("positions", _unavailable("Positions", exc))
            return
        coins = [row.coin for row in rows]
        if self._selected not in coins:
            self._selected = coins[0] if coins else None
        self._rows = rows
        self._details = {}
        if not rows:
            self._empty_facts = self._watch_facts()
        else:
            try:
                details = {row.coin: self._model.position(row.coin) for row in rows}
            except _CARD_ERRORS:
                details = {}
            self._details = {coin: d for coin, d in details.items() if d is not None}
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

    def _redraw_market(self) -> None:
        """Read the market watch, then draw the Market rows."""
        try:
            self._watch = tuple(self._model.market_watch())
        except _CARD_ERRORS as exc:
            self._watch = None
            self._set("market", _unavailable("Market watch", exc))
            return
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

    def _redraw_third_column(self, ctx: RenderContext, layout: HomeLayout) -> None:
        """Draw the Signals placeholder and the log tail (ultra width)."""
        self._set("signals", Text(_SIGNALS_PENDING, style="dim"))
        demo = self._model.demo_scenario is not None
        lines = None if demo or self._journal is None else self._journal(layout.log_rows)
        self._set("log-tail", log_tail(lines, ctx, demo=demo, count=layout.log_rows))

    def _demo_tag_style(self) -> str:
        tokens = tokens_for(self.theme)
        return f"bold {tokens.void} on {tokens.warn}"

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

    def _detail_content(
        self, ctx: RenderContext | None = None, *, cached: bool = False
    ) -> RenderableType:
        """Return the selected position's detail, with its live line when live.

        Args:
            ctx: Render context; a fresh one when ``None``.
            cached: Use the detail read on the last poll (a tick or a pulse
                repaints without reading the ledger).
        """
        ctx = ctx or self._render_context()
        selected = self._selected
        model = self._model
        booked: RenderableType
        if cached:
            booked = render_position_detail(self._details.get(selected or ""), ctx)
        else:
            booked = _card(
                "Position",
                lambda: render_position_detail(model.position(selected) if selected else None, ctx),
            )
        look = self._live_look()
        live = self._live_positions(look).get(selected or "")
        if look is None or live is None or not ctx.shows_money:
            return booked
        return Group(live_detail_line(live, look), Text(""), booked)

    def _redraw_detail(self, ctx: RenderContext) -> None:
        shown = self._detail_open and self.is_wide and self._selected is not None
        self._home.query_one("#detail-panel").set_class(shown, "-open")
        if shown:
            self._set("detail", self._detail_content(ctx))

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
        """Move the position cursor, wrapping around.

        Args:
            step: ``-1`` for the previous position, ``1`` for the next.
        """
        coins = self._positions_coins()
        if not self._home_shown or not coins:
            return
        current = coins.index(self._selected) if self._selected in coins else 0
        self._selected = coins[(current + step) % len(coins)]
        self._redraw()

    def action_open_detail(self) -> None:
        """Open the selected position: beside the list when wide, else full screen."""
        if not self._home_shown or self._selected is None:
            return
        if self.is_wide:
            self._detail_open = True
            self._redraw()
            return
        self.push_screen(_PanelScreen(self._detail_content))

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
            self.push_screen(_PanelScreen(self._logs_content))

    def _logs_content(self) -> RenderableType:
        if self._model.demo_scenario is not None:
            return Text(LOGS_DEMO, style="dim")
        lines = None if self._journal is None else self._journal(_LOGS_LINES)
        if lines is None:
            return Text(LOGS_UNAVAILABLE, style="dim")
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
            Text("  ↑ ↓ choose a position · Enter open its detail · Esc back"),
            Text("  A show or fold the 4h check lines in Activity"),
            Text("  L logs · T theme · ? this help · Q quit"),
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
    version: str | None = None,
    allow_mainnet: bool | None = None,
    theme: ThemeChoice | None = None,
    settings_path: Path | None = DEFAULT_SETTINGS_PATH,
    exposure_cap: Decimal | None = None,
    ticker: LiveTickerPort | None = None,
) -> None:
    """Run the console until the operator quits.

    Args:
        model: The read model to show.
        journal: Source of the logs panel's lines; see :class:`HomeApp`.
        release: Source of the running release's name; see :class:`HomeApp`.
        version: The package version in its release form; see :class:`HomeApp`.
        allow_mainnet: The config's ``allow_mainnet``, ``None`` when unread;
            see :class:`HomeApp`.
        theme: A ``--theme`` choice; saved, and wins over the saved one.
        settings_path: Where the theme choice is read and saved.
        exposure_cap: The roster's exposure cap; see :class:`HomeApp`.
        ticker: The live public ticker; see :class:`HomeApp`.
    """
    HomeApp(
        model,
        journal=journal,
        release=release,
        version=version,
        allow_mainnet=allow_mainnet,
        theme=theme,
        settings_path=settings_path,
        exposure_cap=exposure_cap,
        ticker=ticker,
    ).run()
