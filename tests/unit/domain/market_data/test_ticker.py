"""Tests for :class:`Ticker`.

Every test protects one invariant of the domain ticker contract:

- Positive best_bid / best_ask / last_price required.
- UTC-aware snapshot_time required.
- Crossed book (best_bid >= best_ask) rejected.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from hermes.domain.market_data.ticker import Ticker
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair

_BTC_USDT = Pair(base=Currency.BTC, quote=Currency.USDT)
_NOW = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)


def test_naive_snapshot_time_raises() -> None:
    """A naive datetime is rejected — hermes stores UTC-aware timestamps only."""
    with pytest.raises(ValueError, match="snapshot_time must be UTC-aware"):
        Ticker(
            pair=_BTC_USDT,
            best_bid=Decimal("50000"),
            best_ask=Decimal("50100"),
            last_price=Decimal("50050"),
            snapshot_time=datetime(2026, 8, 24, 12, 0),  # naive
        )


def test_zero_best_bid_raises() -> None:
    """Zero best_bid is impossible in a live market — rejected."""
    with pytest.raises(ValueError, match="best_bid must be > 0"):
        Ticker(
            pair=_BTC_USDT,
            best_bid=Decimal("0"),
            best_ask=Decimal("50100"),
            last_price=Decimal("50050"),
            snapshot_time=_NOW,
        )


def test_zero_best_ask_raises() -> None:
    """Zero best_ask is impossible in a live market — rejected."""
    with pytest.raises(ValueError, match="best_ask must be > 0"):
        Ticker(
            pair=_BTC_USDT,
            best_bid=Decimal("50000"),
            best_ask=Decimal("0"),
            last_price=Decimal("50050"),
            snapshot_time=_NOW,
        )


def test_zero_last_price_raises() -> None:
    """Zero last_price is impossible — every recorded trade has a positive price."""
    with pytest.raises(ValueError, match="last_price must be > 0"):
        Ticker(
            pair=_BTC_USDT,
            best_bid=Decimal("50000"),
            best_ask=Decimal("50100"),
            last_price=Decimal("0"),
            snapshot_time=_NOW,
        )


def test_crossed_book_raises() -> None:
    """best_bid >= best_ask (crossed book) is rejected at construction."""
    with pytest.raises(ValueError, match="crossed"):
        Ticker(
            pair=_BTC_USDT,
            best_bid=Decimal("50100"),
            best_ask=Decimal("50050"),
            last_price=Decimal("50075"),
            snapshot_time=_NOW,
        )


def test_valid_ticker_constructs() -> None:
    """A well-formed ticker constructs and preserves fields verbatim."""
    ticker = Ticker(
        pair=_BTC_USDT,
        best_bid=Decimal("50000"),
        best_ask=Decimal("50100"),
        last_price=Decimal("50050"),
        snapshot_time=_NOW,
    )
    assert ticker.best_bid == Decimal("50000")
    assert ticker.best_ask == Decimal("50100")
    assert ticker.last_price == Decimal("50050")
    assert ticker.snapshot_time == _NOW
