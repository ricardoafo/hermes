"""The Cockpit console's Positions screen, view only (design spec screen 3, issue #715).

One :class:`~hermes.entrypoints.tui_widgets.HudPanel` on tab 3 holds the open
positions and the recently closed ones as a
:class:`~hermes.entrypoints.tui_query.QueryList`; from 100 columns a second
panel beside it shows the selected position's detail (big net, why it opened,
the stop and what would close it, the money waterfall, the chart and the
timeline, drawn by :func:`~hermes.application.console.render.render_position_detail`).
Under 100 columns Enter opens the detail as a
:class:`~hermes.entrypoints.tui_drilldown.DetailScreen` under
``◂ Positions › BTC``.

Money follows the render context: when the surface hides money, no net or
price reaches a row or the closed-position detail.

Every builder here is a pure function of view objects, the time zone and the
token sheet, so tests check the text without running an app.

Reference: openspec/changes/715-positions-screen/change.md.
"""

# ruff: noqa: RUF002 - the single right angle quote is the design spec's breadcrumb separator

from collections.abc import Callable, Sequence
from datetime import datetime, tzinfo
from typing import ClassVar, Final

from rich.console import RenderableType
from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable, Static

from hermes.application.console.format import MINUS, fmt_eur, signed_eur, signed_pct
from hermes.application.console.query import Row
from hermes.application.console.render import RenderContext
from hermes.application.console.views import (
    ActivityDay,
    ActivityItem,
    ActivityKind,
    PositionRow,
)
from hermes.entrypoints.tui_drilldown import (
    DetailMode,
    DetailScreen,
    ListState,
    detail_mode,
)
from hermes.entrypoints.tui_query import QueryList, SortableTable
from hermes.entrypoints.tui_theme import COMPACT_BELOW, Tokens, tokens_for
from hermes.entrypoints.tui_widgets import HudPanel

__all__ = [
    "POSITION_COLUMNS",
    "POSITION_FACETS",
    "POSITION_QUERY_FIELDS",
    "PositionsView",
    "closed_detail",
    "closed_items",
    "position_rows",
]

POSITION_COLUMNS: Final = (
    ("coin", "Coin"),
    ("tf", "TF"),
    ("state", "State"),
    ("when", "When"),
    ("change", "Change"),
    ("net", "Net"),
    ("stop", "Stop room"),
)
POSITION_QUERY_FIELDS: Final = ("coin", "state", "side", "sort")
POSITION_FACETS: Final = ("state", "side")
_PLACEHOLDER: Final = "⌕ type to filter…  state:open coin:ETH side:long"
_TRAIL: Final = ("Positions",)
_NONE: Final = "—"
_CLOSED_KEEP: Final = 20
_DETAIL_CHIPS: Final = (
    ("Esc", "back"),
    ("[ ]", "prev/next"),
    ("1-8", "screens"),
    ("Q", "quit"),
)


def _when(at: datetime, tz: tzinfo) -> str:
    return at.astimezone(tz).strftime("%m-%d %H:%M")


def closed_items(days: Sequence[ActivityDay]) -> tuple[ActivityItem, ...]:
    """Return the recently closed positions in the activity, newest first.

    Args:
        days: The activity days, newest first.

    Returns:
        The ``closed`` rows that name a coin, at most 20.
    """
    items = [
        item
        for day in days
        for item in day.items
        if item.kind is ActivityKind.CLOSED and item.coin is not None
    ]
    return tuple(sorted(items, key=lambda item: item.at, reverse=True)[:_CLOSED_KEEP])


def _closed_key(item: ActivityItem) -> str:
    return f"closed:{item.coin}:{item.at.isoformat()}"


def position_rows(
    positions: Sequence[PositionRow],
    closed: Sequence[ActivityItem],
    ctx: RenderContext,
) -> tuple[Row, ...]:
    """Return the open and the recently closed positions as rows for the query list.

    Args:
        positions: The open positions.
        closed: The recently closed positions, newest first.
        ctx: Render context; money columns stay empty when it hides money.

    Returns:
        Open rows first, then closed rows. A row holds ``key``, ``coin``,
        ``tf``, ``side``, ``state`` (``open`` or ``closed``), ``when``, ``at``
        (UTC, for ``since:``), ``change``, ``net`` and ``stop``; a value the
        row does not have, or that money-hiding surfaces must not show, is
        ``None``.
    """
    money = ctx.shows_money
    rows: list[Row] = []
    for position in positions:
        priced = money and not position.eur_pending
        rows.append(
            {
                "key": position.coin,
                "coin": position.coin,
                "tf": position.timeframe,
                "side": position.side,
                "state": "open",
                "when": f"opened {_when(position.opened_at, ctx.tz)}",
                "at": position.opened_at,
                "change": signed_pct(position.change_pct) if money else None,
                "net": signed_eur(position.net) if priced else None,
                "stop": f"{MINUS}{abs(position.stop_distance_pct):.1f}% · "
                f"{position.stop_distance_atr:.1f} ATR",
            }
        )
    rows.extend(
        {
            "key": _closed_key(item),
            "coin": item.coin,
            "tf": None,
            "side": None,
            "state": "closed",
            "when": f"closed {_when(item.at, ctx.tz)}",
            "at": item.at,
            "change": None,
            "net": signed_eur(item.net) if money and item.net is not None else None,
            "stop": None,
        }
        for item in closed
    )
    return tuple(rows)


