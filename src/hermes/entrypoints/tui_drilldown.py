"""Shared drill-down for the Cockpit console (design spec, issue #730).

One implementation each, styled only through :mod:`hermes.entrypoints.tui_theme`
and built on :mod:`hermes.entrypoints.tui_widgets`:

- :class:`DetailPane`: the inline detail beside a list (a :class:`HudPanel`
  that opens and closes);
- :class:`DetailScreen`: the full-screen detail, with a :class:`Breadcrumb`
  as its top bar (``◂ System › Releases › 2bc2798``), back on Esc or
  Backspace, previous / next on ``[`` / ``]`` and footer key chips;
- :class:`Breadcrumb`: the trail back to the list;
- :class:`TruncatedText`: one line cut at the panel edge with "…", or a list
  cut with "+N", and the full text one Enter away.

The rules (design spec, "Drill-down rules"):

- :func:`detail_mode` decides where a detail opens: a pane when it fits beside
  the list, a screen below :data:`~hermes.entrypoints.tui_theme.COMPACT_BELOW`
  columns or when the host has no room beside the list.
- A list that opens a :class:`DetailScreen` implements :class:`DrillList`: it
  hands over a :class:`ListState` (filters, sort, cursor) when the screen
  opens, and gets it back, with the cursor on the item shown last, when the
  screen closes. A refresh that rebuilt the list meanwhile is undone.
- ``[`` and ``]`` stop at the ends (:func:`step_item`): inside a detail the
  list is out of sight, so a wrap would jump from the last item to the first
  without the operator seeing it. The footer says where the item sits
  ("2 of 5").
- Number keys and Ctrl+P are the app's own bindings, so they keep working on
  top of a detail screen.

The text of each piece is built by a pure function so tests can check it
without running an app.
"""

# ruff: noqa: RUF002 - the single right angle quote is the design spec's breadcrumb separator

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import ClassVar, Final, Protocol

from rich.cells import cell_len
from rich.console import RenderableType
from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, VerticalScroll
from textual.reactive import reactive
from textual.screen import Screen
from textual.widget import Widget
from textual.widgets import Static

from hermes.entrypoints.tui_theme import (
    BACK_GLYPH,
    COMPACT_BELOW,
    CRUMB_SEPARATOR,
    ELLIPSIS,
    Tokens,
    tokens_for,
)
from hermes.entrypoints.tui_widgets import HudPanel, key_chips

__all__ = [
    "DETAIL_CHIPS",
    "ITEM_SEPARATOR",
    "Breadcrumb",
    "DetailMode",
    "DetailPane",
    "DetailScreen",
    "DrillList",
    "ListState",
    "TruncatedText",
    "breadcrumb_text",
    "detail_mode",
    "item_position",
    "step_item",
    "truncate_items",
    "truncate_line",
    "truncated_text",
]

ITEM_SEPARATOR: Final = " · "
DETAIL_CHIPS: Final = (
    ("Esc", "back"),
    ("[ ]", "prev/next"),
    ("^P", "palette"),
    ("Q", "quit"),
)
_READ_ONLY: Final = "read-only"


class DetailMode(StrEnum):
    """Where a detail opens."""

    PANE = "pane"
    SCREEN = "screen"


def detail_mode(width: int, *, beside: bool = True) -> DetailMode:
    """Return where a detail that fits beside its list opens.

    Args:
        width: Terminal width in columns.
        beside: The host has room to place the pane beside the list at this
            width (Home splits into two columns only from 120 until issue #712).

    Returns:
        :attr:`DetailMode.SCREEN` on compact terminals (below 100 columns) or
        when there is no room beside the list, else :attr:`DetailMode.PANE`.
    """
    if width < COMPACT_BELOW or not beside:
        return DetailMode.SCREEN
    return DetailMode.PANE


@dataclass(frozen=True)
class ListState:
    """What a list hands a detail screen and gets back when the screen closes.

    Attributes:
        filters: ``(field, value)`` pairs as the query bar holds them
            (``coin:BTC``, ``since:7d``; free text under the field ``text``).
        sort: The column the list is sorted by; ``None`` for its natural order.
        descending: Sorted from the highest value down.
        cursor: Key of the selected item; ``None`` when nothing is selected.
    """

    filters: tuple[tuple[str, str], ...] = ()
    sort: str | None = None
    descending: bool = False
    cursor: str | None = None

    def at(self, key: str) -> ListState:
        """Return the same state with the cursor on ``key``."""
        return replace(self, cursor=key)


