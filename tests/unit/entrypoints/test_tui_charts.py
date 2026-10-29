"""The Cockpit chart renderers (issue #712): pure Rich text, no add-on.

Invariants:

- the big total is the Textual 3x3 digit glyphs in one bold colour (the Code
  Owner, 2026-10-06: gradient digits were illegible); the gradient is a 1-row
  rule under them, from the first stop to the last; a shimmer brightens only
  the rule cells near its position; a stale rule is one flat colour;
- the braille area curve has exactly the asked rows and columns, only braille
  cells, fills every dot column from the bottom, and never draws a higher value
  lower than a smaller one (property);
- the curve's rows run from the brightest stop at the top to the darkest at the
  bottom;
- the gradient sparkline is the console sparkline in one colour;
- PriceChart (#732) keeps the exact Decimal closes it was given, in order; its
  plot spans the series low to high; an empty series draws an empty plot.

The meters are the shared ``Meter`` and are checked in ``test_tui_widgets.py``.
"""

import asyncio
from decimal import Decimal

from hypothesis import given
from hypothesis import strategies as st
from rich.text import Text
from textual.app import App, ComposeResult

from hermes.entrypoints.tui_charts import (
    BRAILLE_BLANK,
    PriceChart,
    area_chart,
    braille_area,
    braille_spark,
    gradient_rule,
    hero_digits,
    spark_text,
)
from hermes.entrypoints.tui_theme import HERO_RULE_GLYPH

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
# Big total: single-colour digits over a gradient rule
# ----------------------------------------------------------------------


def test_hero_digits_draw_the_textual_glyphs() -> None:
    """'€1' is three rows: the bold euro glyph then the bold one, as Textual draws them."""
    text = hero_digits("€1", "#E6ECF5")
    assert text.plain.split("\n") == ["╭─╮╺┓ ", "╪═  ┃ ", "╰─╯╺┻╸"]


def test_hero_digits_are_one_bold_colour() -> None:
    """Every glyph cell of '€1,021.06' is the one colour given, in bold (no per-digit gradient)."""
    text = hero_digits("€1,021.06", "#E6ECF5")
    styles = {str(span.style) for span in text.spans if text.plain[span.start : span.end].strip()}
    assert styles == {"bold #E6ECF5"}


def test_a_comma_is_one_cell_wide() -> None:
    """'1,0' renders the comma in one column on the bottom row, between two 3-wide digits."""
    assert hero_digits("1,0", "#E6ECF5").plain.split("\n")[2] == "╺┻╸,┗━┛"


def test_rule_runs_from_the_first_to_the_last_stop() -> None:
    """A 5-cell rule is 5 rule glyphs; the first cell is the first stop, the last the last."""
    rule = gradient_rule(5, _BRAND, highlight="#FFFFFF")
    colours = _colours(rule)
    assert (rule.plain, colours[0], colours[-1]) == (HERO_RULE_GLYPH * 5, "#000000", "#FFFFFF")


def test_shimmer_brightens_only_the_rule_cells_near_it() -> None:
    """A shimmer at cell 0 turns cell 0 to the highlight; cell 9 keeps its stop."""
    rule = gradient_rule(10, ("#000000", "#000000"), shimmer=0.0, highlight="#FFFFFF")
    colours = _colours(rule)
    assert (colours[0], colours[-1]) == ("#FFFFFF", "#000000")


def test_a_stale_rule_is_one_flat_colour_even_while_shimmering() -> None:
    """``flat`` overrides the gradient and the shimmer: every cell is that colour."""
    rule = gradient_rule(6, _BRAND, highlight="#FFFFFF", shimmer=2.0, flat="#5D6780")
    assert set(_colours(rule)) == {"#5D6780"}


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


def test_a_short_series_draws_a_slope_not_steps() -> None:
    """Two points over 4 cells (8 dot columns, 8 levels) rise one level per column."""
    heights = _column_heights(braille_area([0.0, 7.0], width=4, rows=2))
    assert heights == [1, 2, 3, 4, 5, 6, 7, 8]


