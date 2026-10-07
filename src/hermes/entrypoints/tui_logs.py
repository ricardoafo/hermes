"""The Logs screen of the Cockpit console (design spec screen 8, issue #716).

hermes's journal as a queryable list, built from the shared pieces:

- the query line, facets and sortable table are #729's :class:`QueryList`,
  over rows from :func:`~hermes.application.console.log_figures.log_query_rows`
  (``level>=warn service:engine coin:ETH since:7d``; Tab completes fields);
- a volume histogram over the window, stacked and coloured by level from the
  token sheet; ``D`` ``W`` ``M`` ``Y`` zoom the window to a day, a week, a
  month or a year;
- Enter shows the selected line as pretty JSON in #730's :class:`DetailPane`
  beside the list (a :class:`DetailScreen` on compact terminals); ``C``
  copies it to the clipboard, ``E`` exports it to ``~/hermes-exports/`` on
  the PC (over ``--vps`` the console says export works from the PC);
- ``T`` opens the selected line's trace, every line with the same
  correlation id as a tree with timings, as a :class:`DetailScreen`
  (``◂ Logs › trace K7Q2ZP``);
- ``F`` follows the journal: the 5 s refresh re-reads it and appends the new
  lines.

The journal can hold money figures. This screen is operator-only and
read-only: it shows, copies or exports to the operator's own PC, and sends
nothing to any AI or notification channel.

Reference: openspec/changes/716-logs-screen/change.md.
"""

# ruff: noqa: RUF002 - the single right angle quote is the design spec's breadcrumb separator

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo
from pathlib import Path
from typing import ClassVar, Final

from rich.console import Group, RenderableType
from rich.json import JSON
from rich.text import Text
from rich.tree import Tree
from textual import events
from textual.actions import SkipAction
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable, Static

from hermes.application.console.log_figures import (
    LOG_FACETS,
    LOG_QUERY_FIELDS,
    TRACE_TAIL,
    VolumeBucket,
    append_new_lines,
    log_line_pretty_json,
    log_query_rows,
    trace_tree,
    volume_buckets,
)
from hermes.application.console.logs import LogLine
from hermes.application.console.query import LEVEL_ORDER, Row, SortKey
from hermes.entrypoints.tui_drilldown import (
    DETAIL_CHIPS,
    DetailMode,
    DetailPane,
    DetailScreen,
    ListState,
    detail_mode,
)
from hermes.entrypoints.tui_query import QueryInput, QueryList, SortableTable
from hermes.entrypoints.tui_theme import TALL_FROM, TOWERING_FROM, Tokens, tokens_for
from hermes.entrypoints.tui_widgets import HudPanel

__all__ = [
    "EXPORT_DIR_NAME",
    "LOG_COLUMNS",
    "LOG_WINDOWS",
    "LogWindow",
    "LogsView",
    "export_dir",
    "export_line",
    "histogram_text",
    "level_colour",
    "line_detail",
    "trace_text",
]

EXPORT_DIR_NAME: Final = "hermes-exports"
LOG_COLUMNS: Final = (
    ("time", "Time"),
    ("level", "Level"),
    ("service", "Service"),
    ("event", "Event"),
    ("coin", "Coin"),
    ("details", "Details"),
    ("trace", "Trace"),
)
READ_LINES: Final = 2000
KEEP_LINES: Final = 5000
_SSH_VARS: Final = ("SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY")
_PLACEHOLDER: Final = "level>=warn service:engine coin:ETH since:7d or any text · Tab completes"
_BLOCKS: Final = " ▁▂▃▄▅▆▇█"
_EIGHTHS: Final = 8
_LOGS_TRAIL: Final = ("Logs",)
_DEMO: Final = "Logs come from the VPS journal; demo data has none."
_UNAVAILABLE: Final = "The journal is not readable here; run hermes logs on the VPS."
_NO_LINES: Final = "No journal lines yet."
_VPS_EXPORT: Final = "Export works from the PC; over --vps, copy the line with C."
_LEVEL_LABELS: Final = {"warning": "warn"}
_HEIGHT_SHORT: Final = 2
_HEIGHT: Final = 3
_HEIGHT_TALL: Final = 5


@dataclass(frozen=True, slots=True)
class LogWindow:
    """One zoom level of the volume histogram.

    Attributes:
        key: The key that selects it.
        label: How the histogram names it.
        span: How far back it reaches from now.
    """

    key: str
    label: str
    span: timedelta


LOG_WINDOWS: Final = (
    LogWindow("d", "24 h", timedelta(days=1)),
    LogWindow("w", "7 days", timedelta(days=7)),
    LogWindow("m", "30 days", timedelta(days=30)),
    LogWindow("y", "365 days", timedelta(days=365)),
)


