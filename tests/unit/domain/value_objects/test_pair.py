"""Property and example-based tests for Pair.

Reference: R-5 (A-5.3, A-5.6, A-5.7).
"""

from __future__ import annotations

import pytest
from hypothesis import given
from returns.result import Failure, Success

import tests.strategies.pair  # noqa: F401
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair
from tests.strategies.pair import pair_strategy


class TestPairFromStrSlash:
    """from_str() with slash-separated format."""

    @given(pair_strategy())
    def test_from_str_slash_roundtrip(self, p: Pair) -> None:
        result = Pair.from_str(str(p))
        assert isinstance(result, Success)
        assert result.unwrap() == p

    def test_from_str_btc_usdt(self) -> None:
        result = Pair.from_str("BTC/USDT")
        assert isinstance(result, Success)
        p = result.unwrap()
        assert p.base == Currency.BTC
        assert p.quote == Currency.USDT

    def test_from_str_unknown_base_fails(self) -> None:
        result = Pair.from_str("DOGE/USDT")
        assert isinstance(result, Failure)

    def test_from_str_same_base_quote_fails(self) -> None:
        result = Pair.from_str("BTC/BTC")
        assert isinstance(result, Failure)


class TestPairFromStrConcatenated:
    """from_str() with concatenated format (BTCUSDT)."""

    def test_btcusdt_parses(self) -> None:
        result = Pair.from_str("BTCUSDT")
        assert isinstance(result, Success)
        p = result.unwrap()
        assert p.base == Currency.BTC
        assert p.quote == Currency.USDT

    def test_etheur_parses(self) -> None:
        result = Pair.from_str("ETHEUR")
        assert isinstance(result, Success)
        p = result.unwrap()
        assert p.base == Currency.ETH
        assert p.quote == Currency.EUR

    def test_ambiguous_concatenated_fails(self) -> None:
        # Not a known pair combination
        result = Pair.from_str("DOGEXYZ")
        assert isinstance(result, Failure)


class TestPairConstraints:
    """Verify Pair invariants."""

    def test_base_quote_must_differ(self) -> None:
        with pytest.raises(ValueError):
            Pair(base=Currency.BTC, quote=Currency.BTC)

    def test_str_is_base_slash_quote(self) -> None:
        p = Pair(base=Currency.BTC, quote=Currency.USDT)
        assert str(p) == "BTC/USDT"

    def test_equality_by_value(self) -> None:
        p1 = Pair(base=Currency.ETH, quote=Currency.USDT)
        p2 = Pair(base=Currency.ETH, quote=Currency.USDT)
        assert p1 == p2

    def test_different_pairs_not_equal(self) -> None:
        p1 = Pair(base=Currency.BTC, quote=Currency.USDT)
        p2 = Pair(base=Currency.ETH, quote=Currency.USDT)
        assert p1 != p2
