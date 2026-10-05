"""The reusable Cockpit widgets (issue #711), checked through their pure text builders.

Invariants:

- a panel's top edge is: cyan bracket corner, upper-case title bar, rule,
  producer label; the AI variant marks the title with ✦ and says
  "✦ AI supervisor"; the focused title is a solid live-colour chip;
- a panel's bottom edge carries the key hints and ends with the magenta corner;
- both edges fill exactly the panel width;
- the window bar shows the brand mark, the binary strip (not at night) and
  the clock at the right edge;
- the tab bar shows names, or numbers only at compact width, and highlights
  the shown tab;
- footer keys are reverse-video chips;
- the theme chip names the choice and says when Auto is at night.
"""

from __future__ import annotations

from rich.console import Console
from rich.style import Style
from rich.text import Text

from hermes.entrypoints.tui_settings import ThemeChoice
from hermes.entrypoints.tui_theme import NEON, binary_strip
from hermes.entrypoints.tui_widgets import (
    LABEL_AI,
    LABEL_COMPUTED,
    Tab,
    bottom_edge,
    key_chips,
    tab_text,
    theme_chip_text,
    top_edge,
    window_bar_text,
)

_TABS = (Tab("1", "Home"), Tab("2", "Signals", 714), Tab("3", "Positions", 715))


_CONSOLE = Console(width=200)


def _style_at(text: Text, offset: int) -> Style:
    return text.get_style_at_offset(_CONSOLE, offset)


def _hex(colour: object) -> str:
    """Return a Rich colour's ``#RRGGBB``, or empty when unset."""
    triplet = getattr(colour, "triplet", None)
    return triplet.hex.upper() if triplet is not None else ""


def test_top_edge_is_corner_title_rule_and_label() -> None:
    """At 40 columns: ┏━, the title in capitals, a rule, then "computed"."""
    edge = top_edge("Portfolio", LABEL_COMPUTED, 40, NEON)
    assert edge.plain == "┏━ PORTFOLIO  " + "─" * 16 + " computed "


def test_top_edge_corner_is_live_coloured() -> None:
    """The top-left bracket is drawn in the live colour (#00F0FF in Neon)."""
    style = _style_at(top_edge("Portfolio", LABEL_COMPUTED, 40, NEON), 0)
    assert _hex(style.color) == "#00F0FF"


def test_ai_variant_marks_the_title_and_the_label() -> None:
    """The AI variant reads ✦ BRIEFS and ✦ AI supervisor, in the AI colour."""
    edge = top_edge("Briefs", LABEL_AI, 40, NEON, ai=True)
    corner = _style_at(edge, 0)
    assert (edge.plain, _hex(corner.color)) == (
        "┏━ ✦ BRIEFS  " + "─" * 10 + " ✦ AI supervisor ",
        "#FF3DF0",
    )


def test_focused_title_is_a_solid_live_chip() -> None:
    """When the panel is active the title's background is the live colour."""
    style = _style_at(top_edge("Positions", LABEL_COMPUTED, 40, NEON, active=True), 3)
    assert _hex(style.bgcolor) == "#00F0FF"


def test_idle_title_is_a_chrome_gradient() -> None:
    """When idle the title bar runs magenta to cyan: first and last cells differ."""
    edge = top_edge("Positions", LABEL_COMPUTED, 40, NEON)
    first, last = _hex(_style_at(edge, 2).bgcolor), _hex(_style_at(edge, 12).bgcolor)
    assert (first, last) == ("#FF3DF0", "#00F0FF")


def test_bottom_edge_carries_hints_and_the_magenta_corner() -> None:
    """The bottom edge reads the key hints and ends with ━┛ in the AI colour."""
    edge = bottom_edge("D W M Y range", 30, NEON)
    corner = _style_at(edge, 29)
    assert (edge.plain, _hex(corner.color)) == (
        "  D W M Y range ────────────━┛",
        "#FF3DF0",
    )


def test_edges_fill_the_width_exactly() -> None:
    """Top and bottom edges are as wide as the panel, for any common width."""
    widths = [
        (
            top_edge("Market watch", LABEL_COMPUTED, w, NEON).cell_len,
            bottom_edge("", w, NEON).cell_len,
        )
        for w in (30, 44, 80, 160)
    ]
    assert widths == [(30, 30), (44, 44), (80, 80), (160, 160)]


def test_narrow_top_edge_drops_the_label_before_the_title() -> None:
    """With no room for the label, the title and corner still show."""
    edge = top_edge("Activity", LABEL_COMPUTED, 16, NEON)
    assert edge.plain == "┏━ ACTIVITY  " + "─" * 3


def test_window_bar_has_mark_strip_and_clock() -> None:
    """The bar opens with ▐ HERMES // SHADOW CONSOLE, shows tick 3's strip, ends with the clock."""
    bar = window_bar_text(80, NEON, tick=3, clock="14:12:00").plain
    assert (
        bar.startswith("▐ HERMES // SHADOW CONSOLE  "),
        binary_strip(3) in bar,
        bar.endswith("14:12:00 "),
        len(bar),
    ) == (True, True, True, 80)


def test_window_bar_has_no_strip_at_night() -> None:
    """With the strip off (night) no binary digits are drawn."""
    bar = window_bar_text(80, NEON, tick=3, clock="23:30:00", strip=False).plain
    assert binary_strip(3) not in bar


def test_tabs_show_names_and_numbers_when_wide() -> None:
    """Wide tabs read "1 Home · 2 Signals · 3 Positions"."""
    text, _ = tab_text(_TABS, 0, NEON, compact=False)
    assert text.plain == "  1 Home · 2 Signals · 3 Positions "


def test_tabs_show_numbers_only_when_compact() -> None:
    """Compact tabs are the numbers alone."""
    text, _ = tab_text(_TABS, 0, NEON, compact=True)
    assert text.plain == "  1   2   3 "


def test_active_tab_is_highlighted_and_click_ranges_cover_each_tab() -> None:
    """The shown tab has the live background; each range covers its label."""
    text, ranges = tab_text(_TABS, 1, NEON, compact=False)
    start, end = ranges[1]
    style = _style_at(text, start)
    assert (text.plain[start:end], _hex(style.bgcolor)) == (
        " 2 Signals ",
        "#00F0FF",
    )


def test_footer_keys_are_reverse_video_chips() -> None:
    """Each key is reversed and followed by its label."""
    chips = key_chips([("D W M Y", "range"), ("Q", "quit")], NEON)
    assert (chips.plain, _style_at(chips, 1).reverse, _style_at(chips, 9).reverse) == (
        " D W M Y range   Q quit",
        True,
        None,
    )


def test_theme_chip_names_the_choice() -> None:
    """The chip says NEON, CONTROL ROOM, AUTO, or AUTO · NIGHT at night; compact is the glyph."""
    chips = [
        theme_chip_text(ThemeChoice.NEON, NEON, night=False).plain,
        theme_chip_text(ThemeChoice.CONTROL, NEON, night=False).plain,
        theme_chip_text(ThemeChoice.AUTO, NEON, night=False).plain,
        theme_chip_text(ThemeChoice.AUTO, NEON, night=True).plain,
        theme_chip_text(ThemeChoice.NEON, NEON, night=True).plain,
        theme_chip_text(ThemeChoice.AUTO, NEON, night=True, compact=True).plain,
    ]
    assert chips == [
        " ◑ NEON ",
        " ◑ CONTROL ROOM ",
        " ◑ AUTO ",
        " ◑ AUTO · NIGHT ",
        " ◑ NEON ",
        " ◑ ",
    ]
