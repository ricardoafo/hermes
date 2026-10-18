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
- the theme chip names the choice and says when Auto is at night;
- the one meter fills ``round(value / cap x width)`` cells, clamps to its
  width, runs its fill along a gradient, turns the whole fill amber from its
  warning share or on the caller's warning, and adds label, caps and percent
  only when asked.
"""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st
from rich.console import Console
from rich.style import Style
from rich.text import Text

from hermes.entrypoints.tui_settings import ThemeChoice
from hermes.entrypoints.tui_theme import BASE, NEON
from hermes.entrypoints.tui_widgets import (
    LABEL_AI,
    LABEL_COMPUTED,
    Meter,
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


def test_window_bar_has_mark_status_and_clock() -> None:
    """The bar opens with ▐ HERMES // SHADOW CONSOLE, then the status, and ends with the clock."""
    status = Text("next bar 03:48:00")
    bar = window_bar_text(80, NEON, status=status, clock="14:12:00").plain
    assert (
        bar.startswith("▐ HERMES // SHADOW CONSOLE  next bar 03:48:00 "),
        bar.endswith("14:12:00 "),
        len(bar),
    ) == (True, True, 80)


def test_window_bar_with_empty_status_has_only_mark_and_clock() -> None:
    """With nothing to show (hermes late or down, no ticker) the bar is the mark and the clock."""
    bar = window_bar_text(40, NEON, status=Text(""), clock="14:12:00").plain
    assert bar == "▐ HERMES // SHADOW CONSOLE" + " " * 5 + "14:12:00 "


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


def test_meter_fills_its_share_of_the_cap() -> None:
    """15 of a 20 cap over 8 cells is 6 full cells and 2 empty ones."""
    assert Meter(15, cap=20, width=8).render(BASE).plain == "██████░░"


def test_meter_default_width_is_ten_cells() -> None:
    """Without a width a meter is ten cells: 0.42 rounds to four full."""
    assert Meter(0.42).render(BASE).plain == "████░░░░░░"


def test_meter_clamps_out_of_range_values() -> None:
    """A load above the cap fills every cell; a negative value or no cap fills none."""
    plains = [
        Meter(1.7, width=4).render(BASE).plain,
        Meter(-0.2, width=4).render(BASE).plain,
        Meter(5, cap=0, width=4).render(BASE).plain,
    ]
    assert plains == ["████", "░░░░", "░░░░"]


def test_meter_fill_follows_the_gain_gradient_and_empty_is_line() -> None:
    """Full fill over 3 cells: first, middle and last gain stop; an empty cell is ``line``."""
    full = Meter(1, width=3).render(BASE)
    empty = Meter(0, width=1).render(BASE)
    assert [_hex(_style_at(full, i).color) for i in range(3)] == ["#1B3A6B", "#468CE6", "#8BE9FF"]
    assert _hex(_style_at(empty, 0).color) == "#1A2740"


def test_meter_takes_other_gradient_stops() -> None:
    """The 90-day bar passes the brand stops: its first cell is the first brand stop."""
    bar = Meter(1, width=4).render(BASE, stops=BASE.brand)
    assert _hex(_style_at(bar, 0).color) == BASE.brand[0].upper()
    assert _hex(_style_at(bar, 3).color) == BASE.brand[-1].upper()


def test_meter_turns_amber_from_the_warning_share() -> None:
    """With an amber share of 0.8: 16 of a 20 cap is amber in every cell, 15 is not."""
    warned = Meter(16, cap=20, width=4, warn_from=0.8).render(BASE)
    calm = Meter(15, cap=20, width=4, warn_from=0.8).render(BASE)
    assert {_hex(_style_at(warned, i).color) for i in range(3)} == {"#E6BE28"}
    assert _hex(_style_at(calm, 0).color) == "#1B3A6B"


def test_meter_turns_amber_on_the_callers_warning() -> None:
    """``warn=True`` draws the fill amber whatever the share."""
    bar = Meter(0.25, width=4, warn=True).render(BASE)
    assert _hex(_style_at(bar, 0).color) == "#E6BE28"


def test_meter_solid_colour_wins_over_gradient_and_warning() -> None:
    """A stale stop meter is drawn ``dim`` even while the stop is close."""
    bar = Meter(1, width=2, warn=True).render(BASE, colour=BASE.dim)
    assert {_hex(_style_at(bar, i).color) for i in range(2)} == {BASE.dim}


def test_meter_with_caps_label_and_percent() -> None:
    """Label padded to 6, caps round the cells, and the share as a whole percent."""
    bar = Meter(0.42, width=10, label="disk", label_width=6, caps=True, percent=True)
    assert bar.render(BASE).plain == "disk   ▕████░░░░░░▏ 42%"


def test_meter_percent_is_not_clamped() -> None:
    """A cpu load of 1.7 reads 170% while the cells stop at full."""
    assert Meter(1.7, width=2, percent=True).render(BASE).plain == "██ 170%"


@given(
    low=st.floats(-2, 3, allow_nan=False),
    high=st.floats(-2, 3, allow_nan=False),
    width=st.integers(0, 64),
)
def test_meter_fill_is_monotonic_and_clamped_to_the_width(
    low: float, high: float, width: int
) -> None:
    """More value never draws fewer full cells, and the fill stays within 0..width."""
    low, high = min(low, high), max(low, high)
    lower, upper = Meter(low, width=width), Meter(high, width=width)
    assert 0 <= lower.filled <= upper.filled <= width
    assert len(upper.render(BASE).plain) == width
