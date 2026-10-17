"""Tests for the readings of the risk inputs shadow records but never acts on (#1030).

Invariants protected here:

- RO-02 would fire exactly when the spread is at least ten times its 1-hour
  median, and a zero median has no multiple and never fires;
- the equity high is the last mark whose equity reached its peak, the days
  are counted from it, and the drawdown is the deepest since it;
- the recovery kill would fire only after more than 90 days without a new
  high that include a drawdown of at least 8%;
- no mark gives no reading.
"""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from hypothesis import given
from hypothesis import strategies as st

from hermes.application.risk_inputs.readings import recovery_reading, spread_reading
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.market_data.spread_sample import SpreadSample
from hermes.domain.portfolio.mark import PortfolioMark
from tests.support.fill_kernel import BTC_USDT, seed

_T0 = datetime(2026, 6, 1, tzinfo=UTC)


def _sample(spread: str, median: str) -> SpreadSample:
    return SpreadSample(
        pair=BTC_USDT,
        sampled_at=_T0,
        book_time=_T0,
        spread=Decimal(spread),
        median_spread_1h=Decimal(median),
        samples_1h=12,
    )


def _mark(day: int, equity: str, peak: str) -> PortfolioMark:
    """Return a flat mark ``day`` days after ``_T0`` with ``equity`` in cash under ``peak``."""
    return PortfolioMark(
        snapshot=replace(seed(), cash=Decimal(equity), peak_equity=Decimal(peak)),
        pair=BTC_USDT,
        interval=KlineInterval.H4,
        bar_close_time=_T0 + timedelta(days=day),
    )


def test_a_spread_ten_times_its_median_would_fire() -> None:
    """At exactly 10x the median RO-02 fires (§5.5: at least ten times)."""
    reading = spread_reading(_sample("0.0010", "0.0001"))

    assert (reading.multiple, reading.would_fire) == (Decimal(10), True)


def test_a_spread_just_under_ten_times_its_median_would_not_fire() -> None:
    """9.9x the median stays under the RO-02 threshold."""
    reading = spread_reading(_sample("0.00099", "0.0001"))

    assert (reading.multiple, reading.would_fire) == (Decimal("9.9"), False)


def test_a_zero_median_has_no_multiple_and_never_fires() -> None:
    """A flat book's zero median defines no multiple, so RO-02 reads nothing to fire on."""
    reading = spread_reading(_sample("0.0005", "0"))

    assert (reading.multiple, reading.would_fire) == (None, False)


@given(
    spread=st.decimals(min_value="0", max_value="0.05", places=6),
    median=st.decimals(min_value="0.000001", max_value="0.05", places=6),
)
def test_ro02_fires_exactly_at_ten_times_the_median(spread: Decimal, median: Decimal) -> None:
    """For any positive median, the verdict is ``spread >= 10 x median``."""
    reading = spread_reading(_sample(str(spread), str(median)))

    assert reading.would_fire is (spread >= 10 * median)


def test_the_days_count_from_the_last_mark_at_its_peak() -> None:
    """A new high on day 3 resets the count; the drawdown is the deepest after it."""
    marks = [
        _mark(0, "5000", "5000"),
        _mark(1, "4500", "5000"),
        _mark(3, "5200", "5200"),
        _mark(4, "4940", "5200"),
        _mark(5, "5100", "5200"),
    ]

    reading = recovery_reading(marks, now=_T0 + timedelta(days=10, hours=6))

    assert reading is not None
    assert (reading.high_at, reading.days_since_high, reading.deepest_drawdown) == (
        _T0 + timedelta(days=3),
        7,
        Decimal("0.05"),
    )


def test_ninety_one_days_from_an_8_percent_drawdown_would_fire() -> None:
    """More than 90 days without a high, after losing 8% of the peak, fires the kill."""
    marks = [_mark(0, "5000", "5000"), _mark(10, "4600", "5000")]

    reading = recovery_reading(marks, now=_T0 + timedelta(days=91))

    assert reading is not None
    assert (reading.days_since_high, reading.deepest_drawdown, reading.would_fire) == (
        91,
        Decimal("0.08"),
        True,
    )


def test_exactly_ninety_days_would_not_fire() -> None:
    """§10 says more than 90 days: day 90 itself does not fire."""
    marks = [_mark(0, "5000", "5000"), _mark(10, "4000", "5000")]

    reading = recovery_reading(marks, now=_T0 + timedelta(days=90))

    assert reading is not None
    assert (reading.days_since_high, reading.would_fire) == (90, False)


def test_a_long_shallow_drawdown_would_not_fire() -> None:
    """120 days under the high never deeper than 7.9% stays under the §10 trigger."""
    marks = [_mark(0, "5000", "5000"), _mark(10, "4605", "5000")]

    reading = recovery_reading(marks, now=_T0 + timedelta(days=120))

    assert reading is not None
    assert (reading.deepest_drawdown, reading.would_fire) == (Decimal("0.079"), False)


def test_no_mark_gives_no_reading() -> None:
    """Before the first bar there is nothing to read; one mark already gives a reading."""
    readings = [recovery_reading(marks, now=_T0) for marks in ([], [_mark(0, "5000", "5000")])]

    assert [reading is None for reading in readings] == [True, False]
