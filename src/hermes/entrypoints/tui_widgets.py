"""Reusable Cockpit console widgets (design spec, issue #711).

One implementation each, styled only through :mod:`hermes.entrypoints.tui_theme`:

- :class:`HudPanel`: the frame every panel sits in (bracket corners, gradient
  title bar, "computed" or "✦ AI supervisor" label, key-hint subtitle, focus
  state, AI variant);
- :class:`WindowBar`: "▐ HERMES // SHADOW CONSOLE" with a ticking binary strip
  and the clock;
- :class:`TabBar`: the screen tabs, numbers only at compact width;
- :class:`ThemeChip` and :class:`ThemePicker`: the theme switch;
- :func:`key_chips`: reverse-video key chips for the footer.

The text of each piece is built by a pure function so tests can check it
without running an app.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import ClassVar, Final

from rich.cells import cell_len
from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Vertical
from textual.message import Message
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from hermes.entrypoints.tui_theme import (
    AI_GLYPH,
    CORNER_BOTTOM_RIGHT,
    CORNER_TOP_LEFT,
    EDGE,
    WINDOW_MARK,
    WINDOW_TITLE,
    ThemeChoice,
    Tokens,
    binary_strip,
    gradient,
    tokens_for,
)

__all__ = [
    "LABEL_AI",
    "LABEL_COMPUTED",
    "HudPanel",
    "Tab",
    "TabBar",
    "ThemeChip",
    "ThemePicker",
    "WindowBar",
    "bottom_edge",
    "key_chips",
    "tab_text",
    "theme_chip_text",
    "top_edge",
    "window_bar_text",
]

LABEL_COMPUTED: Final = "computed"
LABEL_AI: Final = f"{AI_GLYPH} AI supervisor"
_CLOCK_PAD: Final = 1


def _gradient_text(text: str, stops: Sequence[str], fg: str) -> Text:
    """Return ``text`` on a left-to-right gradient background, one colour per cell."""
    out = Text()
    last = max(len(text) - 1, 1)
    for index, char in enumerate(text):
        out.append(char, style=f"bold {fg} on {gradient(stops, index / last)}")
    return out


def top_edge(
    title: str, label: str, width: int, tokens: Tokens, *, ai: bool = False, active: bool = False
) -> Text:
    """Return a panel's top edge: corner, title bar, rule, producer label.

    Args:
        title: Panel title; drawn in upper case.
        label: Who produced the panel ("computed" or "✦ AI supervisor").
        width: Columns to fill.
        tokens: Token sheet.
        ai: The AI variant: ✦ in the title, AI colours.
        active: Focused: the title becomes a solid ``live`` chip.

    Returns:
        Exactly ``width`` cells (shorter only when ``width`` is tiny).
    """
    accent = tokens.ai if ai else tokens.live
    head = f" {AI_GLYPH} {title.upper()} " if ai else f" {title.upper()} "
    out = Text(CORNER_TOP_LEFT, style=f"bold {accent}")
    if active:
        out.append(head, style=f"bold {tokens.void} on {accent}")
    else:
        out.append_text(
            _gradient_text(head, tokens.ai_gradient if ai else tokens.chrome, tokens.void)
        )
    tail = f" {label} "
    rule = width - out.cell_len - 1 - cell_len(tail)
    if rule < 1:
        tail, rule = "", width - out.cell_len - 1
    out.append(" ")
    out.append(EDGE * max(rule, 0), style=tokens.focus_border if active else tokens.line)
    out.append(tail, style=tokens.ai if ai else tokens.muted)
    out.truncate(max(width, 0))
    return out


def bottom_edge(subtitle: str, width: int, tokens: Tokens, *, ai: bool = False) -> Text:
    """Return a panel's bottom edge: key hints, rule, the bottom-right corner.

    Args:
        subtitle: Key hints, for example ``D W M Y range``; may be empty.
        width: Columns to fill.
        tokens: Token sheet.
        ai: The AI variant (the corner is ``ai`` either way; kept for symmetry).

    Returns:
        Exactly ``width`` cells (shorter only when ``width`` is tiny).
    """
    del ai
    out = Text()
    if subtitle:
        out.append(f"  {subtitle} ", style=tokens.dim)
    rule = width - out.cell_len - cell_len(CORNER_BOTTOM_RIGHT)
    out.append(EDGE * max(rule, 0), style=tokens.line)
    out.append(CORNER_BOTTOM_RIGHT, style=f"bold {tokens.ai}")
    out.truncate(max(width, 0))
    return out


class HudPanel(Vertical):
    """The frame every console panel sits in.

    Args:
        *children: The panel's content widgets.
        title: Panel title.
        label: Producer label; ``computed`` unless ``ai``.
        subtitle: Key hints drawn on the bottom edge.
        ai: The AI-supervisor variant (✦, AI colours, ``✦ AI supervisor`` label).
        id: Widget id.
        classes: Extra CSS classes.
    """

    DEFAULT_CSS = """
    HudPanel { height: auto; background: $hud-panel; }
    HudPanel > .hud-edge { height: 1; padding: 0; }
    HudPanel > .hud-body { height: auto; padding: 0 1; }
    HudPanel.-active { background: $hud-panel-focus; }
    """

    active: reactive[bool] = reactive(default=False)

    def __init__(
        self,
        *children: Widget,
        title: str,
        label: str | None = None,
        subtitle: str = "",
        ai: bool = False,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        """Remember the content and the frame texts."""
        super().__init__(id=id, classes=classes)
        self._content_widgets = children
        self.panel_title = title
        self.label = label if label is not None else (LABEL_AI if ai else LABEL_COMPUTED)
        self.subtitle = subtitle
        self.ai = ai
        self.set_class(ai, "-ai")

    def compose(self) -> ComposeResult:
        """Lay out the top edge, the content and the bottom edge."""
        yield Static(classes="hud-edge hud-top")
        for child in self._content_widgets:
            child.add_class("hud-body")
            yield child
        yield Static(classes="hud-edge hud-bottom")

    def on_mount(self) -> None:
        """Draw the edges and redraw them when the theme changes."""
        self.app.theme_changed_signal.subscribe(self, lambda _theme: self.paint())
        self.paint()

    def on_resize(self, event: events.Resize) -> None:
        """Redraw the edges at the new width."""
        del event
        self.paint()

    def watch_active(self, active: bool) -> None:
        """Show the focus state."""
        self.set_class(active, "-active")
        self.paint()

    def paint(self) -> None:
        """Redraw both edges from the current theme's tokens."""
        if not self.is_mounted:
            return
        tokens = tokens_for(self.app.theme)
        width = self.size.width
        for top in self.query(".hud-top").results(Static):
            top.update(
                top_edge(
                    self.panel_title, self.label, width, tokens, ai=self.ai, active=self.active
                )
            )
        for bottom in self.query(".hud-bottom").results(Static):
            bottom.update(bottom_edge(self.subtitle, width, tokens, ai=self.ai))


