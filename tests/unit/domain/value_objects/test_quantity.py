"""Property and example-based tests for Quantity.

Reference: R-5 (A-5.3, A-5.6, A-5.7).
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import given
from returns.result import Success

import tests.strategies.quantity  # noqa: F401
from hermes.domain.errors import InvalidLot
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.quantity import Quantity
from tests.strategies.quantity import quantity_strategy


class TestQuantityConstraint:
    """Amount must be non-negative."""

    @given(quantity_strategy())
    def test_quantity_amount_non_negative(self, q: Quantity) -> None:
        """Quantity.amount is always non-negative — negative quantities cannot be constructed."""
        assert q.amount >= Decimal("0")

    def test_negative_amount_raises(self) -> None:
        """Constructing Quantity with a negative amount raises ValueError."""
        with pytest.raises(ValueError):
            Quantity(amount=Decimal("-1"), asset=Currency.BTC)


class TestQuantityFactories:
    """from_str and zero factories."""

    @given(quantity_strategy())
    def test_from_str_roundtrip(self, q: Quantity) -> None:
        """from_str(str(q)) round-trips losslessly for all valid Quantity values."""
        result = Quantity.from_str(str(q))
        assert isinstance(result, Success)
        assert result.unwrap() == q

    def test_from_str_valid(self) -> None:
        """from_str parses a well-formed 'amount ASSET' string into the correct Quantity fields."""
        result = Quantity.from_str("0.5 BTC")
        assert isinstance(result, Success)
        q = result.unwrap()
        assert q.amount == Decimal("0.5")
        assert q.asset == Currency.BTC

    def test_from_str_negative_fails(self) -> None:
        """from_str returns Failure for negative amounts — Quantity must be non-negative."""
        error = Quantity.from_str("-1 BTC").failure()
        assert error == InvalidLot(reason="Quantity amount must be ≥ 0, got Decimal('-1')")

    def test_from_str_zero_is_accepted(self) -> None:
        """from_str accepts exactly zero: the non-negative rule rejects only amounts below 0."""
        assert Quantity.from_str("0 BTC").unwrap() == Quantity.zero(Currency.BTC)

    def test_from_str_invalid_amount(self) -> None:
        """from_str returns Failure when the amount part is not a valid decimal."""
        error = Quantity.from_str("abc BTC").failure()
        assert error == InvalidLot(reason="Cannot parse amount 'abc' as Decimal")

    def test_from_str_unknown_currency(self) -> None:
        """from_str returns Failure when the asset ticker is not in the currency allowlist."""
        error = Quantity.from_str("1 DOGE").failure()
        assert error == InvalidLot(reason="Unknown currency: 'DOGE'")

    def test_from_str_missing_parts(self) -> None:
        """from_str returns Failure when the input lacks the expected 'amount ASSET' structure."""
        error = Quantity.from_str("1").failure()
        assert error == InvalidLot(reason="Cannot parse Quantity from '1': expected 'AMOUNT ASSET'")

    def test_from_str_extra_parts(self) -> None:
        """from_str rejects three tokens: exactly 'amount ASSET' is accepted, nothing more."""
        error = Quantity.from_str("1 BTC now").failure()
        assert error == InvalidLot(
            reason="Cannot parse Quantity from '1 BTC now': expected 'AMOUNT ASSET'"
        )

    def test_zero_factory(self) -> None:
        """Quantity.zero() produces a Quantity with amount=0 in the requested asset."""
        z = Quantity.zero(Currency.ETH)
        assert z.amount == Decimal("0")
        assert z.asset == Currency.ETH

    def test_str_representation(self) -> None:
        """str(Quantity) produces the canonical 'amount ASSET' format."""
        q = Quantity(amount=Decimal("0.5"), asset=Currency.BTC)
        assert str(q) == "0.5 BTC"
