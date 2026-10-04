"""Tests for :func:`synthesize_orderbook`, the backtest's stand-in for L2 depth.

Invariants (backtest-driver-mvp1-phase2 change.md R-3):

- The touch sits exactly ``half_spread_bps`` from the reference, bid below and
  ask above it.
- Each side's quantities sum exactly to ``depth_per_side``.
- Level quantities never shrink away from the touch; prices strictly move away.
- The book carries the given snapshot time.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hermes.domain.market_data.synthetic_orderbook import LEVEL_QUANTUM, synthesize_orderbook
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair

_PAIR = Pair(base=Currency.BTC, quote=Currency.USDT)
_AT = datetime(2024, 1, 2, 4, 0, tzinfo=UTC)

_prices = st.decimals(min_value=Decimal("0.01"), max_value=Decimal("200000"), places=2)
_half_spreads = st.sampled_from([Decimal("5"), Decimal("15"), Decimal("0.5"), Decimal("100")])
_depths = st.decimals(min_value=Decimal("0.001"), max_value=Decimal("100000"), places=8)
_levels = st.integers(min_value=1, max_value=20)
_growths = st.sampled_from([Decimal("1"), Decimal("1.5"), Decimal("2"), Decimal("1.1")])


@given(price=_prices, half=_half_spreads, depth=_depths, levels=_levels, growth=_growths)
def test_touch_sits_exactly_half_spread_from_the_reference(
    price: Decimal, half: Decimal, depth: Decimal, levels: int, growth: Decimal
) -> None:
    """Best ask = ref * (1 + h/1e4) and best bid = ref * (1 - h/1e4), so bid < ref < ask."""
    book = synthesize_orderbook(_PAIR, price, half, depth, _AT, levels=levels, level_growth=growth)
    assert book.asks[0].price == price * (1 + half / Decimal("10000"))
    assert book.bids[0].price == price * (1 - half / Decimal("10000"))
    assert book.bids[0].price < price < book.asks[0].price


@given(price=_prices, half=_half_spreads, depth=_depths, levels=_levels, growth=_growths)
def test_each_side_sums_exactly_to_the_requested_depth(
    price: Decimal, half: Decimal, depth: Decimal, levels: int, growth: Decimal
) -> None:
    """Sum of bid quantities == sum of ask quantities == depth_per_side, with no rounding loss."""
    book = synthesize_orderbook(_PAIR, price, half, depth, _AT, levels=levels, level_growth=growth)
    assert sum((lvl.quantity for lvl in book.bids), Decimal("0")) == depth
    assert sum((lvl.quantity for lvl in book.asks), Decimal("0")) == depth
    assert len(book.bids) == levels
    assert len(book.asks) == levels


@given(price=_prices, half=_half_spreads, depth=_depths, levels=_levels, growth=_growths)
def test_levels_get_no_thinner_and_prices_move_away_from_the_touch(
    price: Decimal, half: Decimal, depth: Decimal, levels: int, growth: Decimal
) -> None:
    """Quantities are non-decreasing outwards; bids strictly descend and asks strictly ascend."""
    book = synthesize_orderbook(_PAIR, price, half, depth, _AT, levels=levels, level_growth=growth)
    for side in (book.bids, book.asks):
        quantities = [lvl.quantity for lvl in side]
        assert quantities == sorted(quantities)
    bid_prices = [lvl.price for lvl in book.bids]
    ask_prices = [lvl.price for lvl in book.asks]
    assert bid_prices == sorted(bid_prices, reverse=True)
    assert ask_prices == sorted(ask_prices)
    assert len(set(bid_prices)) == levels
    assert len(set(ask_prices)) == levels


def test_book_carries_the_given_snapshot_time_and_pair() -> None:
    """The book is stamped with the caller's time and pair, not a clock read."""
    book = synthesize_orderbook(_PAIR, Decimal("100"), Decimal("5"), Decimal("10"), _AT)
    assert book.snapshot_time == _AT
    assert book.pair == _PAIR


def test_default_book_has_ten_levels_growing_by_one_and_a_half() -> None:
    """Defaults match D-1: 10 levels, each 1.5x the previous, 1 bp apart."""
    book = synthesize_orderbook(_PAIR, Decimal("100"), Decimal("5"), Decimal("1000"), _AT)
    assert len(book.asks) == 10
    ratio = book.asks[1].quantity / book.asks[0].quantity
    assert abs(ratio - Decimal("1.5")) < Decimal("1e-9")
    assert book.asks[1].price == Decimal("100") * (1 + Decimal("6") / Decimal("10000"))


@pytest.mark.parametrize(
    ("price", "half", "depth"),
    [
        (Decimal("0"), Decimal("5"), Decimal("1")),
        (Decimal("100"), Decimal("0"), Decimal("1")),
        (Decimal("100"), Decimal("5"), Decimal("0")),
    ],
)
def test_non_positive_inputs_are_refused(price: Decimal, half: Decimal, depth: Decimal) -> None:
    """A zero price, half-spread or depth cannot describe a book, so it raises."""
    with pytest.raises(ValueError, match="must be > 0"):
        synthesize_orderbook(_PAIR, price, half, depth, _AT)


def test_depth_below_the_level_grid_is_refused() -> None:
    """A depth whose touch share rounds to zero would build a zero-size level, so it raises."""
    with pytest.raises(ValueError, match="too small"):
        synthesize_orderbook(_PAIR, Decimal("100"), Decimal("5"), LEVEL_QUANTUM, _AT)


def test_levels_reaching_zero_price_are_refused() -> None:
    """An outermost level 10000 bps out would price a bid at zero, so it raises."""
    with pytest.raises(ValueError, match="outermost level"):
        synthesize_orderbook(_PAIR, Decimal("100"), Decimal("9999"), Decimal("10"), _AT, levels=2)


def test_non_utc_snapshot_time_is_refused() -> None:
    """A book stamped in another zone would break the UTC-only rule, so it raises."""
    cet = datetime(2024, 1, 2, 5, 0, tzinfo=timezone(timedelta(hours=1)))
    with pytest.raises(ValueError, match="UTC-aware"):
        synthesize_orderbook(_PAIR, Decimal("100"), Decimal("5"), Decimal("10"), cet)


@pytest.mark.parametrize(
    ("levels", "growth", "step"),
    [
        (0, Decimal("1.5"), Decimal("1")),
        (3, Decimal("0.9"), Decimal("1")),
        (3, Decimal("1.5"), Decimal("0")),
    ],
)
def test_degenerate_level_shapes_are_refused(levels: int, growth: Decimal, step: Decimal) -> None:
    """No levels, shrinking levels or zero step would break the book invariants, so they raise."""
    with pytest.raises(ValueError, match=r"levels|level_growth|level_step_bps"):
        synthesize_orderbook(
            _PAIR,
            Decimal("100"),
            Decimal("5"),
            Decimal("10"),
            _AT,
            levels=levels,
            level_growth=growth,
            level_step_bps=step,
        )
