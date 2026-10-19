"""Tests for the execution-quality measures (drift and slippage).

Invariants protected here: drift and slippage are signed in the cost
direction (positive means the fill cost the strategy more), a fill exactly at
the reference measures zero, and the two sides mirror each other. A sign error
would let an adverse shadow fill look favourable to the SHADOW_CERTIFIED gate.

Reference: openspec/archive/353-fill-kernel-in-serve/change.md D-4.
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hermes.domain.market_data.orderbook import Orderbook, OrderbookLevel
from hermes.domain.trading.execution_quality import (
    book_mid,
    book_spread,
    drift_bps,
    slippage_vs_mid,
)
from hermes.domain.trading.order import OrderSide
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair

_BTC = Pair(base=Currency.BTC, quote=Currency.USDT)
_AT = datetime(2026, 10, 4, tzinfo=UTC)
_prices = st.decimals(min_value=Decimal("0.01"), max_value=Decimal("1000000"), places=2)


def _book(bid: str, ask: str) -> Orderbook:
    """Return a one-level book."""
    return Orderbook(
        pair=_BTC,
        bids=(OrderbookLevel(price=Decimal(bid), quantity=Decimal("1")),),
        asks=(OrderbookLevel(price=Decimal(ask), quantity=Decimal("1")),),
        snapshot_time=_AT,
    )


def test_buy_above_the_model_price_drifts_positive() -> None:
    """A BUY filled 10 above a 10000 model price costs 10 bps."""
    assert drift_bps(OrderSide.BUY, Decimal("10010"), Decimal("10000")) == Decimal("10")


def test_sell_below_the_model_price_drifts_positive() -> None:
    """A SELL filled 10 below a 10000 model price also costs 10 bps."""
    assert drift_bps(OrderSide.SELL, Decimal("9990"), Decimal("10000")) == Decimal("10")


@given(fill=_prices, model=_prices)
def test_drift_of_a_sell_is_the_negated_drift_of_a_buy(fill: Decimal, model: Decimal) -> None:
    """The same two prices measure the same distance with opposite cost signs."""
    assert drift_bps(OrderSide.SELL, fill, model) == -drift_bps(OrderSide.BUY, fill, model)


@given(price=_prices, side=st.sampled_from(OrderSide))
def test_a_fill_at_the_model_price_has_zero_drift(price: Decimal, side: OrderSide) -> None:
    """No distance, no drift, on either side."""
    assert drift_bps(side, price, price) == 0


@given(model=_prices, extra=_prices)
def test_a_buy_above_the_model_costs_the_excess_in_bps(model: Decimal, extra: Decimal) -> None:
    """An adverse BUY reads exactly ``excess / model`` in bps, so always positive."""
    assert drift_bps(OrderSide.BUY, model + extra, model) == extra / model * Decimal("10000")


def test_drift_refuses_a_non_positive_price() -> None:
    """A zero model price would divide by zero; it is refused instead."""
    with pytest.raises(ValueError, match="prices must be > 0"):
        drift_bps(OrderSide.BUY, Decimal("1"), Decimal("0"))


def test_slippage_of_a_buy_at_the_ask_is_half_the_spread() -> None:
    """A BUY at the ask of a 99/101 book pays 1 over a mid of 100: 1%."""
    assert slippage_vs_mid(OrderSide.BUY, Decimal("101"), Decimal("100")) == Decimal("0.01")


def test_slippage_of_a_sell_at_the_bid_is_half_the_spread() -> None:
    """A SELL at the bid of a 99/101 book also pays 1%."""
    assert slippage_vs_mid(OrderSide.SELL, Decimal("99"), Decimal("100")) == Decimal("0.01")


def test_slippage_refuses_a_non_positive_mid() -> None:
    """A zero mid is refused, not divided by."""
    with pytest.raises(ValueError, match="prices must be > 0"):
        slippage_vs_mid(OrderSide.SELL, Decimal("1"), Decimal("0"))


def test_mid_is_the_average_of_the_touch() -> None:
    """The mid of a 99/101 book is 100."""
    assert book_mid(_book("99", "101")) == Decimal("100")


def test_a_one_sided_book_has_no_mid_and_zero_spread() -> None:
    """Without both sides there is no mid; the spread reads zero."""
    book = Orderbook(
        pair=_BTC,
        bids=(OrderbookLevel(price=Decimal("99"), quantity=Decimal("1")),),
        asks=(),
        snapshot_time=_AT,
    )
    assert (book_mid(book), book_spread(book)) == (None, Decimal("0"))


def test_spread_is_the_touch_distance_over_the_mid() -> None:
    """A 99/101 book has a 2% spread."""
    assert book_spread(_book("99", "101")) == Decimal("0.02")
