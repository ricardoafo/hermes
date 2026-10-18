"""Property and example-based tests for Money (R-5).

Property invariants per code-conventions.md §8:
- add same currency → Success
- add different currency → Failure(CurrencyMismatch)
- add is commutative
- zero is neutral element
- from_str roundtrip

Reference: R-5 (A-5.3, A-5.4, A-5.6, A-5.7).
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import given
from returns.result import Failure, Success

# Import for side-effect: registers type strategy for Money with hypothesis
import tests.strategies.money  # noqa: F401
from hermes.domain.errors import CurrencyMismatch, InvalidLot
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.money import Money
from tests.strategies.money import currency_strategy, money_strategy


class TestMoneyAddSameCurrency:
    """add() with same currency always succeeds."""

    @given(money_strategy(), money_strategy())
    def test_add_same_currency_succeeds(self, a: Money, b: Money) -> None:
        """Adding same-currency Money values always yields a Success with the summed amount."""
        # Force same currency
        b_same = Money(amount=b.amount, currency=a.currency)
        result = a.add(b_same)
        assert isinstance(result, Success)
        unwrapped = result.unwrap()
        assert unwrapped.currency == a.currency
        assert unwrapped.amount == a.amount + b_same.amount


class TestMoneyAddDifferentCurrency:
    """add() with different currencies always fails with CurrencyMismatch."""

    @given(money_strategy(), money_strategy(), currency_strategy(), currency_strategy())
    def test_add_different_currency_fails(
        self, a: Money, b: Money, c1: Currency, c2: Currency
    ) -> None:
        """Adding mismatched-currency Money always yields a Failure wrapping CurrencyMismatch."""
        # Force different currencies — skip if sampled currencies happen to be equal
        if c1 == c2:
            return
        a_mod = Money(amount=a.amount, currency=c1)
        b_mod = Money(amount=b.amount, currency=c2)
        result = a_mod.add(b_mod)
        assert isinstance(result, Failure)
        err = result.failure()
        assert isinstance(err, CurrencyMismatch)
        assert err.expected == c1
        assert err.actual == c2


class TestMoneyAddCommutativity:
    """a.add(b) == b.add(a) for same-currency pairs."""

    @given(money_strategy())
    def test_add_is_commutative(self, a: Money) -> None:
        """a.add(b) and b.add(a) produce the same result — addition is commutative for Money."""
        b = Money(amount=Decimal("1.5"), currency=a.currency)
        ab = a.add(b)
        ba = b.add(a)
        assert isinstance(ab, Success)
        assert isinstance(ba, Success)
        assert ab.unwrap() == ba.unwrap()


class TestMoneyZeroNeutral:
    """Adding zero does not change the value."""

    @given(money_strategy())
    def test_zero_is_neutral(self, a: Money) -> None:
        """Adding zero (right-hand) to any Money value returns the original value unchanged."""
        z = Money.zero(a.currency)
        result = a.add(z)
        assert isinstance(result, Success)
        assert result.unwrap() == a

    @given(money_strategy())
    def test_zero_plus_value_is_value(self, a: Money) -> None:
        """Adding any Money to zero (left-hand) returns the original — zero is the identity."""
        z = Money.zero(a.currency)
        result = z.add(a)
        assert isinstance(result, Success)
        assert result.unwrap() == a


class TestMoneyFromStrRoundtrip:
    """from_str(str(m)) == m for all valid Money values."""

    @given(money_strategy())
    def test_from_str_roundtrip(self, m: Money) -> None:
        """from_str(str(m)) round-trips losslessly for all valid Money values."""
        result = Money.from_str(str(m))
        assert isinstance(result, Success)
        assert result.unwrap() == m


class TestMoneyExampleBased:
    """Example-based tests for factory methods and edge cases."""

    def test_from_str_valid(self) -> None:
        """from_str parses a well-formed 'amount CURRENCY' string into the correct Money fields."""
        result = Money.from_str("100.50 EUR")
        assert isinstance(result, Success)
        m = result.unwrap()
        assert m.amount == Decimal("100.50")
        assert m.currency == Currency.EUR

    def test_from_str_invalid_amount(self) -> None:
        """from_str returns Failure when the amount part is not a valid decimal."""
        error = Money.from_str("abc EUR").failure()
        assert error == InvalidLot(reason="Cannot parse amount 'abc' as Decimal")

    def test_from_str_unknown_currency(self) -> None:
        """from_str returns Failure when the currency ticker is not in the allowlist."""
        error = Money.from_str("100 XYZ").failure()
        assert error == InvalidLot(reason="Unknown currency: 'XYZ'")

    def test_from_str_missing_parts(self) -> None:
        """from_str returns Failure when the input lacks the 'amount CURRENCY' structure."""
        error = Money.from_str("100").failure()
        assert error == InvalidLot(
            reason="Cannot parse Money from '100': expected 'AMOUNT CURRENCY'"
        )

    def test_from_str_extra_parts(self) -> None:
        """from_str rejects three tokens: exactly 'amount CURRENCY' is accepted, nothing more."""
        error = Money.from_str("1 2 EUR").failure()
        assert error == InvalidLot(
            reason="Cannot parse Money from '1 2 EUR': expected 'AMOUNT CURRENCY'"
        )

    def test_zero_factory(self) -> None:
        """Money.zero() produces a Money with amount=0 in the requested currency."""
        z = Money.zero(Currency.BTC)
        assert z.amount == Decimal("0")
        assert z.currency == Currency.BTC

    def test_sub_same_currency(self) -> None:
        """sub() with same-currency operands returns a Success with the arithmetic difference."""
        a = Money(amount=Decimal("10"), currency=Currency.EUR)
        b = Money(amount=Decimal("3"), currency=Currency.EUR)
        result = a.sub(b)
        assert isinstance(result, Success)
        assert result.unwrap().amount == Decimal("7")

    def test_sub_different_currency_fails(self) -> None:
        """sub() with mismatched currencies returns a Failure wrapping a CurrencyMismatch error."""
        a = Money(amount=Decimal("10"), currency=Currency.EUR)
        b = Money(amount=Decimal("3"), currency=Currency.BTC)
        error = a.sub(b).failure()
        assert error == CurrencyMismatch(expected=Currency.EUR, actual=Currency.BTC)

    def test_frozen_immutable(self) -> None:
        """Money is a frozen dataclass — mutating a field after construction raises."""
        m = Money(amount=Decimal("100"), currency=Currency.EUR)
        with pytest.raises((AttributeError, TypeError)):
            m.amount = Decimal("200")  # type: ignore[misc]

    def test_equality_by_value(self) -> None:
        """Two Money instances with identical amount and currency are equal (value semantics)."""
        m1 = Money(amount=Decimal("50"), currency=Currency.EUR)
        m2 = Money(amount=Decimal("50"), currency=Currency.EUR)
        assert m1.amount == Decimal("50")
        assert m1.currency == m2.currency
        assert m1 == m2

    def test_str_representation(self) -> None:
        """str(Money) produces the canonical 'amount CURRENCY' format required for serialisation."""
        m = Money(amount=Decimal("42.5"), currency=Currency.BTC)
        assert str(m) == "42.5 BTC"
