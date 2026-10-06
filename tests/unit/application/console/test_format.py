"""Console number and glyph formatting.

Invariants: € and % show two decimals; every change carries a sign and an
arrow that agree with the rounded value; levels are unsigned; sparklines and
stop bars keep their width and character set.
"""

# The console prints the typographic minus (U+2212) and times sign on purpose.
# ruff: noqa: RUF001

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hermes.application.console.format import (
    CushionLevel,
    cushion_level,
    fmt_atr,
    fmt_change,
    fmt_eur,
    fmt_level,
    fmt_pct,
    fmt_price,
    fmt_qty,
    signed_eur,
    sparkline,
    stop_bar,
    stop_room_share,
)

_amounts = st.decimals(min_value=-1_000_000, max_value=1_000_000, places=4, allow_nan=False)
_EUR_RE = re.compile(r"^(?P<arrow>[▲▼·]) (?P<sign>[+−]?)€(?P<num>[\d,]+\.\d{2})$")


def _parse_eur(text: str) -> tuple[str, Decimal]:
    match = _EUR_RE.match(text)
    assert match is not None, text
    value = Decimal(match["num"].replace(",", ""))
    return match["arrow"], -value if match["sign"] == "−" else value


@given(value=_amounts)
def test_eur_round_trips_to_the_cent(value: Decimal) -> None:
    """The printed amount equals the value rounded half up to cents."""
    _, printed = _parse_eur(fmt_eur(value))
    assert printed == value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


@given(value=_amounts)
def test_arrow_agrees_with_rounded_sign(value: Decimal) -> None:
    """Up arrow for a positive rounded value, down for negative, dot for zero."""
    glyph, printed = _parse_eur(fmt_eur(value))
    assert glyph == ("▲" if printed > 0 else "▼" if printed < 0 else "·")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (Decimal("12.3"), "▲ +€12.30"),
        (Decimal("-1.275"), "▼ −€1.28"),
        (Decimal("1234.5"), "▲ +€1,234.50"),
        (Decimal(0), "· €0.00"),
    ],
)
def test_eur_examples(value: Decimal, expected: str) -> None:
    """Signed EUR uses the typographic minus, thousands commas and two decimals."""
    assert fmt_eur(value) == expected


def test_tiny_loss_rounds_to_flat_zero() -> None:
    """A loss under half a cent shows as a flat zero, never a down arrow on €0.00."""
    assert fmt_eur(Decimal("-0.004")) == "· €0.00"


def test_percent_has_two_decimals_and_arrow() -> None:
    """Percentages carry sign, arrow and two decimals."""
    assert fmt_pct(Decimal("-1.1")) == "▼ −1.10%"


def test_change_puts_one_arrow_before_eur_and_percent() -> None:
    """The hero change shows the EUR and percent figures under one arrow."""
    assert fmt_change(Decimal("12.30"), Decimal("0.29")) == "▲ +€12.30 +0.29%"


def test_level_is_unsigned() -> None:
    """A level such as total value has no sign and no arrow."""
    assert fmt_level(Decimal("4231.5")) == "€4,231.50"


def test_signed_eur_has_no_arrow() -> None:
    """The arrow-free form keeps the sign."""
    assert signed_eur(Decimal("-3.1")) == "−€3.10"


@pytest.mark.parametrize(
    ("price", "expected"),
    [
        (Decimal("62400.4"), "62,400"),
        (Decimal("146.54"), "146.5"),
        (Decimal("2.345"), "2.35"),
        (Decimal("0.341234"), "0.3412"),
    ],
)
def test_price_precision_follows_magnitude(price: Decimal, expected: str) -> None:
    """Prices drop decimals as they grow, keeping columns narrow."""
    assert fmt_price(price) == expected


def test_quantity_drops_trailing_zeros() -> None:
    """Sizes print without trailing zeros."""
    assert fmt_qty(Decimal("0.03500")) == "0.035"


def test_atr_has_one_decimal() -> None:
    """Stop distances in ATR show one decimal."""
    assert fmt_atr(Decimal("1.4193")) == "1.4 ATR"


@given(
    values=st.lists(
        st.decimals(min_value=0, max_value=10_000, places=2, allow_nan=False),
        min_size=1,
        max_size=200,
    ),
    width=st.integers(min_value=1, max_value=60),
)
def test_sparkline_fits_width_with_block_characters(values: list[Decimal], width: int) -> None:
    """The sparkline is at most ``width`` long and uses only block characters."""
    line = sparkline(values, width)
    assert len(line) == min(len(values), width)
    assert set(line) <= set("▁▂▃▄▅▆▇█")


def test_sparkline_spans_lowest_to_highest_block() -> None:
    """The minimum maps to the lowest block and the maximum to the highest."""
    assert sparkline([Decimal(1), Decimal(5), Decimal(9)], 10) == "▁▅█"


def test_flat_sparkline_is_mid_height() -> None:
    """A flat series draws a flat line rather than dividing by zero."""
    assert sparkline([Decimal(7)] * 4, 10) == "▄▄▄▄"


def test_empty_sparkline_is_empty() -> None:
    """No samples draw nothing."""
    assert sparkline([], 10) == ""


@given(
    stop=st.decimals(min_value=1, max_value=1000, places=2, allow_nan=False),
    current=st.decimals(min_value=0, max_value=2000, places=2, allow_nan=False),
    gap=st.decimals(min_value=0, max_value=1000, places=2, allow_nan=False),
)
def test_stop_bar_keeps_its_width(stop: Decimal, current: Decimal, gap: Decimal) -> None:
    """The stop bar is always eight cells between its caps, whatever the prices."""
    bar = stop_bar(stop, current, stop + gap)
    assert len(bar) == 10
    assert bar[0] == "▕"
    assert bar[-1] == "▏"


def test_stop_bar_fill_is_the_share_of_cushion_left() -> None:
    """Three quarters of the way from stop to high fills six of eight cells."""
    assert stop_bar(Decimal(100), Decimal(175), Decimal(200)) == "▕██████░░▏"


def test_stop_room_share_is_the_room_left_up_to_the_high() -> None:
    """Room left is (current - stop) / (high - stop), clamped; a stop at the high is all room."""
    assert (
        stop_room_share(Decimal(100), Decimal(175), Decimal(200)),
        stop_room_share(Decimal(100), Decimal(250), Decimal(200)),
        stop_room_share(Decimal(100), Decimal(101), Decimal(100)),
    ) == (Decimal("0.75"), Decimal(1), Decimal(1))


def test_stop_bar_is_empty_at_or_below_the_stop() -> None:
    """At the stop there is no cushion left."""
    assert stop_bar(Decimal(100), Decimal(90), Decimal(200)) == "▕░░░░░░░░▏"


@pytest.mark.parametrize(
    ("distance", "level"),
    [
        (Decimal("2.01"), CushionLevel.GREEN),
        (Decimal(2), CushionLevel.AMBER),
        (Decimal(1), CushionLevel.AMBER),
        (Decimal("0.99"), CushionLevel.RED),
    ],
)
def test_cushion_bands(distance: Decimal, level: CushionLevel) -> None:
    """Green above 2 ATR, amber from 1 to 2, red below 1."""
    assert cushion_level(distance) is level