def closed_detail(item: ActivityItem, ctx: RenderContext, tokens: Tokens) -> Text:
    """Return the detail of a closed position: what the activity recorded about it.

    Args:
        item: The ``closed`` activity row.
        ctx: Render context; the exit price and the net show only with money.
        tokens: Token sheet.

    Returns:
        The coin, when it closed, and (with money) the exit price and the net.
    """
    out = Text()
    out.append(f"{item.coin} · closed", style="bold")
    out.append(f"\nclosed {_when(item.at, ctx.tz)}", style=tokens.muted)
    if ctx.shows_money:
        if item.price_eur is not None:
            out.append(f"\nexit price {fmt_eur(item.price_eur)}")
        if item.net is not None:
            colour = tokens.up if item.net >= 0 else tokens.down
            out.append("\nnet ")
            out.append(signed_eur(item.net), style=colour)
    out.append(
        "\n\nA closed position keeps its full record in the Tax screen's lots and the Activity "
        "panel.",
        style=tokens.dim,
    )
    return out


class PositionsView(Vertical, can_focus=True):
    """The Positions screen: the list, and the selected position's detail beside it.

    Args:
        detail_body: Draws an open position's detail for a coin (the app adds
            its live line and the unavailable states).
        now: The current time (``since:`` ends there).
        id: Widget id.
    """

    DEFAULT_CSS = """
    PositionsView { height: 1fr; }
    PositionsView > #positions-body { height: 1fr; }
    PositionsView #positions-body > HudPanel { height: 1fr; }
    PositionsView #positions-body > HudPanel > .hud-body { height: 1fr; }
    PositionsView #positions-list-panel > QueryList { height: 1fr; }
    PositionsView #positions-list-panel #positions-note { height: auto; padding: 0 1; }
    PositionsView #positions-list-panel #positions-note.-empty { display: none; }
    PositionsView #positions-detail-panel { display: none; width: 2fr; }
    PositionsView #positions-list-panel { width: 3fr; }
    PositionsView #positions-detail { height: 1fr; overflow-y: auto; }
    PositionsView.-beside #positions-detail-panel { display: block; }
    """
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("slash", "focus_query", "filter", show=False),
    ]

    def __init__(
        self,
        *,
        detail_body: Callable[[str], RenderableType],
        now: Callable[[], datetime],
        id: str | None = None,
    ) -> None:
        """Remember how to draw a detail; nothing shows before the first read."""
        super().__init__(id=id)
        self._detail_body = detail_body
        self._now = now
        self._ctx: RenderContext | None = None
        self._closed_by_key: dict[str, ActivityItem] = {}
        self._shown: tuple[Row, ...] = ()
        self._open_keys: frozenset[str] = frozenset()
        self._error: str | None = None
        self._cursor_key: str | None = None
        self.beside = False

    def compose(self) -> ComposeResult:
        """Lay out the list panel and the detail panel."""
        with Horizontal(id="positions-body"):
            yield HudPanel(
                Static(id="positions-note"),
                QueryList(
                    (),
                    POSITION_COLUMNS,
                    fields=POSITION_QUERY_FIELDS,
                    facets=POSITION_FACETS,
                    now=self._now,
                    placeholder=_PLACEHOLDER,
                    id="positions-list",
                ),
                title="Positions",
                subtitle="↑↓ select · ↵ open · / filter · S sort",
                id="positions-list-panel",
            )
            yield HudPanel(
                Static(id="positions-detail"),
                title="Detail",
                subtitle="why it opened · what would close it",
                id="positions-detail-panel",
            )

    def on_mount(self) -> None:
        """Redraw the colours when the theme changes."""
        self.app.theme_changed_signal.subscribe(self, lambda _theme: self._paint_detail())
        self._arrange()

    def on_resize(self, event: events.Resize) -> None:
        """Show the detail beside the list when it fits."""
        del event
        self._arrange()
        self._paint_detail()

    def _arrange(self) -> None:
        self.beside = detail_mode(self.app.size.width) is DetailMode.PANE
        self.set_class(self.beside, "-beside")

    @property
    def table(self) -> SortableTable:
        """Return the list's table."""
        return self.query_one(SortableTable)

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    def receive(
        self,
        positions: Sequence[PositionRow] | None,
        closed: Sequence[ActivityItem],
        ctx: RenderContext,
        *,
        error: str | None = None,
    ) -> None:
        """Show a read of the positions.

        Args:
            positions: The open positions; ``None`` when they could not be read.
            closed: The recently closed positions, newest first.
            ctx: The read's render context.
            error: Why the open positions are not shown, when ``positions`` is ``None``.
        """
        if not self.is_mounted:
            return
        self._ctx = ctx
        self._error = error
        self._closed_by_key = {_closed_key(item): item for item in closed}
        rows = () if positions is None else position_rows(positions, closed, ctx)
        self._open_keys = frozenset(str(row["key"]) for row in rows if row.get("state") == "open")
        current = self._current_key()
        self.query_one(QueryList).set_rows(rows)
        self._cursor_key = current
        self._restore_cursor()
        self._paint()

    def _current_key(self) -> str | None:
        row = self.table.cursor_row
        if 0 <= row < len(self._shown):
            return str(self._shown[row].get("key"))
        return self._cursor_key

    def _restore_cursor(self) -> None:
        keys = self._keys()
        if self._cursor_key in keys:
            self.table.move_cursor(row=keys.index(str(self._cursor_key)), animate=False)

    def _keys(self) -> list[str]:
        return [str(row.get("key")) for row in self._shown]

    def selected_key(self) -> str | None:
        """Return the key of the row under the cursor: a coin, or a closed position's key."""
        row = self.table.cursor_row
        if not 0 <= row < len(self._shown):
            return None
        return str(self._shown[row].get("key"))

    # ------------------------------------------------------------------
    # Drawing
    # ------------------------------------------------------------------

    def on_query_list_refreshed(self, event: QueryList.Refreshed) -> None:
        """Redraw what depends on the rows shown."""
        event.stop()
        self._shown = event.shown
        self._restore_cursor()
        self._paint()

    def on_data_table_cell_highlighted(self, event: DataTable.CellHighlighted) -> None:
        """Follow the cursor with the detail."""
        event.stop()
        self._cursor_key = self.selected_key()
        self._paint_detail()

    def _crumb(self, key: str) -> str:
        item = self._closed_by_key.get(key)
        if item is None or self._ctx is None:
            return key
        return f"{item.coin} closed {_when(item.at, self._ctx.tz)}"

    def _body(self, key: str) -> RenderableType:
        tokens = tokens_for(self.app.theme)
        if key in self._closed_by_key and self._ctx is not None:
            return closed_detail(self._closed_by_key[key], self._ctx, tokens)
        if key in self._open_keys:
            return self._detail_body(key)
        return Text("This position left the list.", style=tokens.muted)

    def _empty_text(self, tokens: Tokens) -> Text:
        if self._error is not None:
            return Text(self._error, style=tokens.warn)
        if self._ctx is None:
            return Text("Reading the positions…", style=tokens.dim)
        if self._shown:
            return Text("")
        if self.query_one(QueryList).rows:
            return Text("No position matches the filter.", style=tokens.muted)
        return Text("No open or recently closed position yet.", style=tokens.muted)

    def _paint(self) -> None:
        if not self.is_mounted:
            return
        tokens = tokens_for(self.app.theme)
        note = self.query_one("#positions-note", Static)
        text = self._empty_text(tokens)
        note.update(text)
        note.set_class(not text.plain, "-empty")
        self._paint_detail()

    def _paint_detail(self) -> None:
        if not self.is_mounted:
            return
        key = self.selected_key()
        detail = self.query_one("#positions-detail", Static)
        if key is None or not self.beside:
            detail.update(Text(""))
            return
        detail.update(self._body(key))

    # ------------------------------------------------------------------
    # Opening a position on a compact terminal
    # ------------------------------------------------------------------

    def list_state(self) -> ListState:
        """Return the list's sort and cursor (its query stays in the bar)."""
        sort = self.table.sort_keys[:1]
        return ListState(
            sort=sort[0].field if sort else None,
            descending=bool(sort and sort[0].descending),
            cursor=self.selected_key(),
        )

    def restore_list_state(self, state: ListState | None) -> None:
        """Put the cursor on the position shown last.

        Args:
            state: What the detail screen returned; ``None`` changes nothing.
        """
        if state is None or state.cursor is None:
            return
        self._cursor_key = state.cursor
        self._restore_cursor()
        self.table.focus()

    def on_data_table_cell_selected(self, event: DataTable.CellSelected) -> None:
        """Enter opens the position as a screen when compact; else the panel already shows it."""
        event.stop()
        key = self.selected_key()
        if key is None or self.app.size.width >= COMPACT_BELOW:
            return
        self.app.push_screen(
            DetailScreen(
                trail=_TRAIL,
                keys=self._keys,
                current=key,
                body=self._body,
                title="Position",
                state=self.list_state(),
                label=self._crumb,
                chips=_DETAIL_CHIPS,
            ),
            self.restore_list_state,
        )

    def action_focus_query(self) -> None:
        """Move to the query line."""
        self.query_one("#query-input").focus()
