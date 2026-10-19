"""Chart pieces of the Cockpit console, drawn as Rich text (design spec, issue #712).

No chart add-on: the big total uses Textual's own 3x3 digit glyphs in one
high-contrast colour over a 1-row gradient rule, and the curve is braille
dots; meters are the shared :class:`~hermes.entrypoints.tui_widgets.Meter`.
Every colour comes from the :mod:`hermes.entrypoints.tui_theme` tokens and
is blended with its :func:`~hermes.entrypoints.tui_theme.gradient`, so a
token change restyles every chart. Each function is pure, so tests check the text directly.
:class:`PriceChart` is the one plotted chart (``textual-plotext``, issue #732);
charts are display-only and never decide anything.
"""

from collections.abc import Sequence
from decimal import Decimal
from typing import Final

from rich.text import Text
from textual.renderables.digits import DIGITS, DIGITS3X3_BOLD
from textual_plotext import PlotextPlot

from hermes.application.console.format import sparkline
from hermes.entrypoints.tui_theme import HERO_RULE_GLYPH, blend, gradient

__all__ = [
    "BRAILLE_BLANK",
    "PriceChart",
    "area_chart",
    "braille_area",
    "braille_spark",
    "gradient_rule",
    "hero_digits",
    "spark_text",
]

BRAILLE_BLANK: Final = "⠀"
_GLYPH_ROWS: Final = 3
_GLYPH_WIDTH: Final = 3
_DOTS_PER_ROW: Final = 4
# Braille dot bits, bottom dot first: the left column, then the right column.
_LEFT_DOTS: Final = (0x40, 0x04, 0x02, 0x01)
_RIGHT_DOTS: Final = (0x80, 0x20, 0x10, 0x08)
_SHIMMER_BAND: Final = 3.0
_DIGIT_SWAPS: Final = str.maketrans({".": "•"})
# One-cell glyphs Textual's digits lack, centred on the middle row so they keep
# the one-cell width ``Digits.get_width`` gives every non-digit ("12 / 90").
_NARROW_GLYPHS: Final[dict[str, tuple[str, str, str]]] = {
    "/": (" ", "╱", " "),  # noqa: RUF001 - the drawn slash is meant
}


def _glyph(char: str) -> tuple[str, str, str]:
    """Return the three rows Textual's bold ``Digits`` draws for one character."""
    narrow = _NARROW_GLYPHS.get(char)
    if narrow is not None:
        return narrow
    try:
        position = DIGITS.index(char) * _GLYPH_ROWS
    except ValueError:
        return " ", " ", char
    rows = DIGITS3X3_BOLD[position : position + _GLYPH_ROWS]
    return (
        rows[0].ljust(_GLYPH_WIDTH),
        rows[1].ljust(_GLYPH_WIDTH),
        rows[2].ljust(_GLYPH_WIDTH),
    )


def hero_digits(text: str, colour: str) -> Text:
    """Return ``text`` as three rows of bold digit glyphs in one colour.

    Args:
        text: The figure, for example ``€1,021.06``.
        colour: The ``hero`` token (or ``dim`` while hermes is down).

    Returns:
        Three lines joined by newlines.
    """
    rows = [Text(), Text(), Text()]
    for char in text.translate(_DIGIT_SWAPS):
        for row, piece in zip(rows, _glyph(char), strict=True):
            row.append(piece, style=f"bold {colour}")
    return Text("\n").join(rows)


def gradient_rule(
    width: int,
    stops: Sequence[str],
    *,
    highlight: str,
    shimmer: float | None = None,
    flat: str | None = None,
) -> Text:
    """Return the 1-row rule under the big total, one gradient colour per cell.

    Args:
        width: Cells.
        stops: Gradient stops, first cell to last (the ``hero-underline`` token).
        highlight: The shimmer's colour.
        shimmer: Cell position of a shimmer pass, or ``None``; cells within
            the band blend toward ``highlight``.
        flat: One colour for every cell (stale), overriding gradient and shimmer.

    Returns:
        ``width`` cells of ``HERO_RULE_GLYPH``.
    """
    out = Text()
    last = max(width - 1, 1)
    for index in range(max(width, 0)):
        colour = flat or gradient(stops, index / last)
        if flat is None and shimmer is not None and abs(index - shimmer) < _SHIMMER_BAND:
            colour = blend(colour, highlight, 1 - abs(index - shimmer) / _SHIMMER_BAND)
        out.append(HERO_RULE_GLYPH, style=colour)
    return out