def window_bar_text(
    width: int, tokens: Tokens, *, tick: int, clock: str, strip: bool = True
) -> Text:
    """Return the window bar: brand mark, binary strip, clock at the right.

    Args:
        width: Columns to fill.
        tokens: Token sheet.
        tick: Binary-strip step.
        clock: Local time shown at the right (``HH:MM:SS``), or empty.
        strip: Draw the binary strip (off in night mode).

    Returns:
        At most ``width`` cells.
    """
    out = _gradient_text(f"{WINDOW_MARK} {WINDOW_TITLE} ", tokens.chrome, tokens.void)
    if strip:
        out.append(" ")
        out.append(binary_strip(tick), style=tokens.frame)
    right = Text(f"{clock}{' ' * _CLOCK_PAD}" if clock else "", style=tokens.muted)
    gap = width - out.cell_len - right.cell_len
    if gap > 0:
        out.append(" " * gap)
        out.append_text(right)
    out.truncate(max(width, 0))
    return out


class WindowBar(Static):
    """The Neon window bar; the app sets ``tick`` and ``clock``."""

    tick: reactive[int] = reactive(0)
    clock: reactive[str] = reactive("")
    strip: reactive[bool] = reactive(default=True)

    def on_mount(self) -> None:
        """Redraw when the theme changes."""
        self.app.theme_changed_signal.subscribe(self, lambda _theme: self.paint())
        self.paint()

    def on_resize(self, event: events.Resize) -> None:
        """Redraw at the new width."""
        del event
        self.paint()

    def watch_tick(self) -> None:
        """Advance the strip."""
        self.paint()

    def watch_clock(self) -> None:
        """Show the new time."""
        self.paint()

    def watch_strip(self) -> None:
        """Show or hide the strip."""
        self.paint()

    def paint(self) -> None:
        """Redraw from the current theme's tokens."""
        if self.is_mounted:
            self.update(
                window_bar_text(
                    self.size.width,
                    tokens_for(self.app.theme),
                    tick=self.tick,
                    clock=self.clock,
                    strip=self.strip,
                )
            )


