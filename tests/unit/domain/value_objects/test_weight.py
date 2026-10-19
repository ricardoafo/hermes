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
from hermes.domain.errors import InvalidLot
from hermes.domain.value_objects.weight import Weight
from tests.strategies.weight import weight_strategy


class TestWeightConstraint:
    """Weight must be in [0, 1]."""

    @given(weight_strategy())
    def test_weight_in_range(self, w: Weight) -> None:
        """Weight.value is always in the closed interval [0, 1] — out-of-range values rejected."""
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
        """Constructing Weight with value > 1 raises ValueError — the upper bound is enforced."""
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
        """Constructing Weight with value < 0 raises ValueError — negative weights are forbidden."""
        with pytest.raises(ValueError):
            Weight(value=v)


class TestWeightFactories:
    """from_str and zero factories."""

    @given(weight_strategy())
    def test_from_str_roundtrip(self, w: Weight) -> None:
        """from_str(str(w)) round-trips losslessly for all valid Weight values."""
        result = Weight.from_str(str(w))
        assert isinstance(result, Success)
        assert result.unwrap() == w

    def test_from_str_valid(self) -> None:
        """from_str parses a valid decimal string into the correct Weight value."""
        result = Weight.from_str("0.25")
        assert isinstance(result, Success)
        assert result.unwrap().value == Decimal("0.25")

    def test_from_str_invalid_decimal(self) -> None:
        """from_str returns Failure when the input is not a valid decimal."""
        result = Weight.from_str("abc")
        assert result == Failure(
            InvalidLot(reason="Cannot parse Weight from 'abc': not a valid Decimal")
        )

    def test_from_str_out_of_range(self) -> None:
        """from_str returns Failure when the decimal is outside the [0, 1] range."""
        result = Weight.from_str("1.5")
        assert result == Failure(InvalidLot(reason="Weight must be in [0, 1], got Decimal('1.5')"))

    @pytest.mark.parametrize("raw", ["-0.01", "1.01"])
    def test_from_str_just_outside_range(self, raw: str) -> None:
        """from_str rejects values one hundredth beyond either end of [0, 1]."""
        result = Weight.from_str(raw)
        assert result == Failure(
            InvalidLot(reason=f"Weight must be in [0, 1], got Decimal('{raw}')")
        )

    @pytest.mark.parametrize("raw", ["0", "1"])
    def test_from_str_bounds_accepted(self, raw: str) -> None:
        """from_str accepts both ends of [0, 1] (the interval is closed)."""
        assert Weight.from_str(raw) == Success(Weight(value=Decimal(raw)))

    def test_zero_factory(self) -> None:
        """Weight.zero() produces a Weight with value exactly 0."""
        z = Weight.zero()
        assert z.value == Decimal("0")
