"""Reusable query, filter and sort widgets for console lists (issue #729).

Every console list (logs, tax lots, briefs, signals, activity) filters and sorts
the same way, through :mod:`hermes.application.console.query`:

- :class:`QueryBar`: one input line; Tab completes field names, the line under
  it shows "N of M", the sort indicator, or the parse error to fix.
- :class:`FacetFilter`: the values of each facet in the current result, with
  their counts.
- :class:`SortableTable`: a ``DataTable`` sorted by clicking a header or
  pressing ``S`` on a column; the sorted columns carry an arrow.
- :class:`QueryList`: the three wired together over a list of rows; this is
  what a screen embeds.

Styling is a few ``DEFAULT_CSS`` lines that read only the console's ``$hud-*``
theme variables (:mod:`hermes.entrypoints.tui_theme`), so Neon, Control room and
night each restyle the widgets with no colour of their own; the widgets only
read, so a list screen stays read-only.

:class:`QueryHarnessApp` and :func:`activity_query_app` show the widgets over
activity rows inside a :class:`~hermes.entrypoints.tui_widgets.HudPanel`. They
are a demo harness, not wired into ``hermes tui``.

Reference: openspec/changes/729-query-bar/change.md.
"""

from collections.abc import Callable, Iterable, Sequence
from datetime import datetime, tzinfo
from typing import ClassVar, Final

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.message import Message
from textual.widget import Widget
from textual.widgets import DataTable, Input, Static

from hermes.application.console.activity import ACTIVITY_QUERY_FIELDS, activity_query_rows
from hermes.application.console.query import (
    SHARED_FACETS,
    Query,
    QueryError,
    Row,
    SortKey,
    complete,
    display_value,
    facet_counts,
    filter_rows,
    format_sort,
    parse_query,
    sort_rows,
    toggle_sort,
)
from hermes.application.console.views import ActivityItem
from hermes.entrypoints.tui_theme import (
    BASE,
    NEON_THEME,
    THEMES,
    data_tokens,
    terminal_theme,
    theme_variables,
)
from hermes.entrypoints.tui_widgets import HudPanel

__all__ = [
    "ACTIVITY_COLUMNS",
    "FacetFilter",
    "QueryBar",
    "QueryHarnessApp",
    "QueryInput",
    "QueryList",
    "SortableTable",
    "activity_query_app",
    "facet_lines",
    "status_line",
]

type Column = tuple[str, str]

ACTIVITY_COLUMNS: Final[tuple[Column, ...]] = (
    ("when", "When"),
    ("kind", "Kind"),
    ("coin", "Coin"),
    ("text", "Text"),
)
_PLACEHOLDER: Final = "filter: coin:BTC kind:closed year>=2026 since:7d or any text · Tab completes"
_FACET_VALUES: Final = 4


def status_line(shown: int, total: int, sort: Sequence[SortKey]) -> str:
    """Return the line under the query input, e.g. ``3 of 12 · sort when ▼``.

    Args:
        shown: Rows the query keeps.
        total: Rows in the list.
        sort: The current sort levels.

    Returns:
        The count, then the sort indicator when the list is sorted.
    """
    indicator = format_sort(sort)
    count = f"{shown} of {total}"
    return f"{count} · sort {indicator}" if indicator else count


def facet_lines(rows: Iterable[Row], facets: Sequence[str], limit: int = _FACET_VALUES) -> str:
    """Return one line per facet: its most common values and their counts.

    Args:
        rows: The rows to count (the current result).
        facets: Facet fields, in display order.
        limit: Values shown per facet before "+N more".

    Returns:
        Lines like ``coin  BTC 3 · ETH 2``; a facet with no values is left out.
    """
    kept = list(rows)
    lines = []
    for facet in facets:
        counts = facet_counts(kept, facet)
        if not counts:
            continue
        shown = " · ".join(f"{c.value} {c.count}" for c in counts[:limit])
        more = f" · +{len(counts) - limit} more" if len(counts) > limit else ""
        lines.append(f"{facet}  {shown}{more}")
    return "\n".join(lines)