@dataclass(frozen=True)
class Tab:
    """One screen tab.

    Attributes:
        key: Number key that jumps to it.
        name: Screen name.
        issue: The issue that builds it; ``None`` when it is built.
    """

    key: str
    name: str
    issue: int | None = None


def tab_text(
    tabs: Sequence[Tab], active: int, tokens: Tokens, *, compact: bool
) -> tuple[Text, list[tuple[int, int]]]:
    """Return the tab bar and the cell range of each tab (for clicks).

    Args:
        tabs: The tabs, in order.
        active: Index of the shown tab.
        tokens: Token sheet.
        compact: Numbers only.

    Returns:
        The text and one ``(start, end)`` cell range per tab.
    """
    out = Text(" ")
    ranges: list[tuple[int, int]] = []
    for index, tab in enumerate(tabs):
        if index:
            out.append(" " if compact else "·", style=tokens.dim)
        label = f" {tab.key} " if compact else f" {tab.key} {tab.name} "
        start = out.cell_len
        if index == active:
            out.append(label, style=f"bold {tokens.void} on {tokens.live}")
        elif compact:
            out.append(label, style=tokens.muted)
        else:
            out.append(f" {tab.key} ", style=f"bold {tokens.frame}")
            out.append(f"{tab.name} ", style=tokens.muted)
        ranges.append((start, out.cell_len))
    return out, ranges


class TabBar(Static):
    """The screen tabs; a click posts :class:`TabBar.Picked`.

    Args:
        tabs: The tabs, in order.
        id: Widget id.
    """

    class Picked(Message):
        """A tab was clicked."""

        def __init__(self, index: int) -> None:
            """Remember which tab."""
            super().__init__()
            self.index = index

    active: reactive[int] = reactive(0)
    compact: reactive[bool] = reactive(default=False)

    def __init__(self, tabs: Sequence[Tab], *, id: str | None = None) -> None:
        """Remember the tabs."""
        super().__init__(id=id)
        self._tabs = tuple(tabs)
        self._ranges: list[tuple[int, int]] = []

    def on_mount(self) -> None:
        """Redraw when the theme changes."""
        self.app.theme_changed_signal.subscribe(self, lambda _theme: self.paint())
        self.paint()

    def watch_active(self) -> None:
        """Highlight the new tab."""
        self.paint()

    def watch_compact(self) -> None:
        """Switch between names and numbers."""
        self.paint()

    def paint(self) -> None:
        """Redraw from the current theme's tokens."""
        if self.is_mounted:
            text, self._ranges = tab_text(
                self._tabs, self.active, tokens_for(self.app.theme), compact=self.compact
            )
            self.update(text)

    def on_click(self, event: events.Click) -> None:
        """Pick the tab under the pointer."""
        for index, (start, end) in enumerate(self._ranges):
            if start <= event.x < end:
                self.post_message(self.Picked(index))
                return


