"""``hermes tui``: the read-only operator home screen.

A Textual app over a :class:`~hermes.application.console.read_model.ConsoleReadModel`.
It embeds the same Rich renderers the static ``hermes`` home screen prints, so
the two can never disagree. The app never writes: it only calls the read
methods of the read model, so opening it while ``hermes serve`` runs neither
blocks nor touches the ledger.

Layout: below 120 columns everything stacks (portfolio, positions, market
watch, activity; the position detail opens as its own screen). From 120
columns the portfolio and activity sit left and the positions (with the market
watch under them) and detail right. The portfolio total
is drawn three rows tall on terminals 30 rows or higher.

Every card is drawn on its own: a view the read model cannot serve yet (or a
ledger read that fails) turns that card into a plain "not available yet" (or
"could not read the ledger") line and leaves the others standing, so the live
screen never stops on a missing figure (change ``live-home-screen`` D-6).

The top line names the running release when the caller passes a release
source (``hermes tui`` reads the same ``current`` link as ``hermes status``,
issue #649).

When the release the console started on is no longer the current one, a
persistent notice offers ``R`` to restart onto the new release (issue #652).

Keys: ``D`` ``W`` ``M`` ``Y`` range (day, week, month, year), up and down
(or ``j`` ``k``) select a position, Enter opens it, Esc closes it, ``L`` logs,
``?`` help, ``R`` restart (only while the notice shows), ``Q`` quit.

Reference: openspec/changes/readonly-tui-dashboard/change.md;
openspec/changes/live-home-screen/change.md (D-6).
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from typing import ClassVar, Final

from rich.console import Group, RenderableType
from rich.terminal_theme import TerminalTheme
from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Container, Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Static

from hermes.application.console import (
    ConsoleReadModel,
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
from hermes.domain.errors import LedgerError

__all__ = ["REFRESH_SECONDS", "HomeApp", "run_home"]

REFRESH_SECONDS: Final = 5.0
_WIDE_FROM: Final = 120
_LEFT_COLUMN: Final = 46
_BIG_TOTAL_FROM_ROWS: Final = 30
_ACTIVITY_ROWS: Final = 4
_ACTIVITY_ROWS_SHORT: Final = 3
_NEW_VERSION_NOTICE: Final = "new version deployed · press R to restart"
_UNKNOWN_RELEASE: Final = "unknown"
_FOOTER: Final = "D W M Y range · ↵ detail · L logs · ? help · Q quit · read-only"
_LOGS_LINES: Final = 50
_LOGS_DEMO: Final = "Logs come from the VPS journal; demo data has none."
_LOGS_UNAVAILABLE: Final = "The journal is not readable here; run hermes logs on the VPS."

# Colour-blind friendly palette: the console's "red" reads orange and its
# "green" reads blue. Amber (yellow) stays amber for late and warning states.
_ORANGE: Final = (240, 120, 20)
_BLUE: Final = (70, 140, 230)
_BLUE_ORANGE: Final = TerminalTheme(
    (12, 12, 12),
    (217, 217, 217),
    [
        (26, 26, 26),
        _ORANGE,
        _BLUE,
        (230, 190, 40),
        (157, 101, 255),
        _ORANGE,
        (88, 209, 235),
        (196, 197, 181),
        (98, 94, 76),
    ],
    [
        _ORANGE,
        _BLUE,
        (255, 214, 90),
        (157, 101, 255),
        _ORANGE,
        (88, 209, 235),
        (246, 246, 239),
    ],
)

_CARD_ERRORS: Final = (ViewNotAvailableError, LedgerError)


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
        """Return to the home screen."""
        self.dismiss(None)


class HomeApp(App[None]):
    """The read-only home screen.

    Args:
        model: Where every figure comes from; only read methods are called.
        refresh_seconds: How often the screen re-reads the model.
    """

    TITLE = "hermes"
    CSS = """
    Screen { background: $surface; }
    #top { height: 1; }
    #demo-tag { width: auto; }
    #release { width: auto; color: $text-muted; }
    #heartbeat { width: 1fr; }
    #down-row { display: none; height: auto; }
    #down-row.-down { display: block; }
    #notice { display: none; height: 1; }
    #notice.-on { display: block; }
    #body {
        layout: grid;
        grid-size: 1;
        grid-gutter: 0 2;
        grid-rows: auto;
        height: 1fr;
        overflow-y: auto;
    }
    #body.-wide { grid-size: 2; grid-columns: 46 1fr; grid-gutter: 1 2; }
    .cell { height: auto; }
    #watch-column { height: auto; }
    #positions, #market { height: auto; }
    #detail { display: none; }
    #detail.-open { display: block; }
    #shadow, #footer, #panel-footer { height: 1; }
    #footer, #panel-footer { color: $text-muted; }
    #panel { height: 1fr; padding: 0 1; overflow-y: auto; }
    """
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("d,D", "range('today')", "today", show=False),
        Binding("w,W", "range('week')", "week", show=False),
        Binding("m,M", "range('month')", "month", show=False),
        Binding("y,Y", "range('year')", "year", show=False),
        Binding("up,k", "select(-1)", "previous", show=False),
        Binding("down,j", "select(1)", "next", show=False),
        Binding("enter", "open_detail", "detail", show=False),
        Binding("escape", "close_detail", "back", show=False),
        Binding("l,L", "logs", "logs", show=False),
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
    ) -> None:
        """Hold the read model, the journal source and the screen state.

        Args:
            model: The read model every panel draws from.
            refresh_seconds: Seconds between redraws.
            journal: Returns the last N journal lines, or ``None`` when the
                journal is not readable here; ``None`` means no journal at all.
            release: Returns the running release's name, re-read on every
                redraw; ``None`` leaves the top line without a release.
        """
        super().__init__()
        self._model = model
        self._journal = journal
        self._release = release
        self._refresh_seconds = refresh_seconds
        self._range = PortfolioRange.TODAY
        self._selected: str | None = None
        self._detail_open = False
        self._content: dict[str, RenderableType] = {}
        self._started_release: str | None = None
        self._release_is_new = False
        self.restart_requested = False

    def compose(self) -> ComposeResult:
        """Lay out the status bar, the four cells, the 90-day line and the footer."""
        with Horizontal(id="top"):
            yield Static(id="demo-tag")
            yield Static(id="release")
            yield Static(id="heartbeat")
        yield Static(id="down-row")
        yield Static(id="notice")
        with Container(id="body"):
            yield Static(id="portfolio", classes="cell")
            with Vertical(id="watch-column", classes="cell"):
                yield Static(id="positions")
                yield Static(id="market")
            yield Static(id="activity", classes="cell")
            yield Static(id="detail", classes="cell")
        yield Static(id="shadow")
        yield Static(_FOOTER, id="footer")

    def on_mount(self) -> None:
        """Apply the colour-blind palette, draw, and start the refresh timer."""
        self.ansi_theme_dark = _BLUE_ORANGE
        self._redraw()
        self.set_interval(self._refresh_seconds, self._redraw)

    def on_resize(self, event: events.Resize) -> None:
        """Re-flow when the terminal size changes."""
        del event
        self._redraw()

    # ------------------------------------------------------------------
    # Drawing
    # ------------------------------------------------------------------

    @property
    def is_wide(self) -> bool:
        """Return whether the terminal is wide enough for two columns."""
        return self.size.width >= _WIDE_FROM

    def plain_text(self, widget_id: str) -> str:
        """Return the text of one widget without styles (for tests and review).

        Args:
            widget_id: ``demo-tag``, ``release``, ``heartbeat``, ``down-row``, ``portfolio``,
                ``positions``, ``market``, ``activity``, ``detail`` or ``shadow``.

        Returns:
            The rendered text, one line per row.
        """
        content = self._content.get(widget_id, Text(""))
        return to_plain_text(content, width=max(self._panel_width(), 20))

    def _panel_width(self) -> int:
        width = self.size.width
        return width - _LEFT_COLUMN - 2 if self.is_wide else width

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

    def _redraw(self) -> None:
        if not self.is_running or not self.screen_stack:
            return
        model = self._model
        ctx = self._render_context()
        scenario = model.demo_scenario
        tag = (
            Text(f" {DEMO_TAG} · scenario {scenario} ", style="bold black on yellow")
            if scenario
            else Text("")
        )
        self._set("demo-tag", tag)
        name = self._read_release()
        self._set("release", Text("") if name is None else Text(f"release {name}"))
        self._release_is_new = self._release_changed()
        self._set("notice", Text(_NEW_VERSION_NOTICE) if self._release_is_new else Text(""))
        self._home.query_one("#notice").set_class(self._release_is_new, "-on")
        try:
            heartbeat = model.heartbeat()
        except _CARD_ERRORS as exc:
            self._set("heartbeat", _unavailable("Heartbeat", exc))
            self._set("down-row", Text(""))
            down = False
        else:
            down = heartbeat.state is HeartbeatState.DOWN
            self._set("heartbeat", Text("") if down else render_heartbeat(heartbeat, ctx))
            self._set("down-row", render_heartbeat(heartbeat, ctx) if down else Text(""))
        self._home.query_one("#down-row").set_class(down, "-down")

        coins = self._positions_coins()
        if self._selected not in coins:
            self._selected = coins[0] if coins else None
        body = self._home.query_one("#body")
        body.set_class(self.is_wide, "-wide")
        big = self.size.height >= _BIG_TOTAL_FROM_ROWS
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
                lambda: Group(Text(""), render_market_watch(model.market_watch(), ctx)),
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
        screen = self.screen
        if isinstance(screen, _PanelScreen):
            screen.refresh_panel()

    def _read_release(self) -> str | None:
        """Return the current release name, or ``None`` without a source or when it fails."""
        if self._release is None:
            return None
        try:
            return self._release()
        except Exception:
            return None

    def _release_changed(self) -> bool:
        """Return whether the current release differs from the one this console started on."""
        current = self._read_release()
        if current is None or current == _UNKNOWN_RELEASE:
            return False
        if self._started_release is None:
            self._started_release = current
        return current != self._started_release

    def _detail_content(self, ctx: RenderContext | None = None) -> RenderableType:
        ctx = ctx or self._render_context()
        selected = self._selected
        model = self._model
        return _card(
            "Position",
            lambda: render_position_detail(model.position(selected) if selected else None, ctx),
        )

    def _redraw_detail(self, ctx: RenderContext) -> None:
        shown = self._detail_open and self.is_wide and self._selected is not None
        self._home.query_one("#detail").set_class(shown, "-open")
        if shown:
            self._set("detail", self._detail_content(ctx))

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    @property
    def _on_home(self) -> bool:
        return len(self.screen_stack) <= 1

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
        if not self._on_home or not coins:
            return
        current = coins.index(self._selected) if self._selected in coins else 0
        self._selected = coins[(current + step) % len(coins)]
        self._redraw()

    def action_open_detail(self) -> None:
        """Open the selected position: beside the list when wide, else full screen."""
        if not self._on_home or self._selected is None:
            return
        if self.is_wide:
            self._detail_open = True
            self._redraw()
            return
        self.push_screen(_PanelScreen(self._detail_content))

    def action_close_detail(self) -> None:
        """Close the detail pane on a wide terminal."""
        if self._on_home and self._detail_open:
            self._detail_open = False
            self._redraw()

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
        keys = [
            Text("Keys", style="bold"),
            Text("  D day · W week · M month · Y year"),
            Text("  ↑ ↓ choose a position · Enter open its detail · Esc back"),
            Text("  L logs · ? this help · Q quit"),
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
    exec_fn: Callable[[str, list[str]], object] = os.execv,
    argv: Sequence[str] | None = None,
    platform: str = sys.platform,
) -> None:
    """Run the home screen until the operator quits, or restarts onto a new release.

    Args:
        model: The read model to show.
        journal: Source of the logs panel's lines; see :class:`HomeApp`.
        release: Source of the running release's name; see :class:`HomeApp`.
        exec_fn: ``os.execv``-shaped replacement of the process; injectable for tests.
        argv: The command line to restart with; defaults to ``sys.argv``.
        platform: ``sys.platform``; where exec is not meaningful (Windows) the
            console exits with a message instead.
    """
    app = HomeApp(model, journal=journal, release=release)
    app.run()
    if not app.restart_requested:
        return
    if platform == "win32":
        sys.stdout.write("New version deployed: start hermes again to use it.\n")
        return
    exec_fn(sys.executable, [sys.executable, *(sys.argv if argv is None else argv)])
