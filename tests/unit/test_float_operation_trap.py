"""The unit tier runs with ``decimal.FloatOperation`` trapped (issue #842).

``tests/unit/conftest.py`` sets the trap for every unit test, so a float that
reaches money maths, ``Decimal(0.1)`` or a ``Decimal`` compared with a float,
fails the test at that line instead of carrying binary rounding error.
"""

import decimal
from decimal import Decimal

import pytest

_TENTH = float("0.1")


def test_building_a_decimal_from_a_float_raises_float_operation() -> None:
    """Invariant: Decimal(float) raises inside a unit test, so a float cannot become money."""
    with pytest.raises(decimal.FloatOperation):
        Decimal(_TENTH)


def test_ordering_a_decimal_against_a_float_raises_float_operation() -> None:
    """Invariant: comparing a Decimal with a float by order raises inside a unit test."""
    with pytest.raises(decimal.FloatOperation):
        _ = Decimal("1") < _TENTH


def test_decimal_from_text_is_unaffected_by_the_trap() -> None:
    """Invariant: the trap refuses floats only; Decimal("0.1") stays exact."""
    assert Decimal("0.1") + Decimal("0.2") == Decimal("0.3")
