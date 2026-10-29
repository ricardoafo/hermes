"""Tests for the Currency value object.

Reference: R-5 (A-5.3, A-5.6, A-5.7).
"""

from hypothesis import given
from hypothesis import strategies as st
from returns.result import Failure, Success

from hermes.domain.errors import InvalidLot
from hermes.domain.value_objects.currency import Currency


class TestCurrencyFromStr:
    """from_str() classmethod contract."""

    @given(st.sampled_from(list(Currency)))
    def test_from_str_known_currency_succeeds(self, c: Currency) -> None:
        """from_str succeeds for every ticker in the Currency allowlist."""
        result = Currency.from_str(c.value)
        assert isinstance(result, Success)
        assert result.unwrap() == c

    def test_from_str_unknown_returns_failure(self) -> None:
        """from_str returns Failure for a ticker that is not in the allowlist."""
        result = Currency.from_str("DOGE")
        assert result == Failure(InvalidLot(reason="Unknown currency: 'DOGE'"))

    def test_from_str_empty_returns_failure(self) -> None:
        """from_str returns Failure for an empty string — empty tickers are never valid."""
        result = Currency.from_str("")
        assert result == Failure(InvalidLot(reason="Unknown currency: ''"))

    def test_from_str_lowercase_accepted(self) -> None:
        """from_str is case-insensitive — normalises to upper."""
        result = Currency.from_str("eur")
        assert isinstance(result, Success)
        assert result.unwrap() == Currency.EUR

    def test_from_str_mixed_case(self) -> None:
        """from_str accepts mixed-case input and normalises it to the canonical uppercase ticker."""
        result = Currency.from_str("Btc")
        assert isinstance(result, Success)
        assert result.unwrap() == Currency.BTC


class TestCurrencyAllowlist:
    """Verify the full allowlist matches the spec (EUR, USDT, USDC, BTC, ETH, SOL)."""

    def test_allowlist_contains_expected_members(self) -> None:
        """Currency allowlist contains exactly the 6 spec-required members — no more, no fewer."""
        actual = {c.value for c in Currency}
        assert actual == {"EUR", "USDT", "USDC", "BTC", "ETH", "SOL"}

    @given(st.sampled_from(list(Currency)))
    def test_str_is_the_bare_ticker(self, c: Currency) -> None:
        """str(currency) is the bare ticker, so Pair renders as 'BTC/USDT'."""
        assert str(c) == c.value