class QueryInput(Input):
    """The query line; Tab completes the field name being typed."""

    DEFAULT_CSS = """
    QueryInput {
        height: 1; border: none; padding: 0 1;
        background: $hud-void; color: $hud-text;
    }
    QueryInput:focus { background: $hud-panel-focus; border: none; }
    QueryInput > .input--placeholder { color: $hud-dim; }
    QueryInput > .input--cursor { background: $hud-live; color: $hud-void; }
    QueryInput > .input--selection { background: $hud-frame; }
    """
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("tab", "complete", "complete", show=False),
    ]

    def __init__(
        self,
        fields: Sequence[str],
        *,
        placeholder: str = "",
        id: str | None = None,
    ) -> None:
        """Remember which field names Tab completes.

        Args:
            fields: The list's field names.
            placeholder: Hint shown in the empty input.
            id: Widget id.
        """
        super().__init__(placeholder=placeholder, id=id)
        self._fields = tuple(fields)

    def action_complete(self) -> None:
        """Complete the last word to a field name (or the common prefix)."""
        completed = complete(self.value, self._fields)
        if completed != self.value:
            self.value = completed
            self.cursor_position = len(completed)


class QueryBar(Widget):
    """A query input with its result count, sort indicator or error below."""

    DEFAULT_CSS = """
    QueryBar { height: auto; }
    QueryBar > #query-status { height: 1; color: $hud-muted; }
    QueryBar > #query-status.-error { color: $hud-warn; }
    """

    class Changed(Message):
        """The input now parses to ``query``."""

        def __init__(self, query: Query) -> None:
            """Carry the parsed query."""
            super().__init__()
            self.current = query

    def __init__(
        self,
        fields: Sequence[str] = SHARED_FACETS,
        *,
        placeholder: str = _PLACEHOLDER,
        id: str | None = None,
    ) -> None:
        """Set the accepted fields.

        Args:
            fields: Field names conditions may use; ``since`` is always accepted.
            placeholder: Hint shown in the empty input.
            id: Widget id.
        """
        super().__init__(id=id)
        self._fields = tuple(fields)
        self._placeholder = placeholder
        self.current = Query()
        self.error: str | None = None
        self._status = ""

    def compose(self) -> ComposeResult:
        """Lay out the input and the status line."""
        yield QueryInput(self._fields, placeholder=self._placeholder, id="query-input")
        yield Static(id="query-status")

    @property
    def status_text(self) -> str:
        """Return the status line as shown."""
        return self.error if self.error is not None else self._status

    def on_input_changed(self, event: Input.Changed) -> None:
        """Parse the line; post :class:`Changed` or show the error."""
        event.stop()
        try:
            query = parse_query(event.value, self._fields)
        except QueryError as exc:
            self.error = str(exc)
        else:
            self.error = None
            self.current = query
            self.post_message(self.Changed(query))
        self._draw()

    def show_result(self, shown: int, total: int, sort: Sequence[SortKey]) -> None:
        """Show the result count and the sort indicator.

        Args:
            shown: Rows the query keeps.
            total: Rows in the list.
            sort: The current sort levels.
        """
        self._status = status_line(shown, total, sort)
        self._draw()

    def _draw(self) -> None:
        for status in self.query_children("#query-status").results(Static):
            status.update(Text(self.status_text))
            status.set_class(self.error is not None, "-error")


class FacetFilter(Static):
    """Facet values with their counts in the current result."""

    DEFAULT_CSS = """
    FacetFilter { height: auto; color: $hud-muted; }
    """

    def __init__(self, facets: Sequence[str], *, id: str | None = None) -> None:
        """Set which facets are counted.

        Args:
            facets: Facet fields, in display order.
            id: Widget id.
        """
        super().__init__(id=id)
        self.facets = tuple(facets)
        self.counts_text = ""

    def show_counts(self, rows: Iterable[Row]) -> None:
        """Count ``rows`` per facet value and show the result."""
        self.counts_text = facet_lines(rows, self.facets)
        self.update(Text(self.counts_text))


