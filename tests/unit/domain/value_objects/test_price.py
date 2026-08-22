"""Property and example-based tests for Price.

Reference: R-5 (A-5.3, A-5.6, A-5.7).
"""

from __future__ import annotations

from decimal import Decimal

from hypothesis import given
from returns.result import Failure, Success

import tests.strategies.price  # noqa: F401
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
        result = Price.from_str(str(p))
        assert isinstance(result, Success)
        assert result.unwrap() == p

    def test_from_str_valid(self) -> None:
        result = Price.from_str("50000.00 BTC/USDT")
        assert isinstance(result, Success)
        p = result.unwrap()
        assert p.amount == Decimal("50000.00")
        assert p.pair == Pair(base=Currency.BTC, quote=Currency.USDT)

    def test_from_str_invalid_amount(self) -> None:
        result = Price.from_str("abc BTC/USDT")
        assert isinstance(result, Failure)

    def test_from_str_invalid_pair(self) -> None:
        result = Price.from_str("50000 DOGE/XYZ")
        assert isinstance(result, Failure)

    def test_from_str_missing_parts(self) -> None:
        result = Price.from_str("50000")
        assert isinstance(result, Failure)


class TestPriceApplyTo:
    """apply_to() returns quote-currency Money."""

    def test_apply_to_matching_asset(self) -> None:
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
        price = Price(
            amount=Decimal("50000"),
            pair=Pair(base=Currency.BTC, quote=Currency.USDT),
        )
        qty = Quantity(amount=Decimal("2"), asset=Currency.ETH)
        result = price.apply_to(qty)
        assert isinstance(result, Failure)

    def test_apply_to_zero_quantity(self) -> None:
        price = Price(
            amount=Decimal("50000"),
            pair=Pair(base=Currency.BTC, quote=Currency.USDT),
        )
        qty = Quantity.zero(Currency.BTC)
        result = price.apply_to(qty)
        assert isinstance(result, Success)
        assert result.unwrap() == Money.zero(Currency.USDT)

    def test_str_representation(self) -> None:
        price = Price(
            amount=Decimal("50000"),
            pair=Pair(base=Currency.BTC, quote=Currency.USDT),
        )
        assert str(price) == "50000 BTC/USDT"
