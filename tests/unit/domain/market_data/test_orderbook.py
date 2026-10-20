"""Tests for :class:`Orderbook` and :class:`OrderbookLevel`.

Every test protects one invariant of the domain orderbook contract:

- OrderbookLevel rejects non-positive price or quantity.
- Orderbook requires UTC-aware snapshot_time.
- Orderbook requires at least one level on either side.
- Bids must be strictly descending; asks strictly ascending.
- The book must be uncrossed (best_bid < best_ask).
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from hermes.domain.market_data.orderbook import Orderbook, OrderbookLevel
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair

_BTC_USDT = Pair(base=Currency.BTC, quote=Currency.USDT)
_NOW = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)


class TestOrderbookLevel:
    """Level construction rejects non-positive numerics."""

    def test_zero_price_raises(self) -> None:
        """A zero-price level cannot appear in a real book — rejected."""
        with pytest.raises(ValueError, match="price must be > 0"):
            OrderbookLevel(price=Decimal("0"), quantity=Decimal("1"))

    def test_zero_quantity_raises(self) -> None:
        """A zero-size level would be a book delete, not a snapshot row — rejected."""
        with pytest.raises(ValueError, match="quantity must be > 0"):
            OrderbookLevel(price=Decimal("50000"), quantity=Decimal("0"))

    def test_negative_price_raises(self) -> None:
        """Negative price is nonsensical — rejected."""
        with pytest.raises(ValueError, match="price must be > 0"):
            OrderbookLevel(price=Decimal("-1"), quantity=Decimal("1"))


class TestOrderbookInvariants:
    """Orderbook enforces ordering and non-crossing on construction."""

    def _level(self, price: str, qty: str = "1") -> OrderbookLevel:
        return OrderbookLevel(price=Decimal(price), quantity=Decimal(qty))

    def test_naive_snapshot_time_raises(self) -> None:
        """A naive datetime is rejected — hermes stores UTC-aware timestamps only."""
        with pytest.raises(ValueError, match="snapshot_time must be UTC-aware"):
            Orderbook(
                pair=_BTC_USDT,
                bids=(self._level("50000"),),
                asks=(self._level("50100"),),
                snapshot_time=datetime(2026, 8, 24, 12, 0),  # naive
            )

    def test_empty_book_raises(self) -> None:
        """A book with both sides empty is not a valid snapshot."""
        with pytest.raises(ValueError, match="at least one bid or ask"):
            Orderbook(pair=_BTC_USDT, bids=(), asks=(), snapshot_time=_NOW)

    def test_bids_not_descending_raises(self) -> None:
        """Bids in ascending order break the domain invariant."""
        with pytest.raises(ValueError, match="bids must be strictly descending"):
            Orderbook(
                pair=_BTC_USDT,
                bids=(self._level("50000"), self._level("50050")),
                asks=(self._level("50100"),),
                snapshot_time=_NOW,
            )

    def test_asks_not_ascending_raises(self) -> None:
        """Asks in descending order break the domain invariant."""
        with pytest.raises(ValueError, match="asks must be strictly ascending"):
            Orderbook(
                pair=_BTC_USDT,
                bids=(self._level("50000"),),
                asks=(self._level("50200"), self._level("50100")),
                snapshot_time=_NOW,
            )

    def test_crossed_book_raises(self) -> None:
        """best_bid >= best_ask is a crossed book — rejected."""
        with pytest.raises(ValueError, match="crossed"):
            Orderbook(
                pair=_BTC_USDT,
                bids=(self._level("50100"),),
                asks=(self._level("50050"),),
                snapshot_time=_NOW,
            )

    def test_valid_book_constructs(self) -> None:
        """A well-formed book constructs and preserves ordering verbatim."""
        book = Orderbook(
            pair=_BTC_USDT,
            bids=(self._level("50050"), self._level("50000")),
            asks=(self._level("50100"), self._level("50150")),
            snapshot_time=_NOW,
        )
        assert book.bids[0].price == Decimal("50050")
        assert book.asks[0].price == Decimal("50100")
        assert book.snapshot_time == _NOW


def test_orderbook_refuses_a_negative_last_update_id() -> None:
    """A venue sequence number below zero cannot come from a real book (issue #866)."""
    with pytest.raises(ValueError, match="last_update_id must be >= 0"):
        Orderbook(
            pair=_BTC_USDT,
            bids=(OrderbookLevel(price=Decimal(100), quantity=Decimal(1)),),
            asks=(),
            snapshot_time=_NOW,
            last_update_id=-1,
        )