class SortableTable(DataTable[str]):
    """A table sorted by a header click or ``S`` on the cursor's column."""

    DEFAULT_CSS = """
    SortableTable { background: $hud-panel; color: $hud-text; }
    SortableTable > .datatable--header { background: $hud-panel-focus; color: $hud-live; }
    SortableTable > .datatable--cursor { background: $hud-live; color: $hud-void; }
    SortableTable > .datatable--hover { background: $hud-panel-focus; }
    SortableTable:focus > .datatable--cursor { background: $hud-live; color: $hud-void; }
    """
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("s,S", "sort_column", "sort", show=False),
    ]

    class SortChanged(Message):
        """The sort levels changed to ``keys``."""

        def __init__(self, keys: tuple[SortKey, ...]) -> None:
            """Carry the new sort levels."""
            super().__init__()
            self.keys = keys

    def __init__(
        self,
        columns: Sequence[Column],
        *,
        sort: Sequence[SortKey] = (),
        id: str | None = None,
    ) -> None:
        """Set the columns and the starting sort.

        Args:
            columns: ``(field, label)`` pairs, left to right.
            sort: Sort levels to start with.
            id: Widget id.
        """
        super().__init__(id=id, cursor_type="cell", zebra_stripes=False)
        self.fields = tuple(field for field, _ in columns)
        self._labels = dict(columns)
        self.sort_keys: tuple[SortKey, ...] = tuple(sort)

    def _label(self, field: str) -> Text:
        for key in self.sort_keys[:1]:
            if key.field == field:
                return Text(f"{self._labels[field]} {'▼' if key.descending else '▲'}")
        return Text(self._labels[field])

    def show_rows(self, rows: Sequence[Row]) -> None:
        """Replace the table's rows, keeping the cursor where it was.

        Args:
            rows: The rows to show, already filtered and sorted.
        """
        cursor = self.cursor_coordinate
        self.clear(columns=True)
        for field in self.fields:
            self.add_column(self._label(field), key=field)
        for row in rows:
            self.add_row(*(display_value(row.get(field)) for field in self.fields))
        self.move_cursor(row=cursor.row, column=cursor.column, animate=False)

    def sort_by(self, field: str) -> None:
        """Apply one sort click on ``field`` and announce the new sort."""
        self.sort_keys = toggle_sort(self.sort_keys, field)
        self.post_message(self.SortChanged(self.sort_keys))

    def action_sort_column(self) -> None:
        """Sort by the column under the cursor (again flips the direction)."""
        if self.fields:
            self.sort_by(self.fields[min(self.cursor_column, len(self.fields) - 1)])

    def on_data_table_header_selected(self, event: DataTable.HeaderSelected) -> None:
        """Sort by the clicked header's column."""
        event.stop()
        field = event.column_key.value
        if field is not None:
            self.sort_by(field)


