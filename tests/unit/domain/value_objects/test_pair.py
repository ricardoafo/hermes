"""Property and example-based tests for Pair.

Reference: R-5 (A-5.3, A-5.6, A-5.7).
"""

import pytest
from hypothesis import given
from returns.result import Failure, Success

import tests.strategies.pair  # noqa: F401
from hermes.domain.errors import InvalidLot
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair
from tests.strategies.pair import pair_strategy


class TestPairFromStrSlash:
    """from_str() with slash-separated format."""

    @given(pair_strategy())
    def test_from_str_slash_roundtrip(self, p: Pair) -> None:
        """from_str(str(p)) round-trips losslessly for all valid Pair values."""
        result = Pair.from_str(str(p))
        assert isinstance(result, Success)
        assert result.unwrap() == p

    def test_from_str_btc_usdt(self) -> None:
        """from_str parses 'BTC/USDT' into a Pair with the correct base and quote currencies."""
        result = Pair.from_str("BTC/USDT")
        assert isinstance(result, Success)
        p = result.unwrap()
        assert p.base == Currency.BTC
        assert p.quote == Currency.USDT

    def test_from_str_unknown_base_fails(self) -> None:
        """from_str returns Failure when the base currency is not in the allowlist."""
        result = Pair.from_str("DOGE/USDT")
        assert result == Failure(
            InvalidLot(reason="Unknown base currency 'DOGE' in pair 'DOGE/USDT'")
        )

    def test_from_str_same_base_quote_fails(self) -> None:
        """from_str returns Failure when base and quote are the same currency (self-pair)."""
        result = Pair.from_str("BTC/BTC")
        assert result == Failure(
            InvalidLot(reason="Pair base and quote must differ, got <Currency.BTC: 'BTC'> twice")
        )


class TestPairFromStrConcatenated:
    """from_str() with concatenated format (BTCUSDT)."""

    def test_btcusdt_parses(self) -> None:
        """from_str accepts the exchange-native concatenated format 'BTCUSDT' and splits it."""
        result = Pair.from_str("BTCUSDT")
        assert isinstance(result, Success)
        p = result.unwrap()
        assert p.base == Currency.BTC
        assert p.quote == Currency.USDT

    def test_etheur_parses(self) -> None:
        """from_str resolves 'ETHEUR' unambiguously into ETH base and EUR quote."""
        result = Pair.from_str("ETHEUR")
        assert isinstance(result, Success)
        p = result.unwrap()
        assert p.base == Currency.ETH
        assert p.quote == Currency.EUR

    def test_ambiguous_concatenated_fails(self) -> None:
        """from_str returns Failure for concatenated strings that do not resolve to a known pair."""
        # Not a known pair combination
        result = Pair.from_str("DOGEXYZ")
        assert result == Failure(
            InvalidLot(reason="Cannot split concatenated pair 'DOGEXYZ' into known currencies")
        )

    def test_unknown_quote_fails(self) -> None:
        """from_str names the unknown quote currency when only the quote is unknown."""
        result = Pair.from_str("BTC/DOGE")
        assert result == Failure(
            InvalidLot(reason="Unknown quote currency 'DOGE' in pair 'BTC/DOGE'")
        )


class TestPairConstraints:
    """Verify Pair invariants."""

    def test_base_quote_must_differ(self) -> None:
        """Constructing a Pair where base equals quote raises ValueError (self-pairs forbidden)."""
        with pytest.raises(ValueError):
            Pair(base=Currency.BTC, quote=Currency.BTC)

    def test_str_is_base_slash_quote(self) -> None:
        """str(Pair) produces the canonical 'BASE/QUOTE' format required for exchange API calls."""
        p = Pair(base=Currency.BTC, quote=Currency.USDT)
        assert str(p) == "BTC/USDT"

    def test_equality_by_value(self) -> None:
        """Two Pair instances with matching base and quote are equal (value semantics)."""
        p1 = Pair(base=Currency.ETH, quote=Currency.USDT)
        p2 = Pair(base=Currency.ETH, quote=Currency.USDT)
        assert p1.base == Currency.ETH
        assert p1.quote == p2.quote
        assert p1 == p2

    def test_different_pairs_not_equal(self) -> None:
        """Two Pair instances with different base currencies are not equal."""
        p1 = Pair(base=Currency.BTC, quote=Currency.USDT)
        p2 = Pair(base=Currency.ETH, quote=Currency.USDT)
        assert p1 != p2
