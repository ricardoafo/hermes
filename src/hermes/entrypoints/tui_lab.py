"""The Lab screen of the Cockpit console (design: Lab, issue #1056).

The backtested strategies, read only from committed files
(:mod:`hermes.application.console.lab`):

- two views, switched with ``[`` and ``]``: **Backtests** (this screen) and
  **Drafts** (MVP2, issue #967; a placeholder here);
- a filter bar and a sortable list (#729's :class:`QueryList`): strategy,
  family, the stage strip ``S T V J`` (signed, Train, Validation, judged), the
  status, trials, the family's Validation touches used of 5, and the last run;
- the selected strategy's **Strategy** panel (brief, rules, hypothesis, the
  parameters as signed against as run) and **Results** panel (the gate's 17
  checks with their signed thresholds, the runs, the links);
- four layouts by width: compact (below 100 columns) lists only and Enter opens
  the strategy over its results in #730's :class:`DetailScreen`; standard puts
  the list on top and the two panels side by side below; wide keeps the list
  and the strategy on the left and the results on the right; ultra shows all
  three in columns.

Thresholds always show. The trial log's figures show only when the operator
turned ``show_values`` on in the settings file and the session is not an AI
session (``HERMES_AI_SESSION`` or ``CLAUDECODE``); ``V`` hides and shows them
again. In an AI session ``V`` does nothing and the reader never loads a figure.

Reference: openspec/archive/1056-lab-screen/change.md.
"""

# ruff: noqa: RUF001 - the single right angle quote is the design spec's breadcrumb separator

from collections.abc import Callable, Sequence
from datetime import datetime, tzinfo
from typing import ClassVar, Final

from rich.console import Group, RenderableType
from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Container, Vertical
from textual.widgets import DataTable, Static

from hermes.application.console.lab import (
    FAMILY_VALIDATION_BUDGET,
    GATE_CHECKS,
    LabReader,
    LabSnapshot,
    LabStatus,
    LabStrategy,
    Mark,
    StageVerdict,
)
from hermes.application.console.query import Row, SortKey
from hermes.entrypoints.tui_drilldown import (
    DETAIL_CHIPS,
    DetailMode,
    DetailScreen,
    ListState,
    detail_mode,
)
from hermes.entrypoints.tui_query import QueryList, SortableTable
from hermes.entrypoints.tui_theme import COMPACT_BELOW, ULTRA_FROM, WIDE_FROM, Tokens, tokens_for
from hermes.entrypoints.tui_widgets import HudPanel

__all__ = [
    "LAB_COLUMNS",
    "LAB_FACETS",
    "LAB_QUERY_FIELDS",
    "VIEWS",
    "LabView",
    "lab_layout",
    "lab_rows",
    "results_text",
    "stage_strip",
    "strategy_text",
    "view_bar",
]

LAB_COLUMNS: Final = (
    ("strategy", "Strategy"),
    ("family", "Family"),
    ("stages", "S T V J"),
    ("chip", "Status"),
    ("trials", "Trials"),
    ("famval", "Fam.val"),
    ("last", "Last run"),
)
LAB_QUERY_FIELDS: Final = ("strategy", "family", "status", "split", "sort")
LAB_FACETS: Final = ("status", "family", "split")
VIEWS: Final = ("Backtests", "Drafts")
_PLACEHOLDER: Final = "⌕ type to filter…  family:breakout status:champion split:validation"
_TRAIL: Final = ("Lab", "Backtests")
_NONE: Final = "—"
_NO_FILES: Final = (
    "No experiment files here. Run from a clone, or use --vps on a release that ships docs/."
)
_DRAFTS: Final = (
    "Drafts: the parameter chat and the drafts of the next batch come with the MVP2 lab "
    "(issue #967)."
)
_MARKS: Final = {Mark.PASS: "✓", Mark.FAIL: "✗", Mark.NA: "–"}
_CHIPS: Final = {
    LabStatus.CHAMPION: ("★", "champion"),
    LabStatus.GATE_PASS: ("✓", "gate-pass"),
    LabStatus.FAIL_VALIDATION: ("✗", "fail-validation"),
    LabStatus.FAIL_TRAIN: ("✗", "fail-train"),
    LabStatus.TRAIN_PASS: ("▸", "train-pass"),
    LabStatus.NO_VERDICT: ("·", "no-verdict"),
    LabStatus.DRAFT: ("○", "draft"),
}