class QueryList(Widget):
    """A query bar, facet counts and a sortable table over one list of rows.

    After every filter or sort it posts :class:`QueryList.Refreshed`, so a
    screen can redraw what depends on the rows shown (subtotals, a detail).
    """

    class Refreshed(Message):
        """The list now shows ``shown``."""

        def __init__(self, shown: tuple[Row, ...]) -> None:
            """Carry the rows shown."""
            super().__init__()
            self.shown = shown

    DEFAULT_CSS = """
    QueryList { height: 1fr; background: $hud-panel; }
    QueryList > SortableTable { height: 1fr; }
    """

    def __init__(
        self,
        rows: Sequence[Row],
        columns: Sequence[Column],
        *,
        fields: Sequence[str] = SHARED_FACETS,
        facets: Sequence[str] = (),
        sort: Sequence[SortKey] = (),
        now: Callable[[], datetime],
        placeholder: str = _PLACEHOLDER,
        id: str | None = None,
    ) -> None:
        """Hold the rows and how to query them.

        Args:
            rows: Every row of the list.
            columns: ``(field, label)`` pairs the table shows.
            fields: Field names conditions may use.
            facets: Facets counted under the bar.
            sort: Sort levels to start with.
            now: The moment ``since:`` windows end at, read on every filter.
            placeholder: Hint shown in the empty query input.
            id: Widget id.
        """
        super().__init__(id=id)
        self._placeholder = placeholder
        self.rows = tuple(rows)
        self._columns = tuple(columns)
        self._fields = tuple(fields)
        self._facets = tuple(facets)
        self._sort = tuple(sort)
        self._now = now
        self.shown: tuple[Row, ...] = ()

    def compose(self) -> ComposeResult:
        """Lay out the bar, the facets and the table."""
        yield QueryBar(self._fields, placeholder=self._placeholder, id="query-bar")
        yield FacetFilter(self._facets, id="facets")
        yield SortableTable(self._columns, sort=self._sort, id="query-table")

    def on_mount(self) -> None:
        """Show every row in the starting sort."""
        self.refresh_rows()

    def set_rows(self, rows: Sequence[Row]) -> None:
        """Replace the list's rows and re-apply the query and the sort."""
        self.rows = tuple(rows)
        self.refresh_rows()

    def refresh_rows(self) -> None:
        """Filter, sort and redraw from the current query and sort."""
        bar = self.query_one(QueryBar)
        table = self.query_one(SortableTable)
        kept = filter_rows(self.rows, bar.current, now=self._now())
        self.shown = sort_rows(kept, table.sort_keys)
        table.show_rows(self.shown)
        self.query_one(FacetFilter).show_counts(self.shown)
        bar.show_result(len(self.shown), len(self.rows), table.sort_keys)
        self.post_message(self.Refreshed(self.shown))

    def on_query_bar_changed(self, event: QueryBar.Changed) -> None:
        """Re-filter on a new query."""
        event.stop()
        self.refresh_rows()

    def on_sortable_table_sort_changed(self, event: SortableTable.SortChanged) -> None:
        """Re-sort on a new sort."""
        event.stop()
        self.refresh_rows()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Enter moves from the query line to the table."""
        event.stop()
        self.query_one(SortableTable).focus()


class QueryHarnessApp(App[None]):
    """A demo screen holding one :class:`QueryList` (not part of ``hermes tui``)."""

    TITLE = "hermes · query"
    CSS = """
    Screen { background: $hud-void; }
    #query-panel { height: 1fr; }
    #query-panel > .hud-body { height: 1fr; }
    """
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("ctrl+q", "quit", "quit", show=False),
    ]

    def __init__(
        self,
        query_list: Callable[[], QueryList],
        *,
        title: str = "Query",
        theme: str = NEON_THEME,
        motion: bool = True,
    ) -> None:
        """Remember how to build the list and which theme draws it.

        Args:
            query_list: Builds the :class:`QueryList` to show.
            title: Title of the panel the list sits in.
            theme: A hermes theme name from :mod:`hermes.entrypoints.tui_theme`.
            motion: Blink the input cursor; off gives a still picture.
        """
        super().__init__()
        self._build = query_list
        self._title = title
        self._motion = motion
        for hermes_theme in THEMES:
            self.register_theme(hermes_theme)
        self.ansi_theme_dark = terminal_theme(data_tokens(theme))
        self.theme = theme

    def get_theme_variable_defaults(self) -> dict[str, str]:
        """Give TCSS the ``hud-*`` variables before a hermes theme is applied."""
        return theme_variables(BASE)

    def compose(self) -> ComposeResult:
        """Lay out the list in a panel."""
        yield HudPanel(
            self._build(),
            title=self._title,
            subtitle="Tab completes · Enter table · S sorts",
            id="query-panel",
        )

    def on_mount(self) -> None:
        """Freeze the input cursor when motion is off."""
        if not self._motion:
            self.query_one(QueryInput).cursor_blink = False


def activity_query_app(
    items: Iterable[ActivityItem],
    tz: tzinfo,
    now: Callable[[], datetime],
    *,
    theme: str = NEON_THEME,
    motion: bool = True,
) -> QueryHarnessApp:
    """Return the demo harness over activity rows, newest first.

    Args:
        items: Activity rows.
        tz: Timezone for the time facets and the ``when`` column.
        now: The moment ``since:`` windows end at.
        theme: A hermes theme name (Neon by default).
        motion: Blink the input cursor; off gives a still picture.

    Returns:
        An app showing the activity in a :class:`QueryList`.
    """
    rows = activity_query_rows(items, tz)
    return QueryHarnessApp(
        lambda: QueryList(
            rows,
            ACTIVITY_COLUMNS,
            fields=ACTIVITY_QUERY_FIELDS,
            facets=("kind", "coin"),
            sort=(SortKey("when", descending=True),),
            now=now,
            id="activity-query",
        ),
        title="Activity",
        theme=theme,
        motion=motion,
    )
