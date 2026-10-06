"""Unit tests for the spread median and the spread sample (#835).

Invariants under test:

- The median of an odd count is the middle value; of an even count, the mean
  of the two middle values; of nothing, ``None``.
- The median never depends on the input order and lies between the smallest
  and the largest value (property).
- A sample refuses a naive timestamp, a negative spread and an empty window.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hermes.domain.market_data.spread_sample import (
    SpreadSample,
    median_spread,
)
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair

_BTC = Pair(base=Currency.BTC, quote=Currency.USDT)
_AT = datetime(2026, 10, 6, 12, tzinfo=UTC)
_NAIVE = datetime(2026, 10, 6, 12)

_spreads = st.lists(
    st.decimals(min_value=Decimal(0), max_value=Decimal("0.1"), places=8),
    min_size=1,
    max_size=40,
)


def test_median_of_an_odd_count_is_the_middle_value() -> None:
    """Three spreads give the second smallest, whatever the order."""
    assert median_spread([Decimal("0.3"), Decimal("0.1"), Decimal("0.2")]) == Decimal("0.2")


def test_median_of_an_even_count_is_the_mean_of_the_middle_two() -> None:
    """Four spreads give the exact mean of the two middle ones."""
    values = [Decimal("0.0004"), Decimal("0.0001"), Decimal("0.0003"), Decimal("0.0002")]
    assert median_spread(values) == Decimal("0.00025")


def test_median_of_nothing_is_none_not_zero() -> None:
    """An empty window has no median, rather than a zero spread."""
    assert [median_spread([]), median_spread([Decimal("0.5")])] == [None, Decimal("0.5")]


def test_one_outlier_does_not_move_the_median() -> None:
    """A single wide book among twelve leaves the median at the typical spread."""
    values = [Decimal("0.0001")] * 11 + [Decimal("0.05")]
    assert median_spread(values) == Decimal("0.0001")


@pytest.mark.property
@given(values=_spreads, data=st.data())
def test_median_is_order_independent_and_bounded(
    values: list[Decimal], data: st.DataObject
) -> None:
    """Shuffling never changes the median, which stays within [min, max]."""
    shuffled = data.draw(st.permutations(values))
    median = median_spread(values)
    assert median == median_spread(shuffled)
    assert median is not None
    assert min(values) <= median <= max(values)


def _sample(**overrides: object) -> SpreadSample:
    fields: dict[str, object] = {
        "pair": _BTC,
        "sampled_at": _AT,
        "book_time": _AT,
        "spread": Decimal("0.0001"),
        "median_spread_1h": Decimal("0.0001"),
        "samples_1h": 1,
    }
    fields.update(overrides)
    return SpreadSample(**fields)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "overrides",
    [
        {"sampled_at": _NAIVE},
        {"book_time": _NAIVE},
        {"spread": Decimal("-0.0001")},
        {"median_spread_1h": Decimal("-0.0001")},
        {"samples_1h": 0},
    ],
)
def test_sample_refuses_an_invalid_value(overrides: dict[str, object]) -> None:
    """Naive times, negative spreads and an empty window cannot be recorded."""
    with pytest.raises(ValueError, match=r"timezone|negative|samples_1h"):
        _sample(**overrides)