def export_dir(env: Mapping[str, str], home: Path) -> Path | None:
    """Return where ``E`` exports a line, or ``None`` over ``--vps``.

    Args:
        env: The environment; an ssh session (``hermes --vps tui``) carries
            ``SSH_CONNECTION``.
        home: The operator's home directory.

    Returns:
        ``home / "hermes-exports"`` on the PC; ``None`` in an ssh session,
        where a file would land on the VPS instead of the operator's PC.
    """
    if any(env.get(name) for name in _SSH_VARS):
        return None
    return home / EXPORT_DIR_NAME


def export_line(line: LogLine, directory: Path) -> Path:
    """Write one line as pretty JSON under ``directory`` and return the file.

    Args:
        line: The journal line.
        directory: The export folder; created when missing.

    Returns:
        ``log-YYYYMMDD-HHMMSS-<trace tail or "line">.json`` (the line's own
        UTC time), overwritten if it exists.

    Raises:
        OSError: The folder or the file cannot be written.
    """
    directory.mkdir(parents=True, exist_ok=True)
    corr = line.correlation_id
    tail = corr[-TRACE_TAIL:] if corr else "line"
    path = directory / f"log-{line.at:%Y%m%d-%H%M%S}-{tail}.json"
    path.write_text(log_line_pretty_json(line) + "\n", encoding="utf-8")
    return path


def level_colour(level_index: int, tokens: Tokens) -> str:
    """Return the token colour of a level (an index into ``LEVEL_ORDER``).

    debug is dim, info muted, warning amber, error and critical the down colour.
    """
    return (tokens.dim, tokens.muted, tokens.warn, tokens.down, tokens.down)[level_index]


def _stack(bucket: VolumeBucket) -> list[int]:
    """Return the level indexes bottom-up, worst first, one per line."""
    order = range(len(LEVEL_ORDER) - 1, -1, -1)
    return [index for index in order for _ in range(bucket.counts[index])]


def _cell(bucket: VolumeBucket, peak: int, height: int, row: int) -> tuple[str, int | None]:
    """Return the block and the level index for one cell; ``row`` 0 is the bottom."""
    if bucket.total == 0 or peak == 0:
        return " ", None
    filled = max(round(bucket.total / peak * height * _EIGHTHS), 1)
    here = min(max(filled - row * _EIGHTHS, 0), _EIGHTHS)
    if here == 0:
        return " ", None
    middle = (row * _EIGHTHS + here / 2) / filled
    stack = _stack(bucket)
    return _BLOCKS[here], stack[min(int(middle * len(stack)), len(stack) - 1)]


def histogram_text(
    buckets: Sequence[VolumeBucket], tokens: Tokens, *, height: int, window: str
) -> Text:
    """Return the volume histogram: one column per bucket, stacked by level.

    Args:
        buckets: Oldest first.
        tokens: Token sheet for the level colours.
        height: Rows of bars.
        window: The window's name for the caption.

    Returns:
        ``height`` rows of bars scaled to the busiest bucket (the worst level
        at the bottom of each column), then a caption with the window, the
        peak and the colour legend.
    """
    peak = max((b.total for b in buckets), default=0)
    out = Text()
    for row in range(height - 1, -1, -1):
        for bucket in buckets:
            block, level = _cell(bucket, peak, height, row)
            out.append(block, style="" if level is None else level_colour(level, tokens))
        out.append("\n")
    out.append(f"{window} · peak {peak} per bar ", style=tokens.muted)
    for index, name in enumerate(LEVEL_ORDER[:-1]):
        out.append(" ■", style=level_colour(index, tokens))
        out.append(f" {_LEVEL_LABELS.get(name, name)}", style=tokens.dim)
    return out


def _seconds(delta: timedelta) -> str:
    return f"{delta.total_seconds():.3f} s"


def _step_text(line: LogLine, offset: timedelta, gap: timedelta, tokens: Tokens) -> Text:
    out = Text()
    out.append(f"+{_seconds(offset)}", style=tokens.text)
    out.append(f"  (+{_seconds(gap)})  ", style=tokens.dim)
    rank = LEVEL_ORDER.index(line.level) if line.level in LEVEL_ORDER else 1
    out.append(f"{line.level.upper():<8}", style=level_colour(rank, tokens))
    out.append(f" {line.event}")
    if line.fields:
        out.append("  " + " ".join(f"{k}={v}" for k, v in line.fields), style=tokens.muted)
    return out


