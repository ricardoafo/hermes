"""Property and example-based tests for Weight.

Reference: R-5 (A-5.3, A-5.6, A-5.7).
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st
from returns.result import Failure, Success

import tests.strategies.weight  # noqa: F401
from hermes.domain.value_objects.weight import Weight
from tests.strategies.weight import weight_strategy


class TestWeightConstraint:
    """Weight must be in [0, 1]."""

    @given(weight_strategy())
    def test_weight_in_range(self, w: Weight) -> None:
        assert Decimal("0") <= w.value <= Decimal("1")

    @given(
        st.decimals(
            min_value=Decimal("1.0000001"),
            max_value=Decimal("1000"),
            allow_nan=False,
            allow_infinity=False,
            places=8,
        )
    )
    def test_above_one_raises(self, v: Decimal) -> None:
        with pytest.raises(ValueError):
            Weight(value=v)

    @given(
        st.decimals(
            min_value=Decimal("-1000"),
            max_value=Decimal("-0.0000001"),
            allow_nan=False,
            allow_infinity=False,
            places=8,
        )
    )
    def test_below_zero_raises(self, v: Decimal) -> None:
        with pytest.raises(ValueError):
            Weight(value=v)


class TestWeightFactories:
    """from_str, from_decimal, zero, one factories."""

    @given(weight_strategy())
    def test_from_str_roundtrip(self, w: Weight) -> None:
        result = Weight.from_str(str(w))
        assert isinstance(result, Success)
        assert result.unwrap() == w

    def test_from_str_valid(self) -> None:
        result = Weight.from_str("0.25")
        assert isinstance(result, Success)
        assert result.unwrap().value == Decimal("0.25")

    def test_from_str_invalid_decimal(self) -> None:
        result = Weight.from_str("abc")
        assert isinstance(result, Failure)

    def test_from_str_out_of_range(self) -> None:
        result = Weight.from_str("1.5")
        assert isinstance(result, Failure)

    @given(weight_strategy())
    def test_from_decimal_roundtrip(self, w: Weight) -> None:
        result = Weight.from_decimal(w.value)
        assert isinstance(result, Success)
        assert result.unwrap() == w

    def test_from_decimal_out_of_range(self) -> None:
        result = Weight.from_decimal(Decimal("2"))
        assert isinstance(result, Failure)

    def test_zero_factory(self) -> None:
        z = Weight.zero()
        assert z.value == Decimal("0")

    def test_one_factory(self) -> None:
        o = Weight.one()
        assert o.value == Decimal("1")
