"""Tests for the cent grid (`hermes.domain.value_objects.cents`).

Invariant (issue #1180): every amount hermes shows or files is rounded to the
cent half up, so a half cent always rounds away from zero and no screen rounds
the same amount differently.
"""

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hermes.domain.value_objects.cents import cents_text, to_cents


@pytest.mark.parametrize(
    ("amount", "expected"),
    [
        ("0.125", "0.13"),  # half even would give 0.12
        ("0.135", "0.14"),
        ("-0.125", "-0.13"),
        ("0.005", "0.01"),
        ("0.0049999", "0.00"),
        ("1234.5", "1234.50"),
    ],
)
def test_half_a_cent_rounds_away_from_zero(amount: str, expected: str) -> None:
    """Ties round up in size, the way a tax form rounds; below a tie rounds down."""
    assert to_cents(Decimal(amount)) == Decimal(expected)


def test_cents_text_keeps_two_decimals_and_no_separator() -> None:
    """``-1234.5`` reads ``-1234.50``: CSV and figures files parse it back."""
    assert cents_text(Decimal("-1234.5")) == "-1234.50"


@given(st.decimals(min_value=-(10**9), max_value=10**9, places=6))
def test_rounding_moves_an_amount_by_at_most_half_a_cent(amount: Decimal) -> None:
    """The result has two decimals and is within half a cent of the amount."""
    rounded = to_cents(amount)
    assert (rounded.as_tuple().exponent, abs(rounded - amount) <= Decimal("0.005")) == (-2, True)