class DrillList(Protocol):
    """A list whose items open in a :class:`DetailScreen`."""

    def list_state(self) -> ListState:
        """Return the list's filters, sort and cursor."""
        ...

    def restore_list_state(self, state: ListState | None) -> None:
        """Put back the filters, sort and cursor; ``None`` leaves the list as it is."""
        ...


def step_item(keys: Sequence[str], current: str, step: int) -> str:
    """Return the item ``[`` (``-1``) or ``]`` (``1``) moves to; it stops at the ends.

    Args:
        keys: The list's item keys, in the order shown.
        current: The key shown now.
        step: ``-1`` for the previous item, ``1`` for the next.

    Returns:
        The neighbouring key; ``current`` at either end or when the list is
        empty; the first key when ``current`` left the list on a refresh.
    """
    if not keys:
        return current
    if current not in keys:
        return keys[0]
    index = keys.index(current) + step
    return keys[min(max(index, 0), len(keys) - 1)]


def item_position(keys: Sequence[str], current: str) -> str:
    """Return ``"2 of 5"`` for the shown item, or empty when it left the list."""
    if current not in keys:
        return ""
    return f"{keys.index(current) + 1} of {len(keys)}"


def _crop(text: str, width: int) -> str:
    """Return the longest start of ``text`` that fits ``width`` cells."""
    out: list[str] = []
    used = 0
    for char in text:
        used += cell_len(char)
        if used > width:
            break
        out.append(char)
    return "".join(out)


def truncate_line(text: str, width: int) -> str:
    """Return ``text`` as one line of at most ``width`` cells.

    Args:
        text: The full text; only its first line is shown.
        width: Cells available (the panel edge).

    Returns:
        The first line unchanged when it fits and nothing follows it, else
        its start ending in "…" (the "…" also marks hidden lines).
    """
    lines = text.splitlines()
    first = lines[0] if lines else ""
    if len(lines) <= 1 and cell_len(first) <= width:
        return first
    if width <= 0:
        return ""
    return _crop(first, width - cell_len(ELLIPSIS)).rstrip() + ELLIPSIS


def _cut_items(items: Sequence[str], width: int, separator: str) -> tuple[str, int]:
    """Return the shown part of a list and how many items it leaves out."""
    full = separator.join(items)
    if cell_len(full) <= width or len(items) <= 1:
        return truncate_line(full, width), 0
    for shown in range(len(items) - 1, 0, -1):
        head = separator.join(items[:shown])
        hidden = len(items) - shown
        if cell_len(head) + cell_len(f" +{hidden}") <= width:
            return head, hidden
    hidden = len(items) - 1
    budget = width - cell_len(f" +{hidden}")
    if budget < 1 + cell_len(ELLIPSIS):
        return truncate_line(full, width), 0
    return truncate_line(items[0], budget), hidden


def truncate_items(items: Sequence[str], width: int, *, separator: str = ITEM_SEPARATOR) -> str:
    """Return a list as one line of at most ``width`` cells, ending in "+N" when cut.

    Args:
        items: The items, in order.
        width: Cells available.
        separator: Drawn between items.

    Returns:
        ``BTC · ETH · SOL`` when it fits; else the items that fit whole and
        `` +N`` for the rest (``BTC · ETH +3``); when not even the first item
        fits, its start with "…" and the count (``a-very… +1``).
    """
    head, hidden = _cut_items(items, width, separator)
    return f"{head} +{hidden}" if hidden else head


def truncated_text(
    value: str | Sequence[str],
    width: int,
    tokens: Tokens,
    *,
    separator: str = ITEM_SEPARATOR,
) -> Text:
    """Return the collapsed :class:`TruncatedText`: the cut line, "+N" in the muted token.

    Args:
        value: One text, or a list of items.
        width: Cells available.
        tokens: Token sheet.
        separator: Drawn between list items.

    Returns:
        At most ``width`` cells.
    """
    if isinstance(value, str):
        return Text(truncate_line(value, width))
    head, hidden = _cut_items(value, width, separator)
    out = Text(head)
    if hidden:
        out.append(f" +{hidden}", style=tokens.muted)
    return out


