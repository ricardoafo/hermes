"""Reusable Cockpit console widgets (design spec, issue #711).

One implementation each, styled only through :mod:`hermes.entrypoints.tui_theme`:

- :class:`BigNumber`: the big total in bold high-contrast digits over a
  gradient rule that shimmers; it counts up on a change (issue #712);
- :class:`HudPanel`: the frame every panel sits in (bracket corners, gradient
  title bar, "computed" or "✦ AI supervisor" label, key-hint subtitle, focus
  state, AI variant);
- :class:`WindowBar`: "▐ HERMES // SHADOW CONSOLE" with a ticking binary strip
  and the clock;
- :class:`TabBar`: the screen tabs, numbers only at compact width;
- :class:`ThemeChip` and :class:`ThemePicker`: the theme switch;
- :func:`key_chips`: reverse-video key chips for the footer;
- :class:`Meter`: the one meter every panel draws (cash and exposure on Home,
  stop room on a position row, the 90-day run bar, cpu, memory and disk).

The text of each piece is built by a pure function so tests can check it
without running an app.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import ClassVar, Final

from rich.cells import cell_len
from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Vertical
from textual.geometry import Size
from textual.message import Message
from textual.reactive import reactive
from textual.renderables.digits import Digits as DigitsRenderable
from textual.screen import ModalScreen
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import Digits, OptionList, Static
from textual.widgets.option_list import Option

from hermes.application.console.format import fmt_level
from hermes.entrypoints.tui_charts import gradient_rule, hero_digits
from hermes.entrypoints.tui_theme import (
    AI_GLYPH,
    CORNER_BOTTOM_RIGHT,
    CORNER_TOP_LEFT,
    EDGE,
    METER_CAP_LEFT,
    METER_CAP_RIGHT,
    METER_CELLS,
    METER_EMPTY,
    METER_FULL,
    MOTION,
    WINDOW_MARK,
    WINDOW_TITLE,
    ThemeChoice,
    Tokens,
    binary_strip,
    data_tokens,
    gradient,
    tokens_for,
)

__all__ = [
    "LABEL_AI",
    "LABEL_COMPUTED",
    "BigNumber",
    "HudPanel",
    "Meter",
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


def _euro_level(value: float) -> str:
    """Return a money figure mid count-up, as the big total shows it."""
    return fmt_level(Decimal(f"{value:.2f}"))


_SHIMMER_EDGE: Final = 3.0
_SHIMMER_CELLS_PER_STEP: Final = 2
_HERO_ROWS: Final = 4


class BigNumber(Digits):
    """The big total: Textual's digit glyphs in one bold colour, over a gradient rule.

    The digits always use the ``hero-digits`` token, bold, at full contrast
    (at least 7:1 on the panel in every theme): no theme, night mode or stale
    state dims them. The brand gradient is a 1-row underline below them
    (``hero-underline``); a stale figure dims only the underline. This is a
    property of the component, so every big number (Home, detail screens)
    keeps it. The Code Owner, 2026-10-06: gradient digits were illegible.

    :meth:`show` sets the figure. With motion on, a change of value counts up
    from the old value over ``MOTION.slow`` (``out_cubic``), and
    :meth:`start_shimmer` sweeps a bright band along the rule (never the
    digits) at 8 fps. With motion off the figure is set at once and the
    shimmer never runs.

    Args:
        motion: Run the count-up and the shimmer.
        counting: Formats a value mid count-up; euros (``fmt_level``) by
            default, so a day count (``12 / 90``) passes its own.
        id: Widget id.
        classes: Extra CSS classes.
    """

    DEFAULT_CSS = """
    BigNumber { width: auto; height: 4; }
    """

    shown: reactive[float] = reactive(0.0)

    def __init__(
        self,
        *,
        motion: bool = True,
        counting: Callable[[float], str] | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        """Start empty and hidden until :meth:`show` gets a figure."""
        super().__init__("", id=id, classes=classes)
        self._motion = motion
        self._counting_text = counting or _euro_level
        self._figure = ""
        self._target: float | None = None
        self._counting = False
        self._stale = False
        self.shimmer_at: float | None = None
        self._shimmer_timer: Timer | None = None

    def on_mount(self) -> None:
        """Redraw when the theme changes."""
        self.app.theme_changed_signal.subscribe(self, lambda _theme: self.refresh())

    @property
    def figure(self) -> str:
        """Return the figure the number shows once any count-up has ended."""
        return self._figure

    def show(self, figure: str | None, value: float | None, *, stale: bool = False) -> None:
        """Show a figure, counting up from the previous value when motion is on.

        Args:
            figure: The formatted figure (``€1,021.06``), or ``None`` to hide
                the number (a blind surface, EUR pending, a short terminal).
            value: The figure's value, the count-up target.
            stale: Hermes is down: the underline dims; the digits never do.
        """
        if figure is None or value is None:
            self.display = False
            self._target = None
            return
        self.display = True
        self._stale = stale
        previous, self._target, self._figure = self._target, value, figure
        if self._motion and previous is not None and previous != value:
            self._counting = True
            self.set_reactive(BigNumber.shown, previous)
            self.animate(
                "shown",
                value,
                duration=MOTION.slow,
                easing="out_cubic",
                on_complete=self._settle,
            )
            return
        self._settle()

    def watch_shown(self, shown: float) -> None:
        """Draw the counting figure."""
        if self._counting:
            self.update(self._counting_text(shown))

    def _settle(self) -> None:
        self._counting = False
        self.update(self._figure)

    def start_shimmer(self) -> None:
        """Start one shimmer pass across the figure (motion on, figure shown)."""
        if not self._motion or not self.display or self._shimmer_timer is not None:
            return
        self.shimmer_at = -_SHIMMER_EDGE
        self._shimmer_timer = self.set_interval(MOTION.shimmer_step, self._step_shimmer)

    def get_content_height(self, container: Size, viewport: Size, width: int) -> int:
        """Return four rows: three of digits and the rule."""
        del container, viewport, width
        return _HERO_ROWS

    @property
    def rule_width(self) -> int:
        """Return the rule's width: the width of the drawn digits."""
        return DigitsRenderable.get_width(self.value)

    def _step_shimmer(self) -> None:
        position = (self.shimmer_at or 0.0) + _SHIMMER_CELLS_PER_STEP
        if position > self.rule_width + _SHIMMER_EDGE:
            self.shimmer_at = None
            if self._shimmer_timer is not None:
                self._shimmer_timer.stop()
                self._shimmer_timer = None
        else:
            self.shimmer_at = position
        self.refresh()

    def render(self) -> Text:
        """Draw the digits in ``hero-digits`` over the underline; stale dims the underline only."""
        theme = self.app.theme
        tokens = tokens_for(theme)
        dim = data_tokens(theme).dim if self._stale else None
        out = hero_digits(self.value, tokens.hero_digits)
        out.append("\n")
        out.append_text(
            gradient_rule(
                self.rule_width,
                tokens.hero_underline,
                shimmer=self.shimmer_at,
                highlight=tokens.hero_digits,
                flat=dim,
            )
        )
        return out