def _heights(values: Sequence[float], columns: int, dots: int) -> list[int]:
    """Return how many dots each dot column fills: 1 for the low, ``dots`` for the high."""
    lo, hi = min(values), max(values)
    span = hi - lo
    last = len(values) - 1
    heights = []
    for x in range(columns):
        value = values[round(x * last / (columns - 1))] if columns > 1 else values[-1]
        if span > 0:
            heights.append(1 + round((value - lo) / span * (dots - 1)))
        else:
            heights.append(1)
    return heights


def braille_area(values: Sequence[float], *, width: int, rows: int) -> list[str]:
    """Return an area chart of ``values`` in braille cells, filled from the bottom.

    Each cell holds 2x4 dots, so the chart has ``2 x width`` dot columns,
    sampled evenly from the series, and ``4 x rows`` dot levels. The lowest
    value fills one dot, the highest the full height; a flat series is the bottom line.

    Args:
        values: The series, oldest first.
        width: Cells per row.
        rows: Rows.

    Returns:
        ``rows`` strings of ``width`` braille characters, top row first; blank
        braille cells when ``values`` is empty.
    """
    if width < 1 or rows < 1:
        return []
    if not values:
        return [BRAILLE_BLANK * width for _ in range(rows)]
    levels = rows * _DOTS_PER_ROW
    heights = _heights([float(v) for v in values], width * 2, levels)
    cells = [[0] * width for _ in range(rows)]
    for x, height in enumerate(heights):
        dots = _LEFT_DOTS if x % 2 == 0 else _RIGHT_DOTS
        for level in range(height):
            row = rows - 1 - level // _DOTS_PER_ROW
            cells[row][x // 2] |= dots[level % _DOTS_PER_ROW]
    return ["".join(chr(0x2800 + bits) for bits in row) for row in cells]


def area_chart(values: Sequence[float], *, width: int, rows: int, stops: Sequence[str]) -> Text:
    """Return :func:`braille_area` coloured by row: the last stop at the top, the first below.

    Args:
        values: The series, oldest first.
        width: Cells per row.
        rows: Rows.
        stops: Gradient stops, dark first (``gain`` or ``loss`` tokens).

    Returns:
        ``rows`` lines joined by newlines.
    """
    lines = braille_area(values, width=width, rows=rows)
    out = []
    for index, line in enumerate(lines):
        position = 1 - index / max(rows - 1, 1)
        out.append(Text(line, style=gradient(stops, position)))
    return Text("\n").join(out)


def braille_spark(values: Sequence[Decimal], width: int, colour: str) -> Text:
    """Return a one-row braille sparkline of ``values`` in one colour.

    Args:
        values: The series, oldest first.
        width: Cells.
        colour: Its colour (up or down, from the data tokens).

    Returns:
        ``width`` braille cells; blank cells for an empty series, nothing for no width.
    """
    if width < 1:
        return Text("")
    return Text(braille_area([float(v) for v in values], width=width, rows=1)[0], style=colour)


def spark_text(values: Sequence[Decimal], width: int, colour: str) -> Text:
    """Return the console sparkline of ``values`` in one colour.

    Args:
        values: The series, oldest first.
        width: Most cells to use.
        colour: Its colour (up or down, from the data tokens).

    Returns:
        The sparkline.
    """
    return Text(sparkline(values, width), style=colour)


class PriceChart(PlotextPlot):
    """Line chart of a series of closing prices."""

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self._closes: tuple[Decimal, ...] = ()

    @property
    def closes(self) -> tuple[Decimal, ...]:
        """The closes currently plotted, oldest first."""
        return self._closes

    def set_closes(self, closes: Sequence[Decimal]) -> None:
        """Replace the plotted series and redraw.

        Args:
            closes: Closing prices, oldest first. Converted to float only for
                drawing; the Decimal values are kept as given.
        """
        self._closes = tuple(closes)
        self.plt.clear_data()
        if self._closes:
            self.plt.plot([float(close) for close in self._closes])
        self.refresh()