def lab_layout(width: int) -> str:
    """Return the layout class for a terminal ``width``.

    Args:
        width: Terminal width in columns.

    Returns:
        ``-compact`` below 100 columns, ``-standard`` below 160, ``-wide``
        below 220, else ``-ultra``.
    """
    if width < COMPACT_BELOW:
        return "-compact"
    if width < WIDE_FROM:
        return "-standard"
    return "-wide" if width < ULTRA_FROM else "-ultra"


def stage_strip(strategy: LabStrategy) -> str:
    """Return the ``S T V J`` strip: signed, Train, Validation, judged.

    ``✓`` passed or signed, ``✗`` failed, ``·`` nothing yet, ``○`` not signed,
    ``★`` the batch's champion, a dash under V: Validation not run.
    """
    signed = "✓" if strategy.signed else "○"
    train, validation = (_MARKS.get(m, "·") if m is not None else "·" for m in strategy.stages)
    if not strategy.validation_run:
        validation = "–"
    judged = {"champion": "★", "pass": "✓", "fail": "✗"}.get(strategy.judged or "", "·")
    return f"{signed} {train} {validation} {judged}"


def lab_rows(strategies: Sequence[LabStrategy], tz: tzinfo) -> tuple[dict[str, object], ...]:
    """Return the strategies as rows for the shared query list.

    Args:
        strategies: The Lab's strategies.
        tz: Display timezone for the last-run column.

    Returns:
        One mapping per strategy: ``strategy``, ``family``, ``stages`` (the
        strip), ``status`` (the filter token) and ``chip`` (glyph and label),
        ``trials``, ``famval`` (``used/5``), ``last``, ``at`` (UTC, for
        ``since:``), ``split`` (the deepest split run) and ``sort``-less ``None``
        fields where nothing is recorded. Metric values never enter a row.
    """
    rows: list[dict[str, object]] = []
    for s in strategies:
        glyph, token = _CHIPS[s.status]
        splits = {run.split for run in s.runs}
        deepest = "validation" if "validation" in splits else ("train" if splits else None)
        rows.append(
            {
                "strategy": s.strategy_id,
                "family": s.family,
                "stages": stage_strip(s),
                "status": token,
                "chip": f"{glyph} {s.status.value}",
                "trials": s.trials,
                "famval": f"{s.family_touches}/{FAMILY_VALIDATION_BUDGET}" if s.family else None,
                "last": None if s.last_run is None else f"{s.last_run.astimezone(tz):%m-%d %H:%M}",
                "at": s.last_run,
                "split": deepest,
            }
        )
    return tuple(rows)


def _status_colour(status: LabStatus, tokens: Tokens) -> str:
    return {
        LabStatus.CHAMPION: tokens.live,
        LabStatus.GATE_PASS: tokens.up,
        LabStatus.FAIL_VALIDATION: tokens.down,
        LabStatus.FAIL_TRAIN: tokens.down,
    }.get(status, tokens.muted)


def _mark_text(mark: Mark | None, tokens: Tokens) -> Text:
    if mark is None:
        return Text("·", style=tokens.dim)
    colour = {Mark.PASS: tokens.up, Mark.FAIL: tokens.down}.get(mark, tokens.dim)
    return Text(mark.value, style=colour)


def _heading(label: str, tokens: Tokens) -> Text:
    return Text(label, style=f"bold {tokens.live}")


def _bullets(lines: Sequence[str], tokens: Tokens, *, empty: str) -> Text:
    out = Text()
    if not lines:
        out.append(empty, style=tokens.dim)
        return out
    for index, line in enumerate(lines):
        if index:
            out.append("\n")
        out.append("• ", style=tokens.dim)
        out.append(line)
    return out