def trace_text(lines: Sequence[LogLine], correlation_id: str, tz: tzinfo, tokens: Tokens) -> Tree:
    """Return one trace as a tree with timings.

    Args:
        lines: The journal lines read.
        correlation_id: The full id.
        tz: Display timezone for the start time.
        tokens: Token sheet.

    Returns:
        The root names the trace, its line count, span and start; one branch
        per event namespace with its offset and span; one leaf per line with
        its offset from the first line and its gap since the line before.
    """
    tree = trace_tree(lines, correlation_id)
    tail = correlation_id[-TRACE_TAIL:]
    if tree is None:
        return Tree(Text(f"trace {tail} · no lines in the journal read", style=tokens.muted))
    root = Text()
    root.append(f"trace {tail}", style=f"bold {tokens.text}")
    root.append(
        f" · {tree.count} lines · {_seconds(tree.span)} · "
        f"started {tree.started.astimezone(tz):%Y-%m-%d %H:%M:%S}",
        style=tokens.muted,
    )
    root.append(f"\n{correlation_id}", style=tokens.dim)
    out = Tree(root, guide_style=tokens.line)
    for group in tree.groups:
        label = Text()
        label.append(group.name, style=f"bold {tokens.live}")
        label.append(
            f"  +{_seconds(group.offset)} · span {_seconds(group.span)}", style=tokens.muted
        )
        branch = out.add(label)
        for step in group.steps:
            branch.add(_step_text(step.line, step.offset, step.gap, tokens))
    return out


def line_detail(line: LogLine, tokens: Tokens) -> RenderableType:
    """Return the selected line as pretty JSON with its key hints."""
    hints = Text("C copy · E export · T trace", style=tokens.dim)
    return Group(JSON(log_line_pretty_json(line)), Text(""), hints)


