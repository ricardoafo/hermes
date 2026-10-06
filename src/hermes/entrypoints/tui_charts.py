"""Chart pieces of the Cockpit console, drawn as Rich text (design spec, issue #712).

No chart add-on: the big total uses Textual's own 3x3 digit glyphs, the curve
is braille dots, the meters are block cells. Every colour comes from the
:mod:`hermes.entrypoints.tui_theme` tokens and is blended with its
:func:`~hermes.entrypoints.tui_theme.gradient`, so a token change restyles
every chart. Each function is pure, so tests check the text directly.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Final

from rich.text import Text
from textual.renderables.digits import DIGITS, DIGITS3X3_BOLD

from hermes.application.console.format import sparkline
from hermes.entrypoints.tui_theme import METER_EMPTY, METER_FULL, Tokens, blend, gradient

__all__ = [
    "BRAILLE_BLANK",
    "area_chart",
    "braille_area",
    "braille_spark",
    "gradient_digits",
    "meter",
    "run_bar",
    "spark_text",
]

BRAILLE_BLANK: Final = "⠀"
_GLYPH_ROWS: Final = 3
_GLYPH_WIDTH: Final = 3
_DOTS_PER_ROW: Final = 4
# Braille dot bits, bottom dot first: the left column, then the right column.
_LEFT_DOTS: Final = (0x40, 0x04, 0x02, 0x01)
_RIGHT_DOTS: Final = (0x80, 0x20, 0x10, 0x08)
_SHIMMER_BAND: Final = 1.5
_DIGIT_SWAPS: Final = str.maketrans({".": "•"})


def _glyph(char: str) -> tuple[str, str, str]:
    """Return the three rows Textual's bold ``Digits`` draws for one character."""
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


def _char_colour(
    index: int, count: int, stops: Sequence[str], shimmer: float | None, highlight: str
) -> str:
    base = gradient(stops, index / max(count - 1, 1))
    if shimmer is None:
        return base
    distance = abs(index - shimmer)
    if distance >= _SHIMMER_BAND:
        return base
    return blend(base, highlight, 1 - distance / _SHIMMER_BAND)


def gradient_digits(
    text: str,
    stops: Sequence[str],
    *,
    shimmer: float | None = None,
    highlight: str = "#FFFFFF",
    flat: str | None = None,
) -> Text:
    """Return ``text`` as three rows of bold digit glyphs, one gradient colour per character.

    Args:
        text: The figure, for example ``€1,021.06``.
        stops: Gradient stops; the first character gets the first stop and the
            last character the last.
        shimmer: Character position of a shimmer pass, or ``None``; characters
            within 1.5 positions blend toward ``highlight``.
        highlight: The shimmer's colour.
        flat: One colour for every character (a stale total), overriding the
            gradient and the shimmer.

    Returns:
        Three lines joined by newlines.
    """
    chars = text.translate(_DIGIT_SWAPS)
    rows = [Text(), Text(), Text()]
    for index, char in enumerate(chars):
        colour = flat or _char_colour(index, len(chars), stops, shimmer, highlight)
        for row, piece in zip(rows, _glyph(char), strict=True):
            row.append(piece, style=f"bold {colour}")
    return Text("\n").join(rows)


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


def meter(
    share: float,
    cells: int,
    *,
    empty: str,
    colour: str | None = None,
    stops: Sequence[str] | None = None,
) -> Text:
    """Return a block meter: ``round(share x cells)`` full cells, the rest empty.

    Args:
        share: Fill, clamped to [0, 1].
        cells: Width in cells.
        empty: Colour of the empty cells.
        colour: One colour for the full cells; wins over ``stops``.
        stops: Gradient over the whole meter width when no ``colour``.

    Returns:
        Exactly ``cells`` cells.
    """
    share = min(max(share, 0.0), 1.0)
    filled = round(share * cells)
    out = Text()
    for index in range(filled):
        if colour is not None:
            style = colour
        else:
            style = gradient(stops or (empty, empty), index / max(cells - 1, 1))
        out.append(METER_FULL, style=style)
    out.append(METER_EMPTY * (cells - filled), style=empty)
    return out


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


def run_bar(day: int, length: int, cells: int, tokens: Tokens) -> Text:
    """Return the 90-day run bar: ``day / length`` of the cells in the brand gradient.

    Args:
        day: Current day of the run.
        length: Planned days.
        cells: Width in cells.
        tokens: Token sheet.

    Returns:
        Exactly ``cells`` cells.
    """
    share = day / length if length > 0 else 0.0
    return meter(share, cells, stops=tokens.brand, empty=tokens.line)
