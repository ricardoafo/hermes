"""Tests for ``within_catchup`` — which signals are still worth notifying.

Invariants protected here: a signal is recent exactly when it is between 0 and
``bars`` candles old; a future signal is never recent; naive times are refused.

Reference: 309-serve-bar-loop change.md D-6.
"""

from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hermes.domain.market_data.bar_schedule import interval_duration, within_catchup
from hermes.domain.market_data.kline import KlineInterval

_T0 = datetime(2026, 10, 2, 16, tzinfo=UTC)


@pytest.mark.property
@given(
    interval=st.sampled_from([KlineInterval.H4, KlineInterval.D1]),
    offset_seconds=st.integers(min_value=-3 * 86_400, max_value=6 * 86_400),
)
def test_within_catchup_is_true_exactly_between_zero_and_two_candles(
    interval: KlineInterval, offset_seconds: int
) -> None:
    """within_catchup(now, t, i) iff 0 <= now - t <= 2 * duration(i)."""
    now = _T0 + timedelta(seconds=offset_seconds)

    expected = timedelta(0) <= now - _T0 <= 2 * interval_duration(interval)

    assert within_catchup(now, _T0, interval) is expected


def test_signal_exactly_two_bars_old_is_recent() -> None:
    """The boundary is inclusive: 8 h old on a 4h stream is still notified."""
    assert within_catchup(_T0 + timedelta(hours=8), _T0, KlineInterval.H4) is True


def test_signal_one_second_past_two_bars_is_stale() -> None:
    """One second beyond the window is stale."""
    assert within_catchup(_T0 + timedelta(hours=8, seconds=1), _T0, KlineInterval.H4) is False


def test_future_signal_is_not_recent() -> None:
    """A signal timed after now (clock skew) is not recent."""
    assert within_catchup(_T0 - timedelta(seconds=1), _T0, KlineInterval.H4) is False


def test_naive_time_is_rejected() -> None:
    """A naive now is refused."""
    with pytest.raises(ValueError, match="now"):
        within_catchup(datetime(2026, 10, 2, 16), _T0, KlineInterval.H4)
