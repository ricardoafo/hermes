"""Unit tests for the 30-day pair correlation math (#835, pre-registration §5.5).

Invariants under test:

- Daily returns use only days both pairs have, exactly one day apart, and
  keep the newest ``window_days`` returns.
- A recorded correlation refuses an unsorted pair, an out-of-range value and
  a sample count outside [2, window].
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from hermes.domain.portfolio.correlation import PairCorrelation, aligned_daily_returns
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair

_BTC = Pair(base=Currency.BTC, quote=Currency.USDT)
_ETH = Pair(base=Currency.ETH, quote=Currency.USDT)
_DAY0 = datetime(2026, 9, 1, tzinfo=UTC)
_DAY = timedelta(days=1)
_NAIVE = datetime(2026, 9, 1)


def _closes(*values: str, skip: frozenset[int] = frozenset()) -> dict[datetime, Decimal]:
    return {_DAY0 + _DAY * i: Decimal(v) for i, v in enumerate(values) if i not in skip}


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
