"""Number and glyph formatting shared by every console renderer.

Rules from the approved UX proposal and ADR-003 §07: € and % always to two
decimals; every change or P&L figure carries a sign and an arrow, so meaning
never relies on colour; levels (value, cash, prices) are unsigned. Rounding is
half up, and the arrow follows the rounded value so ``-0.004`` reads
``· €0.00`` rather than a down arrow over a zero. Negative figures use the
typographic minus sign (U+2212), which lines up with the plus sign.

All functions are pure.

Reference: openspec/changes/console-read-model/change.md (D-6).
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Final

from hermes.domain.value_objects.cents import to_cents

__all__ = [
    "ARROW_DOWN",
    "ARROW_FLAT",
    "ARROW_UP",
    "MINUS",
    "TIMES",
    "CushionLevel",
    "arrow",
    "big_digits",
    "cushion_level",
    "fmt_atr",
    "fmt_atr_range",
    "fmt_change",
    "fmt_eur",
    "fmt_eur_price",
    "fmt_level",
    "fmt_pct",
    "fmt_price",
    "fmt_qty",
    "fmt_stop",
    "fmt_stop_usdt",
    "fmt_usdt_price",
    "round2",
    "signed_eur",
    "signed_pct",
    "sparkline",
    "stop_bar",
    "stop_room_share",
]

ARROW_UP: Final = "▲"
ARROW_DOWN: Final = "▼"
ARROW_FLAT: Final = "·"
MINUS: Final = "−"  # noqa: RUF001 - the typographic minus aligns with "+"
TIMES: Final = "×"  # noqa: RUF001 - reads as "times" in folded rows

_TENTH: Final = Decimal("0.1")
_ZERO: Final = Decimal(0)
_SPARK_BLOCKS: Final = "▁▂▃▄▅▆▇█"
_FLAT_BLOCK: Final = "▄"
_CUSHION_AMBER_FLOOR: Final = Decimal(1)
_CUSHION_GREEN_ABOVE: Final = Decimal(2)
_PRICE_WHOLE_FROM: Final = Decimal(1000)
_PRICE_TENTHS_FROM: Final = Decimal(100)
_PRICE_CENTS_FROM: Final = Decimal(1)


_BIG_GLYPHS: Final[dict[str, tuple[str, str, str]]] = {
    "0": ("┌─┐", "│ │", "└─┘"),
    "1": ("╶┐ ", " │ ", "╶┴╴"),
    "2": ("╶─┐", "┌─┘", "└─╴"),
    "3": ("╶─┐", " ─┤", "╶─┘"),
    "4": ("╷ ╷", "└─┤", "  ╵"),
    "5": ("┌─╴", "└─┐", "╶─┘"),
    "6": ("┌─╴", "├─┐", "└─┘"),
    "7": ("╶─┐", "  │", "  ╵"),
    "8": ("┌─┐", "├─┤", "└─┘"),
    "9": ("┌─┐", "└─┤", "╶─┘"),
    "€": ("╭─╴", "╞═ ", "╰─╴"),
    ",": ("  ", "  ", "▗ "),
    ".": ("  ", "  ", "▗ "),
    MINUS: ("   ", "╶─╴", "   "),
}


def big_digits(text: str) -> tuple[str, str, str]:
    """Return ``text`` drawn three rows tall with box-drawing characters.

    Covers the characters of a EUR level (digits, ``€``, ``,``, ``.``, minus);
    anything else becomes a blank cell.

    Args:
        text: For example ``€1,021.06``.

    Returns:
        The three rows, one string each, glyphs separated by one space.
    """
    blank = ("   ", "   ", "   ")
    glyphs = [_BIG_GLYPHS.get(char, blank) for char in text]
    return (
        " ".join(g[0] for g in glyphs).rstrip(),
        " ".join(g[1] for g in glyphs).rstrip(),
        " ".join(g[2] for g in glyphs).rstrip(),
    )


def round2(value: Decimal) -> Decimal:
    """Round to cents, half up."""
    return to_cents(value)


def arrow(value: Decimal) -> str:
    """Return the direction glyph of ``value`` after rounding to cents."""
    rounded = round2(value)
    if rounded > _ZERO:
        return ARROW_UP
    if rounded < _ZERO:
        return ARROW_DOWN
    return ARROW_FLAT


def _sign(rounded: Decimal) -> str:
    if rounded > _ZERO:
        return "+"
    if rounded < _ZERO:
        return MINUS
    return ""


def signed_eur(value: Decimal) -> str:
    """Return ``+€12.30``, minus-sign ``€1.28`` or ``€0.00`` (no arrow)."""
    rounded = round2(value)
    return f"{_sign(rounded)}€{abs(rounded):,.2f}"


def signed_pct(value: Decimal) -> str:
    """Return ``+0.29%``, minus-sign ``1.10%`` or ``0.00%`` (no arrow)."""
    rounded = round2(value)
    return f"{_sign(rounded)}{abs(rounded):,.2f}%"


def fmt_eur(value: Decimal) -> str:
    """Return a signed EUR change with its arrow, e.g. ``▲ +€12.30``."""
    return f"{arrow(value)} {signed_eur(value)}"


def fmt_pct(value: Decimal) -> str:
    """Return a signed percentage with its arrow, e.g. ``▲ +1.10%``."""
    return f"{arrow(value)} {signed_pct(value)}"


def fmt_change(eur: Decimal, pct: Decimal) -> str:
    """Return a EUR change and its percentage under one arrow.

    The arrow follows the EUR figure; both numbers keep their own sign.

    Args:
        eur: Change in EUR.
        pct: The same change as a percentage.

    Returns:
        For example ``▲ +€12.30 +0.29%``.
    """
    return f"{arrow(eur)} {signed_eur(eur)} {signed_pct(pct)}"


def fmt_level(value: Decimal) -> str:
    """Return an unsigned EUR level, e.g. ``€4,231.50``."""
    rounded = round2(value)
    prefix = MINUS if rounded < _ZERO else ""
    return f"{prefix}€{abs(rounded):,.2f}"


def fmt_price(value: Decimal) -> str:
    """Return a quote-currency price with precision that fits its size.

    Whole units from 1,000, one decimal from 100, two from 1, otherwise four
    significant digits.
    """
    magnitude = abs(value)
    if magnitude >= _PRICE_WHOLE_FROM:
        return f"{value.quantize(Decimal(1), rounding=ROUND_HALF_UP):,}"
    if magnitude >= _PRICE_TENTHS_FROM:
        return f"{value.quantize(_TENTH, rounding=ROUND_HALF_UP):,}"
    if magnitude >= _PRICE_CENTS_FROM:
        return f"{to_cents(value):,}"
    return f"{value:.4g}"


def fmt_eur_price(value: Decimal) -> str:
    """Return a coin price in EUR with the precision of :func:`fmt_price`."""
    return f"€{fmt_price(value)}"


def fmt_stop(price_eur: Decimal, distance_pct: Decimal) -> str:
    """Return the stop as a EUR price and the distance to it, e.g. ``€2,166 · minus 3.9%``.

    Args:
        price_eur: Stop level in EUR.
        distance_pct: Distance from the current price down to the stop, percent
            (positive when the stop sits below the price).

    Returns:
        The price and the signed distance to one decimal.
    """
    return f"{fmt_eur_price(price_eur)} · {_stop_distance(distance_pct)}"


def fmt_usdt_price(value: Decimal) -> str:
    """Return a price in the quote currency labelled as such, e.g. ``2,618 USDT``.

    Used where a price has no EUR value yet (an unpriced fill), so it is never
    shown under a € sign.
    """
    return f"{fmt_price(value)} USDT"


def fmt_stop_usdt(price: Decimal, distance_pct: Decimal) -> str:
    """Return the stop as a USDT price and the distance to it, e.g. ``2,580 USDT · minus 3.4%``.

    Args:
        price: Stop level in the quote currency.
        distance_pct: As for :func:`fmt_stop`.

    Returns:
        The price and the signed distance to one decimal.
    """
    return f"{fmt_usdt_price(price)} · {_stop_distance(distance_pct)}"


def _stop_distance(distance_pct: Decimal) -> str:
    rounded = distance_pct.quantize(_TENTH, rounding=ROUND_HALF_UP)
    sign = MINUS if rounded > _ZERO else "+" if rounded < _ZERO else ""
    return f"{sign}{abs(rounded)}%"


def fmt_qty(value: Decimal) -> str:
    """Return a quantity without trailing zeros, e.g. ``0.035``."""
    return f"{value.normalize():f}"


def fmt_atr(value: Decimal) -> str:
    """Return a distance in ATR to one decimal, e.g. ``1.4 ATR``."""
    return f"{value.quantize(_TENTH, rounding=ROUND_HALF_UP)} ATR"


def fmt_atr_range(value: Decimal) -> str:
    """Return the detail-view distance label: a number, times, average range (ATR)."""
    return f"{value.quantize(_TENTH, rounding=ROUND_HALF_UP)} {TIMES} average range (ATR)"


def sparkline(values: Sequence[Decimal], width: int) -> str:
    """Return a one-line block-character sparkline.

    Longer series are sampled at evenly spaced points (first and last kept);
    a flat series draws a flat mid-height line.

    Args:
        values: Samples, oldest first.
        width: Maximum number of characters.

    Returns:
        ``min(len(values), width)`` block characters; empty for no input.
    """
    if not values or width <= 0:
        return ""
    count = min(len(values), width)
    if count == 1:
        picked = [values[-1]]
    else:
        last = len(values) - 1
        picked = [values[(i * last) // (count - 1)] for i in range(count)]
    low, high = min(picked), max(picked)
    if high == low:
        return _FLAT_BLOCK * count
    top = len(_SPARK_BLOCKS) - 1
    span = high - low
    return "".join(
        _SPARK_BLOCKS[int(((v - low) * top / span).to_integral_value(ROUND_HALF_UP))]
        for v in picked
    )


def stop_room_share(stop: Decimal, current: Decimal, high: Decimal) -> Decimal:
    """Return how much of the room from the stop up to the high is left, in [0, 1].

    Args:
        stop: Stop level.
        current: Current price.
        high: Highest price since entry.

    Returns:
        ``(current - stop) / (high - stop)``, clamped; 1 above a stop that is at
        the high, 0 at or under it.
    """
    if high <= stop:
        return Decimal(1) if current > stop else _ZERO
    return min(max((current - stop) / (high - stop), _ZERO), Decimal(1))


def stop_bar(stop: Decimal, current: Decimal, high: Decimal, cells: int = 8) -> str:
    """Return the cushion bar from the stop (empty) to the high since entry (full).

    Args:
        stop: Stop level.
        current: Current price.
        high: Highest price since entry.
        cells: Bar width in characters, without the end caps.

    Returns:
        For example ``▕██████░░▏``; the fill is clamped to the bar.
    """
    fraction = stop_room_share(stop, current, high)
    filled = int((fraction * cells).to_integral_value(ROUND_HALF_UP))
    return "▕" + "█" * filled + "░" * (cells - filled) + "▏"


class CushionLevel(StrEnum):
    """Colour band of the stop cushion."""

    GREEN = "green"
    AMBER = "amber"
    RED = "red"


def cushion_level(distance_atr: Decimal) -> CushionLevel:
    """Return green above 2 ATR, amber from 1 to 2 ATR, red below 1 ATR."""
    if distance_atr > _CUSHION_GREEN_ABOVE:
        return CushionLevel.GREEN
    if distance_atr >= _CUSHION_AMBER_FLOOR:
        return CushionLevel.AMBER
    return CushionLevel.RED
