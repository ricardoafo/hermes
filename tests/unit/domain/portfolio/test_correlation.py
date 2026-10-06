"""Unit tests for the 30-day pair correlation math (#835, pre-registration §5.5).

Invariants under test:

- Pearson's coefficient matches a hand-computed value, is ``None`` when it is
  undefined (fewer than two points, a flat series) and refuses unequal lengths.
- For any two series it lies in [-1, 1], is symmetric, and is 1.0 for a
  series with itself (property).
- Daily returns use only days both pairs have, exactly one day apart, and
  keep the newest ``window_days`` returns.
- A recorded correlation refuses an unsorted pair, an out-of-range value and
  a sample count outside [2, window].
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from hermes.domain.portfolio.correlation import (
    PairCorrelation,
    aligned_daily_returns,
    pearson,
)
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair

_BTC = Pair(base=Currency.BTC, quote=Currency.USDT)
_ETH = Pair(base=Currency.ETH, quote=Currency.USDT)
_DAY0 = datetime(2026, 9, 1, tzinfo=UTC)
_DAY = timedelta(days=1)
_NAIVE = datetime(2026, 9, 1)

_returns = st.floats(min_value=-0.5, max_value=0.5, allow_nan=False, allow_infinity=False)
_series = st.lists(_returns, min_size=2, max_size=40)


def _closes(*values: str, skip: frozenset[int] = frozenset()) -> dict[datetime, Decimal]:
    return {_DAY0 + _DAY * i: Decimal(v) for i, v in enumerate(values) if i not in skip}


def test_pearson_of_a_known_pair_of_series() -> None:
    """Series 1..5 against 2, 4, 5, 4, 5 gives r = sqrt(0.6) (hand-computed)."""
    value = pearson([1.0, 2.0, 3.0, 4.0, 5.0], [2.0, 4.0, 5.0, 4.0, 5.0])
    assert value == pytest.approx(math.sqrt(0.6), abs=1e-12)


def test_pearson_of_opposite_series_is_minus_one() -> None:
    """A series against its negation is perfectly anti-correlated."""
    assert pearson([0.01, -0.02, 0.03], [-0.01, 0.02, -0.03]) == pytest.approx(-1.0)


@pytest.mark.parametrize(
    ("xs", "ys"),
    [([], []), ([0.1], [0.2]), ([0.1, 0.1, 0.1], [0.1, 0.2, 0.3])],
)
def test_pearson_is_undefined_without_two_points_or_variance(
    xs: list[float], ys: list[float]
) -> None:
    """No value is invented when the coefficient does not exist."""
    assert [pearson(xs, ys)] == [None]


def test_pearson_refuses_series_of_different_length() -> None:
    """Misaligned series are a caller bug, not a correlation."""
    with pytest.raises(ValueError, match="length"):
        pearson([0.1, 0.2], [0.1, 0.2, 0.3])


@pytest.mark.property
@given(xs=_series, data=st.data())
def test_pearson_is_bounded_and_symmetric(xs: list[float], data: st.DataObject) -> None:
    """Any defined coefficient is in [-1, 1] and the same with the series swapped."""
    ys = data.draw(st.lists(_returns, min_size=len(xs), max_size=len(xs)))
    value = pearson(xs, ys)
    swapped = pearson(ys, xs)
    if value is None:
        assert swapped is None
        return
    assert swapped == pytest.approx(value, abs=1e-12)
    assert -1.0 <= value <= 1.0


@pytest.mark.property
@given(xs=_series)
def test_pearson_of_a_series_with_itself_is_one(xs: list[float]) -> None:
    """Identical series correlate at 1.0 whenever the series varies."""
    mean = math.fsum(xs) / len(xs)
    assume(math.fsum((x - mean) ** 2 for x in xs) > 1e-12)
    assert pearson(xs, xs) == pytest.approx(1.0, abs=1e-9)


def test_returns_are_simple_close_to_close() -> None:
    """Closes 100, 110, 99 give returns +10 % and -10 %."""
    a, b = aligned_daily_returns(_closes("100", "110", "99"), _closes("50", "50", "55"))
    assert a == pytest.approx((0.10, -0.10))
    assert b == pytest.approx((0.0, 0.10))


def test_returns_skip_a_day_either_pair_lacks() -> None:
    """A missing close in one pair drops both returns that would span it."""
    a, b = aligned_daily_returns(
        _closes("100", "110", "121", "133.1"),
        _closes("10", "11", "12.1", "13.31", skip=frozenset({1})),
    )
    assert a == pytest.approx((0.10,))
    assert b == pytest.approx((0.10,))


def test_returns_keep_the_newest_thirty_by_default() -> None:
    """Forty days of closes give the last 30 returns only (§5.5 "30-day")."""
    closes = {_DAY0 + _DAY * i: Decimal(100 + i) for i in range(40)}
    a, _ = aligned_daily_returns(closes, closes)
    assert a[0] == pytest.approx(110 / 109 - 1)
    assert a[-1] == pytest.approx(139 / 138 - 1)


def test_returns_refuse_a_non_positive_close() -> None:
    """A zero close would divide by zero; it is refused instead."""
    with pytest.raises(ValueError, match="positive"):
        aligned_daily_returns(_closes("100", "0"), _closes("1", "2"))


def _correlation(**overrides: object) -> PairCorrelation:
    fields: dict[str, object] = {
        "pair_a": _BTC,
        "pair_b": _ETH,
        "window_days": 30,
        "as_of": _DAY0,
        "correlation": 0.5,
        "sample_count": 30,
    }
    fields.update(overrides)
    return PairCorrelation(**fields)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "overrides",
    [
        {"pair_a": _ETH, "pair_b": _BTC},
        {"pair_b": _BTC},
        {"correlation": 1.0000001},
        {"correlation": -1.5},
        {"sample_count": 1},
        {"sample_count": 31},
        {"as_of": _NAIVE},
    ],
)
def test_recorded_correlation_refuses_an_invalid_value(overrides: dict[str, object]) -> None:
    """Only sorted distinct pairs, values in [-1, 1] and counts in [2, window] are stored."""
    with pytest.raises(ValueError, match=r"pairs|correlation|sample_count|as_of"):
        _correlation(**overrides)
