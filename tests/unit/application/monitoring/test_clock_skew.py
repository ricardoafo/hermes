"""Tests for the clock-skew watch (issue #859 item 2, option A).

Invariants protected here:

- crossing the limit records one ``CRITICAL`` anomaly, coming back one ``INFO``;
- nothing is recorded while the skew stays on the same side of the limit;
- the limit is inclusive: a skew exactly at it is still on time;
- the latest anomaly decides whether the clock is off.
"""

from datetime import UTC, datetime, timedelta

from hypothesis import given
from hypothesis import strategies as st

from hermes.application.monitoring.clock_skew import clock_is_off, clock_skew_change
from hermes.domain.events.anomaly_detected import (
    CLOCK_SKEW_SOURCE,
    AnomalyDetected,
    AnomalySeverity,
)

_NOW = datetime(2026, 10, 17, 12, 0, 5, tzinfo=UTC)
_LIMIT = timedelta(seconds=5)


def _change(skew_ms: int | None, *, was_off: bool) -> AnomalyDetected | None:
    return clock_skew_change(skew_ms, limit=_LIMIT, was_off=was_off, now=_NOW, correlation_id="c-1")


def test_a_clock_five_minutes_ahead_raises_one_critical_anomaly() -> None:
    """The defect's case: the clock 5 min ahead becomes a loud alert naming the skew."""
    alert = _change(300_000, was_off=False)

    assert alert is not None
    assert (alert.source, alert.severity, alert.occurred_at, alert.reason) == (
        CLOCK_SKEW_SOURCE,
        AnomalySeverity.CRITICAL,
        _NOW,
        "the host clock is 300000 ms ahead of Binance (limit 5000 ms); while it is more "
        "than 5 minutes ahead, RO-04 halts every entry on a stale book",
    )


def test_a_clock_behind_names_the_direction() -> None:
    """A clock behind exchange time is off too, and the alert says which way."""
    alert = _change(-7_000, was_off=False)

    assert alert is not None
    assert alert.reason.startswith("the host clock is 7000 ms behind Binance")


def test_a_clock_back_within_the_limit_records_one_info_anomaly() -> None:
    """Coming back clears the red status with a quiet record of the fix."""
    alert = _change(12, was_off=True)

    assert alert is not None
    assert (alert.severity, alert.reason) == (
        AnomalySeverity.INFO,
        "the host clock is back within 5000 ms of Binance (12 ms)",
    )


def test_a_skew_exactly_at_the_limit_is_on_time() -> None:
    """The limit is the largest skew still on time, as in ``hermes health``."""
    assert (_change(5_000, was_off=False), _change(-5_000, was_off=False)) == (None, None)


def test_no_measurement_changes_nothing() -> None:
    """Before the feed compares clocks there is nothing to judge, either way."""
    assert (_change(None, was_off=False), _change(None, was_off=True)) == (None, None)


@given(skew_ms=st.integers(min_value=-(10**9), max_value=10**9), was_off=st.booleans())
def test_an_anomaly_is_recorded_exactly_when_the_state_flips(skew_ms: int, was_off: bool) -> None:
    """For any skew: an anomaly iff off-ness changes, CRITICAL iff it is now off."""
    off = abs(skew_ms) > 5_000
    alert = _change(skew_ms, was_off=was_off)

    expected = (
        None if off == was_off else (AnomalySeverity.CRITICAL if off else AnomalySeverity.INFO)
    )
    assert (alert.severity if alert is not None else None) == expected


def test_the_clock_is_off_only_after_a_critical_skew_anomaly() -> None:
    """No record, or a return to time as the latest record, means the clock is on time."""
    critical = _change(300_000, was_off=False)
    info = _change(0, was_off=True)

    assert (clock_is_off(None), clock_is_off(critical), clock_is_off(info)) == (
        False,
        True,
        False,
    )
