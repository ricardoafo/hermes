"""The Cockpit chart renderers (issue #712): pure Rich text, no add-on.

Invariants:

- the big total is the Textual 3x3 digit glyphs, one brand-gradient colour per
  character from the first stop to the last; a shimmer brightens only the
  characters near its position; a stale total is one flat colour;
- the braille area curve has exactly the asked rows and columns, only braille
  cells, fills every dot column from the bottom, and never draws a higher value
  lower than a smaller one (property);
- the curve's rows run from the brightest stop at the top to the darkest at the
  bottom;
- a meter fills ``round(share x cells)`` cells and leaves the rest empty, clamps
  its share to [0, 1], and draws a solid colour when given one;
- the gradient sparkline is the console sparkline in one colour;
- the 90-day run bar fills day / length of its cells.
"""

from __future__ import annotations

from decimal import Decimal

from hypothesis import given
from hypothesis import strategies as st
from rich.text import Text

from hermes.entrypoints.tui_charts import (
    BRAILLE_BLANK,
    area_chart,
    braille_area,
    gradient_digits,
    meter,
    run_bar,
    spark_text,
)
from hermes.entrypoints.tui_theme import BASE

_BRAND = ("#000000", "#FFFFFF")
_LEFT_DOTS = (0x40, 0x04, 0x02, 0x01)
_RIGHT_DOTS = (0x80, 0x20, 0x10, 0x08)


def _colours(text: Text) -> list[str]:
    """Return the colour of every drawn span, in order (the last word of its style)."""
    return [
        str(span.style).split()[-1].upper()
        for span in text.spans
        if text.plain[span.start : span.end].strip()
    ]