@dataclass(frozen=True, slots=True)
class Meter:
    """The one meter renderer: ``value / cap`` of ``width`` block cells.

    The fill follows a gradient (the ``gain`` tokens unless the caller names
    other stops, or one solid colour), and the whole fill turns amber (the
    ``warn`` token) once ``value / cap`` reaches ``warn_from`` or when the
    caller raises ``warn`` on its own measure. The empty cells are drawn in the
    ``line`` token, the optional end caps and label in ``dim`` and ``muted``.

    Attributes:
        value: How much is used, in the cap's unit.
        cap: The full-scale value; zero or less draws an empty meter.
        width: Fill cells, without the end caps or the words.
        warn_from: Share of the cap from which the fill is amber; ``None``
            never warns on the share.
        warn: Draw the fill amber whatever the share (a warning the caller
            judges by another measure, such as the stop's ATR distance).
        label: Words before the meter; empty draws none.
        label_width: Pad the label to this many cells.
        percent: Append the share of the cap as a whole percent, unclamped,
            so a load above every cpu reads above 100%.
        caps: Draw ``▕`` and ``▏`` round the cells.
    """

    value: float
    cap: float = 1.0
    width: int = METER_CELLS
    warn_from: float | None = None
    warn: bool = False
    label: str = ""
    label_width: int = 0
    percent: bool = False
    caps: bool = False

    @property
    def ratio(self) -> float:
        """Return ``value / cap`` unclamped; 0 when the cap is zero or less."""
        return self.value / self.cap if self.cap > 0 else 0.0

    @property
    def share(self) -> float:
        """Return the fill share, clamped to [0, 1]."""
        return min(max(self.ratio, 0.0), 1.0)

    @property
    def filled(self) -> int:
        """Return the full cells: ``round(share x width)``, never past the width."""
        cells = max(self.width, 0)
        return min(round(self.share * cells), cells)

    @property
    def warned(self) -> bool:
        """Return whether the fill is amber."""
        return self.warn or (self.warn_from is not None and self.ratio >= self.warn_from)

    def render(
        self,
        tokens: Tokens,
        *,
        stops: Sequence[str] | None = None,
        colour: str | None = None,
    ) -> Text:
        """Return the meter as Rich text.

        Args:
            tokens: Token sheet.
            stops: Gradient over the whole width; the ``gain`` tokens when
                ``None``.
            colour: One solid colour for the fill instead of a gradient (a
                series colour, or ``dim`` while the data is stale); wins over
                the amber warning.

        Returns:
            Label, caps and ``width`` cells, then the percent when asked.
        """
        out = Text()
        if self.label:
            out.append(f"{self.label:<{self.label_width}} ", style=tokens.muted)
        if self.caps:
            out.append(METER_CAP_LEFT, style=tokens.dim)
        cells = max(self.width, 0)
        filled = self.filled
        warned = self.warned
        for index in range(filled):
            if colour is not None:
                style = colour
            elif warned:
                style = tokens.warn
            else:
                style = gradient(stops or tokens.gain, index / max(cells - 1, 1))
            out.append(METER_FULL, style=style)
        out.append(METER_EMPTY * (cells - filled), style=tokens.line)
        if self.caps:
            out.append(METER_CAP_RIGHT, style=tokens.dim)
        if self.percent:
            out.append(f" {round(self.ratio * 100)}%", style=tokens.text)
        return out