def strategy_text(strategy: LabStrategy, tokens: Tokens) -> Text:
    """Return the Strategy panel: brief, rules, hypothesis and parameters.

    Args:
        strategy: The selected strategy.
        tokens: Token sheet.

    Returns:
        The strategy's id, family and status, then BRIEF (the batch summary),
        RULES (entry, exit and common rules from its card), HYPOTHESIS
        (expected sign and when it is rejected) and PARAMETERS (signed base,
        neighbours and the values the runs used; an amber ``⚠`` marks a run
        value that is neither the signed base nor a signed neighbour).
    """
    out = Text()
    out.append(strategy.strategy_id, style=f"bold {tokens.text}")
    out.append("  ")
    out.append(
        f"{_CHIPS[strategy.status][0]} {strategy.status.value}",
        _status_colour(strategy.status, tokens),
    )
    out.append(f"\nfamily {strategy.family or _NONE}", style=tokens.muted)
    out.append("\n\n")
    out.append_text(_heading("BRIEF", tokens))
    out.append("\n")
    out.append_text(_bullets(strategy.brief, tokens, empty="No summary recorded."))
    out.append("\n\n")
    out.append_text(_heading("RULES", tokens))
    out.append("\n")
    out.append_text(_bullets(strategy.rules, tokens, empty="No card with rules is committed."))
    out.append("\n\n")
    out.append_text(_heading("HYPOTHESIS", tokens))
    out.append("\nexpects\n", style=tokens.muted)
    out.append_text(_bullets(strategy.expects, tokens, empty="Not recorded."))
    out.append("\nrejected if\n", style=tokens.muted)
    out.append_text(_bullets(strategy.rejected_if, tokens, empty="Not recorded."))
    out.append("\n\n")
    out.append_text(_heading("PARAMETERS", tokens))
    out.append("\n")
    if not strategy.parameters:
        out.append("No signed parameters recorded.", style=tokens.dim)
        return out
    for index, row in enumerate(strategy.parameters):
        if index:
            out.append("\n")
        out.append(f"{row.name:<22}", style=tokens.text)
        out.append(f" signed {row.base if row.base is not None else _NONE}", style=tokens.muted)
        if row.neighbours:
            out.append(f" · neighbours {', '.join(row.neighbours)}", style=tokens.dim)
        if row.as_run:
            out.append(f" · as run {', '.join(row.as_run)}", style=tokens.muted)
        if row.mismatch:
            out.append(" ⚠", style=f"bold {tokens.warn}")
            out.append(" differs from signed", style=tokens.warn)
    return out


def _verdict_for(strategy: LabStrategy, stage: str) -> StageVerdict | None:
    return next((v for v in strategy.verdicts if v.stage == stage), None)


def _check_mark(verdict: StageVerdict | None, check: str) -> Mark | None:
    if verdict is None:
        return None
    return next((c.status for c in verdict.checks if c.check == check), None)


def results_text(strategy: LabStrategy, tokens: Tokens, tz: tzinfo, *, reveal: bool) -> Text:
    """Return the Results panel: the gate, the runs and the links.

    Args:
        strategy: The selected strategy.
        tokens: Token sheet.
        tz: Display timezone for the run times.
        reveal: Show the trial log's figures on each run. Thresholds and
            PASS/FAIL show either way.

    Returns:
        GATE: the 17 checks in the fixed order, each with its Train and
        Validation verdict and its signed threshold, then the batch's
        ``bh_survivor`` and ``champion`` rows; RUNS: when, split, variant,
        window, commit and the hash chain (and the figures when ``reveal``);
        LINKS: the batch, the card, the verdict records and the notebook.
    """
    out = Text()
    out.append_text(_heading(f"{'GATE':<20}", tokens))
    out.append(" Train  Valid  threshold\n", style=tokens.dim)
    if not strategy.validation_run:
        out.append("Validation not run: Train did not let it go on.\n", style=tokens.dim)
    train, validation = _verdict_for(strategy, "train"), _verdict_for(strategy, "validation")
    seen = set(GATE_CHECKS)
    extra = tuple(
        c.check
        for v in (train, validation)
        if v is not None
        for c in v.checks
        if c.check not in seen and c.check not in ("bh_survivor", "champion")
    )
    for check in (*GATE_CHECKS, *dict.fromkeys(extra)):
        out.append(f"{check:<20} ")
        out.append_text(_mark_text(_check_mark(train, check), tokens))
        out.append("   " if _check_mark(train, check) is None else "  ")
        out.append_text(_mark_text(_check_mark(validation, check), tokens))
        out.append(f"   {strategy.thresholds.get(check, _NONE)}\n", style=tokens.muted)
    survivor = _check_mark(validation, "bh_survivor")
    out.append(f"{'bh_survivor':<20} ")
    out.append_text(_mark_text(survivor, tokens))
    out.append("\n")
    champion = None
    if validation is not None:
        champion = Mark.PASS if validation.champion else Mark.FAIL
    out.append(f"{'champion':<20} ")
    out.append_text(_mark_text(champion, tokens))
    out.append("\n\n")
    out.append_text(_heading("RUNS", tokens))
    out.append("\n")
    if not strategy.runs:
        out.append("No run recorded yet.", style=tokens.dim)
    for run in strategy.runs:
        out.append(f"{run.at.astimezone(tz):%m-%d %H:%M} ", style=tokens.muted)
        out.append(f"{run.split:<10} {run.variant:<18} ")
        out.append(f"{run.window[0]}..{run.window[1]} ", style=tokens.dim)
        out.append(f"{run.commit} #{run.digest}", style=tokens.dim)
        out.append(
            " ⛓" if run.chained else " ✗chain", style=tokens.dim if run.chained else tokens.warn
        )
        if reveal and run.values:
            out.append(
                "  " + " ".join(f"{k} {v}" for k, v in run.values.items()), style=tokens.text
            )
        out.append("\n")
    out.append("\n")
    out.append_text(_heading("LINKS", tokens))
    out.append("\n")
    links = (
        ("batch", strategy.document),
        ("card", strategy.card),
        (
            "verdicts",
            f"verdicts/{strategy.batch_id}/" if strategy.verdicts and strategy.batch_id else None,
        ),
        ("notebook", strategy.notebook),
    )
    for name, target in links:
        out.append(f"{name:<9}", style=tokens.muted)
        out.append(f"{target or _NONE}\n", style=tokens.text if target else tokens.dim)
    out.rstrip()
    return out