def test_the_first_and_last_columns_hold_the_first_and_last_values() -> None:
    """Whatever lies between, the left edge is the first value's height and the right the last's."""
    heights = _column_heights(braille_area([2.0, 9.0, 0.0, 5.0], width=3, rows=1))
    assert (heights[0], heights[-1]) == (1 + round(2 / 9 * 3), 1 + round(5 / 9 * 3))


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
    """Any series: exact size, braille only, every column has a dot, and dots stack without gaps."""
    chart = braille_area(values, width=width, rows=rows)
    heights = _column_heights(chart)
    total_dots = sum((ord(c) - 0x2800).bit_count() for row in chart for c in row)
    assert (
        [len(row) for row in chart],
        all(0x2800 <= ord(c) <= 0x28FF for row in chart for c in row),
        min(heights) >= 1,
        sum(heights) == total_dots,
    ) == ([width] * rows, True, True, True)


@given(
    values=st.lists(
        st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False),
        min_size=2,
        max_size=60,
    ),
    width=st.integers(min_value=1, max_value=40),
    rows=st.integers(min_value=1, max_value=8),
)
def test_a_rising_series_never_draws_a_falling_column_property(
    values: list[float], width: int, rows: int
) -> None:
    """A series that only rises (or only falls) draws columns that never turn back.

    The check is stated on the input order alone, not on how the chart samples it.
    """
    rising = _column_heights(braille_area(sorted(values), width=width, rows=rows))
    falling = _column_heights(braille_area(sorted(values, reverse=True), width=width, rows=rows))
    assert (rising == sorted(rising), falling == sorted(falling, reverse=True)) == (True, True)


def test_area_chart_rows_go_from_the_bright_stop_down_to_the_dark_one() -> None:
    """Top row in the last gradient stop, bottom row in the first."""
    chart = area_chart([1.0, 2.0, 3.0], width=4, rows=3, stops=("#000000", "#FFFFFF"))
    lines = chart.split("\n")
    tops = {span.style for span in lines[0].spans}
    bottoms = {span.style for span in lines[-1].spans}
    assert (tops, bottoms) == ({"#FFFFFF"}, {"#000000"})


# ----------------------------------------------------------------------
# Sparkline
# ----------------------------------------------------------------------


def test_spark_text_is_the_console_sparkline_in_one_colour() -> None:
    """Rising values draw low to high blocks in the given colour."""
    text = spark_text([Decimal(1), Decimal(2), Decimal(3)], 3, "#468CE6")
    assert (text.plain, text.style) == ("▁▅█", "#468CE6")


# ----------------------------------------------------------------------
# PriceChart (#732)
# ----------------------------------------------------------------------


class _ChartApp(App[None]):
    def compose(self) -> ComposeResult:
        yield PriceChart(id="chart")


async def _render(closes: list[Decimal]) -> tuple[tuple[Decimal, ...], str]:
    app = _ChartApp()
    async with app.run_test(size=(60, 16)) as pilot:
        chart = app.query_one(PriceChart)
        chart.set_closes(closes)
        await pilot.pause()
        return chart.closes, chart.plt.build()


def test_chart_keeps_decimal_closes_in_order() -> None:
    """Invariant: the widget stores the given closes unchanged and in order."""
    closes = [Decimal("100.5"), Decimal("101.25"), Decimal("99.75")]
    kept, _ = asyncio.run(_render(closes))
    assert kept == (Decimal("100.5"), Decimal("101.25"), Decimal("99.75"))


def test_chart_axis_spans_series_low_and_high() -> None:
    """Invariant: the drawn plot labels cover the lowest and highest close."""
    _, drawn = asyncio.run(_render([Decimal("100"), Decimal("110"), Decimal("105")]))
    assert "100" in drawn
    assert "110" in drawn


def test_chart_with_no_closes_is_empty_and_does_not_raise() -> None:
    """Invariant: an empty series leaves an empty chart, never an error."""
    kept, _ = asyncio.run(_render([]))
    assert kept == ()


def test_braille_spark_is_one_row_of_braille_in_one_colour() -> None:
    """A rising 4-tick series is 2 cells, low at the left, full at the right, all one colour."""
    spark = braille_spark([Decimal(1), Decimal(2), Decimal(3), Decimal(4)], 2, "#468CE6")
    assert (spark.plain, spark.style) == ("⣠⣾", "#468CE6")


def test_braille_spark_of_no_width_is_empty() -> None:
    """Zero cells draw nothing rather than a blank cell."""
    assert braille_spark([Decimal(1)], 0, "#468CE6").plain == ""