def _column_heights(rows: list[str]) -> list[int]:
    """Decode a braille chart into the number of dots in each dot column."""
    height = len(rows) * 4
    width = len(rows[0]) * 2
    heights = [0] * width
    for x in range(width):
        dots = _LEFT_DOTS if x % 2 == 0 else _RIGHT_DOTS
        for level in range(height):
            row = rows[len(rows) - 1 - level // 4]
            bits = ord(row[x // 2]) - 0x2800
            if bits & dots[level % 4]:
                heights[x] += 1
    return heights


# ----------------------------------------------------------------------
# Gradient digits
# ----------------------------------------------------------------------


def test_gradient_digits_draw_the_textual_glyphs() -> None:
    """'€1' is three rows: the bold euro glyph then the bold one, as Textual draws them."""
    text = gradient_digits("€1", _BRAND)
    assert text.plain.split("\n") == ["╭─╮╺┓ ", "╪═  ┃ ", "╰─╯╺┻╸"]


def test_gradient_runs_from_the_first_to_the_last_stop_per_character() -> None:
    """The first character is the first stop and the last character the last stop."""
    text = gradient_digits("00", _BRAND)
    colours = _colours(text)
    assert (colours[0], colours[1]) == ("#000000", "#FFFFFF")


def test_a_comma_is_one_cell_wide_and_keeps_its_colour_slot() -> None:
    """'1,0' renders the comma in one column on the bottom row, between two 3-wide digits."""
    text = gradient_digits("1,0", _BRAND)
    assert text.plain.split("\n")[2] == "╺┻╸,┗━┛"


def test_shimmer_brightens_only_the_characters_near_it() -> None:
    """A shimmer at character 0 turns the first glyph to the highlight; the last stays its stop."""
    text = gradient_digits("000", ("#000000", "#000000"), shimmer=0.0, highlight="#FFFFFF")
    first_row = _colours(text)[:3]
    assert (first_row[0], first_row[-1]) == ("#FFFFFF", "#000000")


def test_a_stale_total_is_one_flat_colour() -> None:
    """``flat`` overrides the gradient: every cell is that colour."""
    text = gradient_digits("12", _BRAND, flat="#5D6780")
    assert set(_colours(text)) == {"#5D6780"}


# ----------------------------------------------------------------------
# Braille area curve
# ----------------------------------------------------------------------


def test_rising_series_fills_from_the_bottom_left_to_the_top_right() -> None:
    """0 then 1 over one cell: left dot column 1 dot, right column full (4 dots)."""
    assert braille_area([0.0, 1.0], width=1, rows=1) == [
        chr(0x2800 | 0x40 | 0x80 | 0x20 | 0x10 | 0x08)
    ]


def test_flat_series_is_the_bottom_line() -> None:
    """A flat series draws one dot per column: a level line at the bottom, read as "no change"."""
    rows = braille_area([5.0, 5.0, 5.0], width=3, rows=2)
    assert _column_heights(rows) == [1] * 6


def test_empty_series_is_blank_cells() -> None:
    """No values: the chart keeps its size and is all blank braille cells."""
    assert braille_area([], width=2, rows=2) == [BRAILLE_BLANK * 2, BRAILLE_BLANK * 2]


@given(
    values=st.lists(
        st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False),
        min_size=1,
        max_size=60,
    ),
    width=st.integers(min_value=1, max_value=40),
    rows=st.integers(min_value=1, max_value=8),
)
def test_curve_shape_and_order_property(values: list[float], width: int, rows: int) -> None:
    """Any series: exact size, braille only, every column has a dot, and order is kept.

    A dot column sampled from a larger value is never lower than one sampled
    from a smaller value, and dots stack from the bottom without gaps.
    """
    chart = braille_area(values, width=width, rows=rows)
    heights = _column_heights(chart)
    total_dots = sum(bin(ord(c) - 0x2800).count("1") for row in chart for c in row)
    columns = width * 2
    sampled = [values[round(x * (len(values) - 1) / (columns - 1))] for x in range(columns)]
    ordered = all(
        heights[a] <= heights[b]
        for a in range(columns)
        for b in range(columns)
        if sampled[a] < sampled[b]
    )
    assert (
        [len(row) for row in chart],
        all(0x2800 <= ord(c) <= 0x28FF for row in chart for c in row),
        min(heights) >= 1,
        sum(heights) == total_dots,
        ordered,
    ) == ([width] * rows, True, True, True, True)


def test_area_chart_rows_go_from_the_bright_stop_down_to_the_dark_one() -> None:
    """Top row in the last gradient stop, bottom row in the first."""
    chart = area_chart([1.0, 2.0, 3.0], width=4, rows=3, stops=("#000000", "#FFFFFF"))
    lines = chart.split("\n")
    tops = {span.style for span in lines[0].spans}
    bottoms = {span.style for span in lines[-1].spans}
    assert (tops, bottoms) == ({"#FFFFFF"}, {"#000000"})


# ----------------------------------------------------------------------
# Meters, sparkline, run bar
# ----------------------------------------------------------------------


def test_meter_fills_the_rounded_share_of_cells() -> None:
    """A 0.25 share of 8 cells fills 2 and leaves 6 empty."""
    assert meter(0.25, 8, colour="#468CE6", empty="#1A2740").plain == "██░░░░░░"


def test_meter_clamps_its_share() -> None:
    """A share above 1 fills every cell; below 0 fills none."""
    assert (
        meter(1.7, 4, colour="#468CE6", empty="#1A2740").plain,
        meter(-0.2, 4, colour="#468CE6", empty="#1A2740").plain,
    ) == (
        "████",
        "░░░░",
    )


def test_solid_meter_uses_its_colour_and_gradient_meter_its_stops() -> None:
    """A solid meter is one colour; a gradient meter's first cell is the first stop."""
    solid = meter(1.0, 3, colour="#E6BE28", empty="#1A2740")
    graded = meter(1.0, 3, stops=("#000000", "#FFFFFF"), empty="#1A2740")
    assert ({s.style for s in solid.spans}, graded.spans[0].style, graded.spans[-1].style) == (
        {"#E6BE28"},
        "#000000",
        "#FFFFFF",
    )


def test_spark_text_is_the_console_sparkline_in_one_colour() -> None:
    """Rising values draw low to high blocks in the given colour."""
    text = spark_text([Decimal(1), Decimal(2), Decimal(3)], 3, "#468CE6")
    assert (text.plain, text.style) == ("▁▅█", "#468CE6")


def test_run_bar_fills_day_over_length() -> None:
    """Day 45 of 90 over 10 cells fills 5 cells in the brand gradient."""
    bar = run_bar(45, 90, 10, BASE)
    assert (bar.plain, bar.spans[0].style) == ("█████░░░░░", BASE.brand[0])