_CHOICE_LABELS: Final[dict[ThemeChoice, str]] = {
    ThemeChoice.NEON: "NEON",
    ThemeChoice.CONTROL: "CONTROL ROOM",
    ThemeChoice.AUTO: "AUTO",
}


def theme_chip_text(
    choice: ThemeChoice, tokens: Tokens, *, night: bool, compact: bool = False
) -> Text:
    """Return the theme chip shown next to the live dot.

    Args:
        choice: The operator's choice.
        tokens: Token sheet.
        night: Auto is showing the night theme now.
        compact: Compact width: the glyph alone.

    Returns:
        `` ◑ NEON ``, `` ◑ CONTROL ROOM ``, `` ◑ AUTO ``, `` ◑ AUTO · NIGHT ``,
        or `` ◑ `` at compact width.
    """
    label = _CHOICE_LABELS[choice]
    if choice is ThemeChoice.AUTO and night:
        label += " · NIGHT"
    text = " ◑ " if compact else f" ◑ {label} "
    return Text(text, style=f"reverse {tokens.frame}")


class ThemeChip(Static):
    """The theme chip; a click posts :class:`ThemeChip.Pressed`."""

    class Pressed(Message):
        """The chip was clicked."""

    def on_click(self, event: events.Click) -> None:
        """Ask the app to open the theme picker."""
        del event
        self.post_message(self.Pressed())


_PICKER_OPTIONS: Final = (
    (ThemeChoice.NEON, "Neon"),
    (ThemeChoice.CONTROL, "Control room"),
    (ThemeChoice.AUTO, "Auto · Neon by day, night 23:00-08:00"),
)


class ThemePicker(ModalScreen[ThemeChoice | None]):
    """Pick Neon, Control room or Auto; Esc keeps the current choice.

    Args:
        current: The choice to highlight first.
    """

    DEFAULT_CSS = """
    ThemePicker { align: center middle; }
    ThemePicker > Vertical {
        width: 46; height: auto; background: $hud-overlay; border: round $hud-frame;
        padding: 0 1;
    }
    ThemePicker OptionList { height: auto; background: $hud-overlay; border: none; }
    """
    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "cancel", "back")]

    def __init__(self, current: ThemeChoice) -> None:
        """Remember the current choice."""
        super().__init__()
        self._current = current

    def compose(self) -> ComposeResult:
        """Lay out the title and the three choices."""
        with Vertical():
            yield Static("Theme")
            yield OptionList(*(Option(text, id=choice.value) for choice, text in _PICKER_OPTIONS))

    def on_mount(self) -> None:
        """Highlight the current choice."""
        options = self.query_one(OptionList)
        options.highlighted = [choice for choice, _ in _PICKER_OPTIONS].index(self._current)
        options.focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Return the picked choice."""
        option_id = event.option.id
        self.dismiss(ThemeChoice(option_id) if option_id else None)

    def action_cancel(self) -> None:
        """Close without changing the theme."""
        self.dismiss(None)


def key_chips(chips: Sequence[tuple[str, str]], tokens: Tokens) -> Text:
    """Return footer key chips: each key in reverse video, then its label.

    Args:
        chips: ``(key, label)`` pairs; an empty label draws the chip alone.
        tokens: Token sheet.

    Returns:
        `` D W M Y range  ↵ detail …`` with every key reversed.
    """
    out = Text()
    for index, (key, label) in enumerate(chips):
        if index:
            out.append("  ")
        out.append(f" {key} ", style=f"reverse {tokens.muted}")
        if label:
            out.append(label, style=tokens.muted)
    return out