class LogsView(Vertical):
    """The Logs screen: volume histogram, query list and line detail.

    Args:
        journal: Returns the last N journal lines, or ``None`` when the
            journal is not readable here.
        now: The current time (``since:`` and the histogram end there).
        tz: Returns the display timezone.
        exports: Where ``E`` writes; ``None`` over ``--vps``.
        demo: Demo data: say there is no journal instead of reading one.
        id: Widget id.
    """

    DEFAULT_CSS = """
    LogsView { height: 1fr; }
    LogsView > #logs-volume { height: auto; }
    LogsView > #logs-main { height: 1fr; }
    LogsView #logs-list-panel { height: 1fr; width: 1fr; }
    LogsView #logs-list-panel > QueryList { height: 1fr; }
    LogsView #logs-pane { height: 1fr; width: 64; }
    LogsView #logs-pane > #logs-json { height: 1fr; overflow-y: auto; }
    """
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("t,T", "trace", "trace", show=False),
        Binding("f,F", "follow", "follow", show=False),
        Binding("c,C", "copy_line", "copy", show=False),
        Binding("e,E", "export_line", "export", show=False),
        Binding("slash", "focus_query", "query", show=False),
        Binding("escape", "close", "close", show=False),
        *(
            Binding(f"{w.key},{w.key.upper()}", f"zoom('{w.key}')", w.label, show=False)
            for w in LOG_WINDOWS
        ),
    ]

    def __init__(
        self,
        *,
        journal: Callable[[int], Sequence[LogLine] | None] | None,
        now: Callable[[], datetime],
        tz: Callable[[], tzinfo],
        exports: Path | None,
        demo: bool,
        id: str | None = None,
    ) -> None:
        """Remember the sources; nothing is read before the screen is shown."""
        super().__init__(id=id)
        self._journal = journal
        self._now = now
        self._tz = tz
        self._exports = exports
        self._demo = demo
        self.lines: tuple[LogLine, ...] = ()
        self._by_row: dict[int, LogLine] = {}
        self._shown: tuple[Row, ...] = ()
        self.window = LOG_WINDOWS[0]
        self.following = False
        self.readable = True
        self.pane_open = False

    def compose(self) -> ComposeResult:
        """Lay out the histogram and the list with its detail pane."""
        yield HudPanel(
            Static(id="logs-histogram"),
            title="Volume",
            subtitle="D W M Y window",
            id="logs-volume",
        )
        with Horizontal(id="logs-main"):
            yield HudPanel(
                QueryList(
                    (),
                    LOG_COLUMNS,
                    fields=LOG_QUERY_FIELDS,
                    facets=LOG_FACETS,
                    sort=(SortKey("time", descending=True),),
                    now=self._now,
                    placeholder=_PLACEHOLDER,
                    id="logs-list",
                ),
                title="Logs",
                subtitle=self._status_text(),
                id="logs-list-panel",
            )
            yield DetailPane(
                Static(id="logs-json"),
                title="Log line",
                subtitle="C copy · E export · T trace · Esc close",
                id="logs-pane",
            )

    def on_mount(self) -> None:
        """Redraw the histogram when the theme changes."""
        self.app.theme_changed_signal.subscribe(self, lambda _theme: self.paint_histogram())

    def on_resize(self, event: events.Resize) -> None:
        """Re-bucket the histogram at the new width."""
        del event
        self.paint_histogram()

    # ------------------------------------------------------------------
    # Reading the journal
    # ------------------------------------------------------------------

    def _read(self) -> Sequence[LogLine] | None:
        if self._demo or self._journal is None:
            return None
        return self._journal(READ_LINES)

    def load(self) -> None:
        """Read the journal again and show it (the screen was opened)."""
        lines = self._read()
        self.readable = lines is not None
        self._show(tuple(lines or ()))

    def tick(self) -> None:
        """On the refresh tick: re-read and append the new lines while following."""
        if not self.following or self._demo:
            return
        fresh = self._read()
        if fresh is None:
            self.readable = False
            self._paint_status()
            return
        self.readable = True
        merged = append_new_lines(self.lines, fresh, keep=KEEP_LINES)
        if merged != self.lines:
            self._show(merged)
        else:
            self._paint_status()

    def _show(self, lines: tuple[LogLine, ...]) -> None:
        self.lines = lines
        rows = log_query_rows(lines, self._tz())
        self._by_row = {id(row): line for row, line in zip(rows, lines, strict=True)}
        self.query_one(QueryList).set_rows(rows)
        self._paint_status()

    # ------------------------------------------------------------------
    # Drawing
    # ------------------------------------------------------------------

    def on_query_list_refreshed(self, event: QueryList.Refreshed) -> None:
        """Re-draw the histogram and the detail from the new result."""
        event.stop()
        self._shown = event.shown
        self.paint_histogram()
        self._paint_detail()

    def _histogram_height(self) -> int:
        height = self.app.size.height
        if height >= TOWERING_FROM:
            return _HEIGHT_TALL
        return _HEIGHT if height >= TALL_FROM else _HEIGHT_SHORT

    def paint_histogram(self) -> None:
        """Draw the volume histogram of the rows shown over the window."""
        if not self.is_mounted:
            return
        static = self.query_one("#logs-histogram", Static)
        width = max(static.content_size.width or self.size.width - 4, 1)
        buckets = volume_buckets(self._shown, end=self._now(), window=self.window.span, count=width)
        static.update(
            histogram_text(
                buckets,
                tokens_for(self.app.theme),
                height=self._histogram_height(),
                window=self.window.label,
            )
        )

    def _status_text(self) -> str:
        if self._demo:
            return _DEMO
        if not self.readable:
            return _UNAVAILABLE
        if not self.lines:
            return _NO_LINES
        follow = "following · re-read every 5 s" if self.following else "F follows live"
        return f"{len(self.lines)} lines read · {follow}"

    def _paint_status(self) -> None:
        panel = self.query_one("#logs-list-panel", HudPanel)
        panel.subtitle = self._status_text()
        panel.paint()

    @property
    def status_text(self) -> str:
        """Return the status the list panel shows on its bottom edge."""
        return self._status_text()

    def _paint_detail(self) -> None:
        pane = self.query_one(DetailPane)
        line = self.selected_line()
        if line is None:
            pane.is_open = self.pane_open = False
            return
        if self.pane_open:
            self.query_one("#logs-json", Static).update(
                line_detail(line, tokens_for(self.app.theme))
            )

    # ------------------------------------------------------------------
    # Selection and drill-down
    # ------------------------------------------------------------------

    @property
    def table(self) -> SortableTable:
        """Return the list's table."""
        return self.query_one(SortableTable)

    def selected_line(self) -> LogLine | None:
        """Return the line under the table cursor, if any."""
        row = self.table.cursor_row
        if not 0 <= row < len(self._shown):
            return None
        return self._by_row.get(id(self._shown[row]))

    def _row_keys(self) -> list[str]:
        return [str(index) for index in range(len(self._shown))]

    def _trace_keys(self) -> list[str]:
        keys: list[str] = []
        for row in self._shown:
            corr = self._by_row[id(row)].correlation_id
            if corr and corr not in keys:
                keys.append(corr)
        return keys

    def list_state(self) -> ListState:
        """Return the list's sort and cursor (its query stays in the bar)."""
        sort = self.table.sort_keys[:1]
        return ListState(
            sort=sort[0].field if sort else None,
            descending=bool(sort and sort[0].descending),
            cursor=str(self.table.cursor_row),
        )

    def restore_list_state(self, state: ListState | None) -> None:
        """Put the cursor on the line, or the first line of the trace, shown last.

        Args:
            state: What the detail screen returned; ``None`` changes nothing.
        """
        if state is None or state.cursor is None:
            return
        if state.cursor.isdigit():
            row = int(state.cursor)
        else:
            corrs = [self._by_row[id(r)].correlation_id for r in self._shown]
            row = corrs.index(state.cursor) if state.cursor in corrs else self.table.cursor_row
        self.table.move_cursor(row=row, animate=False)
        self.table.focus()

    def on_data_table_cell_selected(self, event: DataTable.CellSelected) -> None:
        """Enter opens the line: a pane beside the list, a screen on compact terminals."""
        event.stop()
        line = self.selected_line()
        if line is None:
            return
        if detail_mode(self.app.size.width) is DetailMode.PANE:
            self.pane_open = True
            self.query_one(DetailPane).is_open = True
            self._paint_detail()
            return
        self.app.push_screen(
            DetailScreen(
                trail=_LOGS_TRAIL,
                keys=self._row_keys,
                current=str(self.table.cursor_row),
                body=self._line_body,
                title="Log line",
                state=self.list_state(),
                label=self._line_label,
                chips=DETAIL_CHIPS,
            ),
            self.restore_list_state,
        )

    def on_data_table_cell_highlighted(self, event: DataTable.CellHighlighted) -> None:
        """Follow the cursor in the open pane."""
        event.stop()
        self._paint_detail()

    def _line_at(self, key: str) -> LogLine | None:
        index = int(key) if key.isdigit() else -1
        if not 0 <= index < len(self._shown):
            return None
        return self._by_row.get(id(self._shown[index]))

    def _line_body(self, key: str) -> RenderableType:
        line = self._line_at(key)
        if line is None:
            return Text("This line left the list.", style=tokens_for(self.app.theme).muted)
        return line_detail(line, tokens_for(self.app.theme))

    def _line_label(self, key: str) -> str:
        line = self._line_at(key)
        return "line" if line is None else f"line {line.at.astimezone(self._tz()):%H:%M:%S}"

    def _trace_body(self, correlation_id: str) -> RenderableType:
        return trace_text(self.lines, correlation_id, self._tz(), tokens_for(self.app.theme))

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def action_trace(self) -> None:
        """Open the selected line's trace as a detail screen."""
        line = self.selected_line()
        if line is None:
            raise SkipAction
        if not line.correlation_id:
            self.app.notify("This line has no trace (no correlation id).", severity="warning")
            return
        self.app.push_screen(
            DetailScreen(
                trail=_LOGS_TRAIL,
                keys=self._trace_keys,
                current=line.correlation_id,
                body=self._trace_body,
                title="Trace",
                state=self.list_state(),
                label=lambda corr: f"trace {corr[-TRACE_TAIL:]}",
                chips=DETAIL_CHIPS,
            ),
            self.restore_list_state,
        )

    def action_follow(self) -> None:
        """Start or stop following the journal."""
        self.following = not self.following
        if self.following:
            self.tick()
        self._paint_status()

    def action_copy_line(self) -> None:
        """Copy the selected line's JSON to the clipboard."""
        line = self.selected_line()
        if line is None:
            return
        self.app.copy_to_clipboard(log_line_pretty_json(line))
        self.app.notify("Copied the line as JSON.")

    def action_export_line(self) -> None:
        """Export the selected line's JSON to ``~/hermes-exports/`` on the PC."""
        line = self.selected_line()
        if line is None:
            return
        if self._exports is None:
            self.app.notify(_VPS_EXPORT, severity="warning")
            return
        try:
            path = export_line(line, self._exports)
        except OSError as exc:
            self.app.notify(f"Could not export the line: {exc}", severity="warning")
            return
        self.app.notify(f"Exported to {path}")

    def action_zoom(self, key: str) -> None:
        """Set the histogram window.

        Args:
            key: ``d``, ``w``, ``m`` or ``y``.
        """
        self.window = next(w for w in LOG_WINDOWS if w.key == key)
        self.paint_histogram()

    def action_focus_query(self) -> None:
        """Move to the query line."""
        self.query_one(QueryInput).focus()

    def action_close(self) -> None:
        """Close the pane, or leave the query line; else let Esc go back a screen."""
        if self.pane_open:
            self.pane_open = False
            self.query_one(DetailPane).is_open = False
            return
        if isinstance(self.app.focused, QueryInput):
            self.table.focus()
            return
        raise SkipAction