def view_bar(view: str, *, values: str, tokens: Tokens) -> Text:
    """Return the line above the screen: the two views and the values tag.

    Args:
        view: The active view (one of :data:`VIEWS`).
        values: ``shown``, ``hidden``, ``off`` (not enabled) or ``ai`` (an AI session).
        tokens: Token sheet.

    Returns:
        ``Backtests  Drafts  [ ] view`` and, after it, an amber
        ``● values shown · V hides`` or a dim ``○ values hidden`` with the reason.
    """
    out = Text()
    for name in VIEWS:
        if name == view:
            out.append(f" {name} ", style=f"reverse {tokens.live}")
        else:
            out.append(f" {name} ", style=tokens.muted)
        out.append(" ")
    out.append("[ ] switch    ", style=tokens.dim)
    if values == "shown":
        out.append("● values shown · V hides", style=tokens.warn)
    else:
        reason = {"ai": " · AI session", "hidden": " · V shows"}.get(values, "")
        out.append(f"○ values hidden{reason}", style=tokens.dim)
    return out


class LabView(Vertical, can_focus=True):
    """The Lab screen: view bar, filter bar, list, strategy and results.

    Args:
        reader: Reads the committed experiment files; ``None`` when this
            machine has no clone.
        now: The current time (``since:`` ends there).
        tz: Returns the display timezone.
        ai_session: The session is an AI session; values stay off.
        id: Widget id.
    """

    DEFAULT_CSS = """
    LabView { height: 1fr; }
    LabView > #lab-viewbar { height: 1; padding: 0 1; }
    LabView > #lab-drafts { display: none; height: 1fr; padding: 1 2; }
    LabView.-drafts > #lab-drafts { display: block; }
    LabView.-drafts > #lab-body { display: none; }
    LabView > #lab-body { height: 1fr; layout: grid; grid-size: 1; grid-rows: 1fr; }
    LabView #lab-body > HudPanel { height: 1fr; }
    LabView #lab-body > HudPanel > .hud-body { height: 1fr; }
    LabView #lab-list-panel > QueryList { height: 1fr; }
    LabView #lab-strategy-panel, LabView #lab-results-panel { display: none; }
    LabView #lab-strategy, LabView #lab-results { height: 1fr; overflow-y: auto; }
    LabView.-standard #lab-strategy-panel, LabView.-standard #lab-results-panel,
    LabView.-wide #lab-strategy-panel, LabView.-wide #lab-results-panel,
    LabView.-ultra #lab-strategy-panel, LabView.-ultra #lab-results-panel { display: block; }
    LabView.-standard #lab-body { grid-size: 2 2; grid-rows: 2fr 3fr; grid-gutter: 0 1; }
    LabView.-standard #lab-list-panel { column-span: 2; }
    LabView.-wide #lab-body {
        grid-size: 2 2; grid-columns: 3fr 2fr; grid-rows: 1fr 1fr; grid-gutter: 0 1;
    }
    LabView.-wide #lab-results-panel { row-span: 2; }
    LabView.-ultra #lab-body { grid-size: 3 1; grid-columns: 3fr 2fr 2fr; grid-gutter: 0 1; }
    """
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("left_square_bracket", "view(-1)", "view", show=False),
        Binding("right_square_bracket", "view(1)", "view", show=False),
        Binding("v,V", "toggle_values", "values", show=False),
        Binding("slash", "focus_query", "filter", show=False),
    ]

    def __init__(
        self,
        *,
        reader: LabReader | None,
        now: Callable[[], datetime],
        tz: Callable[[], tzinfo],
        ai_session: bool,
        id: str | None = None,
    ) -> None:
        """Remember the source; nothing is read before the screen is shown."""
        super().__init__(id=id)
        self._reader = reader
        self._now = now
        self._tz = tz
        self._ai_session = ai_session
        self.snapshot: LabSnapshot | None = None
        self._by_id: dict[str, LabStrategy] = {}
        self._shown: tuple[Row, ...] = ()
        self.view = VIEWS[0]
        self.reveal = False
        self.layout_class = "-compact"

    def compose(self) -> ComposeResult:
        """Lay out the view bar, then the list with the two panels, or the drafts note."""
        yield Static(id="lab-viewbar")
        with Container(id="lab-body"):
            yield HudPanel(
                QueryList(
                    (),
                    LAB_COLUMNS,
                    fields=LAB_QUERY_FIELDS,
                    facets=LAB_FACETS,
                    sort=(SortKey("last", descending=True),),
                    now=self._now,
                    placeholder=_PLACEHOLDER,
                    id="lab-list",
                ),
                title="Backtests",
                subtitle="↑↓ select · ↵ open · / filter · S sort",
                id="lab-list-panel",
            )
            yield HudPanel(
                Static(id="lab-strategy"),
                title="Strategy",
                subtitle="signed parameters against as run",
                id="lab-strategy-panel",
            )
            yield HudPanel(
                Static(id="lab-results"),
                title="Results",
                subtitle="thresholds always · values only when shown",
                id="lab-results-panel",
            )
        yield Static(id="lab-drafts")

    def on_mount(self) -> None:
        """Redraw the colours when the theme changes."""
        self.app.theme_changed_signal.subscribe(self, lambda _theme: self._paint())
        self._arrange(lab_layout(self.app.size.width))
        self._paint()

    def on_resize(self, event: events.Resize) -> None:
        """Pick the layout for the new width."""
        del event
        self._arrange(lab_layout(self.app.size.width))
        self._paint()

    def _arrange(self, layout: str) -> None:
        """Set the layout class; wide puts the results before the strategy for the grid."""
        for name in ("-compact", "-standard", "-wide", "-ultra"):
            self.set_class(name == layout, name)
        self.layout_class = layout
        results = self.query_one("#lab-results-panel", HudPanel)
        strategy = self.query_one("#lab-strategy-panel", HudPanel)
        order = [w.id for w in self.query_one("#lab-body").children]
        want_results_first = layout == "-wide"
        results_first = order.index("lab-results-panel") < order.index("lab-strategy-panel")
        if want_results_first and not results_first:
            self.query_one("#lab-body").move_child(results, before=strategy)
        elif not want_results_first and results_first:
            self.query_one("#lab-body").move_child(results, after=strategy)

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    def load(self) -> None:
        """Read the committed files again and show them (the screen was opened)."""
        self.snapshot = None if self._reader is None else self._reader.read()
        self.reveal = self.snapshot is not None and self.snapshot.values_included
        strategies = () if self.snapshot is None else self.snapshot.strategies
        self._by_id = {s.strategy_id: s for s in strategies}
        self.query_one(QueryList).set_rows(lab_rows(strategies, self._tz()))
        self._paint()

    @property
    def values_state(self) -> str:
        """Return ``shown``, ``hidden``, ``off`` or ``ai``: why values do or do not show."""
        if self._ai_session:
            return "ai"
        if self.snapshot is None or not self.snapshot.values_included:
            return "off"
        return "shown" if self.reveal else "hidden"

    # ------------------------------------------------------------------
    # Drawing
    # ------------------------------------------------------------------

    def on_query_list_refreshed(self, event: QueryList.Refreshed) -> None:
        """Redraw the panels from the new result."""
        event.stop()
        self._shown = event.shown
        self._paint()

    def on_data_table_cell_highlighted(self, event: DataTable.CellHighlighted) -> None:
        """Follow the cursor with the panels."""
        event.stop()
        self._paint_panels()

    @property
    def table(self) -> SortableTable:
        """Return the list's table."""
        return self.query_one(SortableTable)

    def selected(self) -> LabStrategy | None:
        """Return the strategy under the table cursor, if any."""
        row = self.table.cursor_row
        if not 0 <= row < len(self._shown):
            return None
        return self._by_id.get(str(self._shown[row].get("strategy")))

    def _empty_text(self) -> str:
        if self.snapshot is None:
            return _NO_FILES
        return "No strategies match the filter." if self._shown else "No strategies yet."

    def _paint(self) -> None:
        if not self.is_mounted:
            return
        tokens = tokens_for(self.app.theme)
        self.set_class(self.view == VIEWS[1], "-drafts")
        self.query_one("#lab-viewbar", Static).update(
            view_bar(self.view, values=self.values_state, tokens=tokens)
        )
        self.query_one("#lab-drafts", Static).update(Text(_DRAFTS, style=tokens.muted))
        self._paint_panels()

    def _paint_panels(self) -> None:
        if not self.is_mounted:
            return
        tokens = tokens_for(self.app.theme)
        strategy = self.selected()
        strat = self.query_one("#lab-strategy", Static)
        res = self.query_one("#lab-results", Static)
        if strategy is None:
            note = Text(self._empty_text(), style=tokens.muted)
            strat.update(note)
            res.update(Text(""))
            return
        strat.update(strategy_text(strategy, tokens))
        res.update(results_text(strategy, tokens, self._tz(), reveal=self.reveal))

    # ------------------------------------------------------------------
    # Opening a strategy on a compact terminal
    # ------------------------------------------------------------------

    def _keys(self) -> list[str]:
        return [str(row.get("strategy")) for row in self._shown]

    def _body(self, key: str) -> RenderableType:
        tokens = tokens_for(self.app.theme)
        strategy = self._by_id.get(key)
        if strategy is None:
            return Text("This strategy left the list.", style=tokens.muted)
        return Group(
            strategy_text(strategy, tokens),
            Text(""),
            results_text(strategy, tokens, self._tz(), reveal=self.reveal),
        )

    def list_state(self) -> ListState:
        """Return the list's sort and cursor (its query stays in the bar)."""
        sort = self.table.sort_keys[:1]
        strategy = self.selected()
        return ListState(
            sort=sort[0].field if sort else None,
            descending=bool(sort and sort[0].descending),
            cursor=None if strategy is None else strategy.strategy_id,
        )

    def restore_list_state(self, state: ListState | None) -> None:
        """Put the cursor on the strategy shown last.

        Args:
            state: What the detail screen returned; ``None`` changes nothing.
        """
        if state is None or state.cursor is None:
            return
        keys = self._keys()
        if state.cursor in keys:
            self.table.move_cursor(row=keys.index(state.cursor), animate=False)
        self.table.focus()

    def on_data_table_cell_selected(self, event: DataTable.CellSelected) -> None:
        """Enter opens the strategy as a screen when compact; else the panels already show it."""
        event.stop()
        strategy = self.selected()
        if strategy is None or detail_mode(self.app.size.width) is DetailMode.PANE:
            return
        self.app.push_screen(
            DetailScreen(
                trail=_TRAIL,
                keys=self._keys,
                current=strategy.strategy_id,
                body=self._body,
                title="Strategy",
                state=self.list_state(),
                chips=DETAIL_CHIPS,
            ),
            self.restore_list_state,
        )

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def action_view(self, step: int) -> None:
        """Switch between Backtests and Drafts.

        Args:
            step: ``-1`` or ``1``.
        """
        self.view = VIEWS[(VIEWS.index(self.view) + step) % len(VIEWS)]
        self._paint()
        if self.view == VIEWS[0]:
            self.table.focus()
        else:
            self.focus()

    def action_toggle_values(self) -> None:
        """Hide or show the trial log's figures; nothing happens when none were read."""
        if self.values_state not in ("shown", "hidden"):
            return
        self.reveal = not self.reveal
        self._paint()

    def action_focus_query(self) -> None:
        """Move to the filter line."""
        self.query_one("#query-input").focus()
