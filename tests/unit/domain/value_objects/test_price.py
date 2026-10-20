"""Property and example-based tests for Price.

Reference: R-5 (A-5.3, A-5.6, A-5.7).
"""

from decimal import Decimal

from hypothesis import given
from returns.result import Success

import tests.strategies.price  # noqa: F401
from hermes.domain.errors import InvalidLot
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.price import Price
from hermes.domain.value_objects.quantity import Quantity
from tests.strategies.price import price_strategy


class TestPriceFromStr:
    """from_str() contract."""

    @given(price_strategy())
    def test_from_str_roundtrip(self, p: Price) -> None:
        """from_str(str(p)) round-trips losslessly for all valid Price values."""
        result = Price.from_str(str(p))
        assert isinstance(result, Success)
        assert result.unwrap() == p

    def test_from_str_valid(self) -> None:
        """from_str parses a well-formed 'amount BASE/QUOTE' string into the correct fields."""
        result = Price.from_str("50000.00 BTC/USDT")
        assert isinstance(result, Success)
        p = result.unwrap()
        assert p.amount == Decimal("50000.00")
        assert p.pair == Pair(base=Currency.BTC, quote=Currency.USDT)

    def test_from_str_invalid_amount(self) -> None:
        """from_str returns Failure when the amount part is not a valid decimal."""
        error = Price.from_str("abc BTC/USDT").failure()
        assert error == InvalidLot(reason="Cannot parse amount 'abc' as Decimal")

    def test_from_str_invalid_pair(self) -> None:
        """from_str returns Failure when the pair string contains unknown currency tickers."""
        error = Price.from_str("50000 DOGE/XYZ").failure()
        assert error == InvalidLot(reason="Unknown base currency 'DOGE' in pair 'DOGE/XYZ'")

    def test_from_str_missing_parts(self) -> None:
        """from_str returns Failure when the input lacks the expected 'amount PAIR' structure."""
        error = Price.from_str("50000").failure()
        assert error == InvalidLot(reason="Cannot parse Price from '50000': expected 'AMOUNT PAIR'")

    def test_from_str_extra_parts(self) -> None:
        """from_str rejects three tokens: exactly 'amount PAIR' is accepted, nothing more."""
        error = Price.from_str("50000 BTC/USDT now").failure()
        assert error == InvalidLot(
            reason="Cannot parse Price from '50000 BTC/USDT now': expected 'AMOUNT PAIR'"
        )


class TestPriceApplyTo:
    """apply_to() returns quote-currency Money."""

    def test_apply_to_matching_asset(self) -> None:
        """apply_to() returns quote-currency Money (price * qty) when the quantity asset matches."""
        price = Price(
            amount=Decimal("50000"),
            pair=Pair(base=Currency.BTC, quote=Currency.USDT),
        )
        qty = Quantity(amount=Decimal("2"), asset=Currency.BTC)
        result = price.apply_to(qty)
        assert isinstance(result, Success)
        money = result.unwrap()
        assert money.amount == Decimal("100000")
        assert money.currency == Currency.USDT

    def test_apply_to_mismatched_asset_fails(self) -> None:
        """apply_to() returns Failure when the Quantity's asset does not match the Price's base."""
        price = Price(
            amount=Decimal("50000"),
            pair=Pair(base=Currency.BTC, quote=Currency.USDT),
        )
        qty = Quantity(amount=Decimal("2"), asset=Currency.ETH)
        error = price.apply_to(qty).failure()
        assert error == InvalidLot(
            reason=(
                "Price.apply_to: quantity asset <Currency.ETH: 'ETH'> "
                "does not match pair base <Currency.BTC: 'BTC'>"
            )
        )

    def test_apply_to_zero_quantity(self) -> None:
        """apply_to() with zero quantity returns zero Money in the quote currency."""
        price = Price(
            amount=Decimal("50000"),
            pair=Pair(base=Currency.BTC, quote=Currency.USDT),
        )
        qty = Quantity.zero(Currency.BTC)
        result = price.apply_to(qty)
        assert isinstance(result, Success)
        assert result.unwrap() == Money.zero(Currency.USDT)

    def test_str_representation(self) -> None:
        """str(Price) produces the canonical 'amount BASE/QUOTE' format."""
        price = Price(
            amount=Decimal("50000"),
            pair=Pair(base=Currency.BTC, quote=Currency.USDT),
        )
        assert str(price) == "50000 BTC/USDT"