def breadcrumb_text(trail: Sequence[str], tokens: Tokens, width: int | None = None) -> Text:
    """Return the top bar of a detail screen: ``◂ System › Releases › 2bc2798``.

    Args:
        trail: Screen, list, then the item shown (the last crumb).
        tokens: Token sheet.
        width: Cells available; ``None`` draws the whole trail.

    Returns:
        The back glyph in the live token, earlier crumbs muted, the item bold.
        When the trail does not fit, leading crumbs give way to "…" first, so
        the item stays whole as long as it can.
    """

    def build(parts: Sequence[str]) -> Text:
        out = Text()
        out.append(BACK_GLYPH, style=f"bold {tokens.live}")
        out.append(" ")
        for index, part in enumerate(parts):
            if index:
                out.append(CRUMB_SEPARATOR, style=tokens.dim)
            last = index == len(parts) - 1
            out.append(part, style=f"bold {tokens.text}" if last else tokens.muted)
        return out

    crumbs = list(trail)
    text = build(crumbs)
    dropped = 0
    while width is not None and text.cell_len > width and dropped < len(crumbs) - 1:
        dropped += 1
        text = build([ELLIPSIS, *crumbs[dropped:]])
    if width is not None and text.cell_len > width:
        text.truncate(max(width, 0), overflow="ellipsis")
    return text


class Breadcrumb(Static):
    """The breadcrumb top bar; set ``trail`` to show another item.

    Args:
        trail: Screen, list, then the item shown.
        id: Widget id.
    """

    DEFAULT_CSS = """
    Breadcrumb { height: 1; padding: 0 1; background: $hud-panel; }
    """

    trail: reactive[tuple[str, ...]] = reactive(())

    def __init__(self, trail: Sequence[str] = (), *, id: str | None = None) -> None:
        """Remember the trail."""
        super().__init__(id=id)
        self.set_reactive(Breadcrumb.trail, tuple(trail))

    def on_mount(self) -> None:
        """Redraw when the theme changes."""
        self.app.theme_changed_signal.subscribe(self, lambda _theme: self.paint())
        self.paint()

    def on_resize(self, event: events.Resize) -> None:
        """Redraw at the new width."""
        del event
        self.paint()

    def watch_trail(self) -> None:
        """Show the new trail."""
        self.paint()

    def paint(self) -> None:
        """Redraw from the current theme's tokens."""
        if self.is_mounted:
            width = self.content_size.width or None
            self.update(breadcrumb_text(self.trail, tokens_for(self.app.theme), width))


class TruncatedText(Static, can_focus=True):
    """One line cut at the panel edge with "…" (or a list with "+N"); Enter shows it all.

    Args:
        value: One text, or a list of items.
        separator: Drawn between list items.
        id: Widget id.
        classes: Extra CSS classes.
    """

    DEFAULT_CSS = """
    TruncatedText { height: auto; }
    TruncatedText:focus { background: $hud-panel-focus; }
    """
    BINDINGS: ClassVar[list[BindingType]] = [Binding("enter", "expand", "expand", show=False)]

    expanded: reactive[bool] = reactive(default=False)

    def __init__(
        self,
        value: str | Sequence[str] = "",
        *,
        separator: str = ITEM_SEPARATOR,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        """Remember the full text."""
        super().__init__(id=id, classes=classes)
        self._value: str | tuple[str, ...] = value if isinstance(value, str) else tuple(value)
        self._separator = separator

    @property
    def full_text(self) -> str:
        """Return the whole text (list items joined)."""
        value = self._value
        return value if isinstance(value, str) else self._separator.join(value)

    def set_value(self, value: str | Sequence[str]) -> None:
        """Show another text, keeping the expanded state.

        Args:
            value: One text, or a list of items.
        """
        self._value = value if isinstance(value, str) else tuple(value)
        self.paint()

    def on_mount(self) -> None:
        """Draw and redraw when the theme changes."""
        self.app.theme_changed_signal.subscribe(self, lambda _theme: self.paint())
        self.paint()

    def on_resize(self, event: events.Resize) -> None:
        """Cut again at the new width."""
        del event
        self.paint()

    def on_click(self, event: events.Click) -> None:
        """A click expands or folds, like Enter."""
        del event
        self.action_expand()

    def watch_expanded(self) -> None:
        """Show the full or the cut text."""
        self.paint()

    def action_expand(self) -> None:
        """Expand the full text, or fold it back to one line."""
        self.expanded = not self.expanded

    def paint(self) -> None:
        """Redraw the full text, or the cut line at the current width."""
        if not self.is_mounted:
            return
        width = self.content_size.width
        if self.expanded or width <= 0:
            self.update(Text(self.full_text))
            return
        self.update(
            truncated_text(
                self._value, width, tokens_for(self.app.theme), separator=self._separator
            )
        )


class DetailPane(HudPanel):
    """The inline detail beside a list: a :class:`HudPanel` shown only while open.

    Args:
        *children: The detail's content widgets.
        title: Panel title.
        subtitle: Key hints drawn on the bottom edge.
        id: Widget id.
        classes: Extra CSS classes.
    """

    DEFAULT_CSS = """
    DetailPane { display: none; }
    DetailPane.-open { display: block; }
    """

    is_open: reactive[bool] = reactive(default=False)

    def __init__(
        self,
        *children: Widget,
        title: str,
        subtitle: str = "Esc close",
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        """Remember the content and the frame texts."""
        super().__init__(*children, title=title, subtitle=subtitle, id=id, classes=classes)

    def watch_is_open(self, is_open: bool) -> None:
        """Show or hide the pane."""
        self.set_class(is_open, "-open")


class DetailScreen(Screen[ListState]):
    """A full-screen detail with a breadcrumb, back and previous / next.

    It returns the list's :class:`ListState` (cursor on the item shown last)
    when it closes with Esc or Backspace; pass the list's
    :meth:`DrillList.restore_list_state` as the ``push_screen`` callback.

    Args:
        trail: The crumbs before the item (``("System", "Releases")``).
        keys: Returns the list's item keys, in the order shown; re-read on every
            step and refresh so a closed item drops out.
        current: The key of the item to show first.
        body: Builds the detail of one item; called again on every refresh.
        title: The detail panel's title.
        state: The list's state when the screen opened.
        label: The item's crumb; the key itself when ``None``.
        chips: Footer key chips.
    """

    DEFAULT_CSS = """
    DetailScreen { background: $hud-void; }
    DetailScreen > #detail-scroll { height: 1fr; }
    DetailScreen > #detail-footer-row { height: 1; }
    DetailScreen #detail-footer { width: 1fr; }
    DetailScreen #detail-status { width: auto; margin-left: 1; color: $hud-muted; }
    """
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape,backspace", "back", "back"),
        Binding("left_square_bracket", "step(-1)", "previous"),
        Binding("right_square_bracket", "step(1)", "next"),
    ]

    def __init__(
        self,
        *,
        trail: Sequence[str],
        keys: Callable[[], Sequence[str]],
        current: str,
        body: Callable[[str], RenderableType],
        title: str,
        state: ListState,
        label: Callable[[str], str] | None = None,
        chips: Sequence[tuple[str, str]] = DETAIL_CHIPS,
    ) -> None:
        """Remember the list, the item and how to draw it."""
        super().__init__()
        self._trail = tuple(trail)
        self._keys = keys
        self._current = current
        self._body = body
        self._title = title
        self._state = state
        self._label = label or str
        self._chips = tuple(chips)

    @property
    def current(self) -> str:
        """Return the key of the item shown."""
        return self._current

    def compose(self) -> ComposeResult:
        """Lay out the breadcrumb, the framed detail and the footer."""
        yield Breadcrumb(self._crumbs(), id="breadcrumb")
        yield VerticalScroll(
            HudPanel(
                Static(id="detail-body"),
                title=self._title,
                subtitle="[ ] prev/next · Esc back",
            ),
            id="detail-scroll",
        )
        with Horizontal(id="detail-footer-row"):
            yield Static(id="detail-footer")
            yield Static(id="detail-status")

    def on_mount(self) -> None:
        """Fill the screen and repaint the footer when the theme changes."""
        self.app.theme_changed_signal.subscribe(self, lambda _theme: self.refresh_panel())
        self.refresh_panel()

    def _crumbs(self) -> tuple[str, ...]:
        return (*self._trail, self._label(self._current))

    def refresh_panel(self) -> None:
        """Redraw the breadcrumb, the detail and the footer from the source.

        The app's refresh timer can fire before compose; the parts not built
        yet are skipped here and filled by ``on_mount``.
        """
        for crumb in self.query("#breadcrumb").results(Breadcrumb):
            crumb.trail = self._crumbs()
        for body in self.query("#detail-body").results(Static):
            body.update(self._body(self._current))
        tokens = tokens_for(self.app.theme)
        for footer in self.query("#detail-footer").results(Static):
            footer.update(key_chips(self._chips, tokens))
        position = item_position(self._keys(), self._current)
        status = f"{position} · {_READ_ONLY}" if position else _READ_ONLY
        for label in self.query("#detail-status").results(Static):
            label.update(Text(status))

    def action_step(self, step: int) -> None:
        """Show the previous (``-1``) or next (``1``) item; stop at the ends.

        Args:
            step: Direction.
        """
        moved = step_item(self._keys(), self._current, step)
        if moved != self._current:
            self._current = moved
            self.refresh_panel()

    def action_back(self) -> None:
        """Close, handing the list its state back with the cursor on the item shown."""
        self.dismiss(self._state.at(self._current))
