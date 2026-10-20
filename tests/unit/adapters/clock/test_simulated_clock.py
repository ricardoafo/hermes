"""Unit and property tests for :class:`hermes.adapters.clock.SimulatedClock`.

Tests:
- Constructor accepts tz-aware datetime; rejects naive.
- ``now()`` is non-destructive (returns same value without advancing).
- ``advance(delta)`` moves time forward by exactly ``delta``.
- ``advance(negative)`` raises ``ValueError``.
- ``set(t)`` where ``t >= current`` jumps the clock to ``t``.
- ``set(t)`` where ``t < current`` raises ``ValueError`` (monotonicity policy D-4).
- ``set(naive)`` raises ``ValueError``.
- Thread safety: concurrent ``advance`` calls do not corrupt state.

Property tests (Hypothesis):
- ``advance(d).now() == prior + d`` for random non-negative deltas.
- ``set(t)`` raises iff ``t < current``.

Reference: openspec/archive/spec-4a-clock-port/change.md D-4, D-5.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta, timezone

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from hermes.adapters.clock import SimulatedClock

# ──────────────────────────────────────────────────────────────────────────────
# Shared fixtures
# ──────────────────────────────────────────────────────────────────────────────

_EPOCH = datetime(2025, 1, 1, tzinfo=UTC)
_ONE_HOUR = timedelta(hours=1)
_ONE_DAY = timedelta(days=1)


class TestSimulatedClockConstructor:
    """Constructor validation."""

    def test_accepts_tz_aware_start(self) -> None:
        """Constructor accepts timezone-aware datetime without raising."""
        clock = SimulatedClock(start=_EPOCH)
        assert clock.now() == _EPOCH

    def test_rejects_naive_start(self) -> None:
        """Constructor rejects a naive datetime (no tzinfo)."""
        naive = datetime(2025, 1, 1)  # no tzinfo
        with pytest.raises(ValueError, match="timezone-aware"):
            SimulatedClock(start=naive)

    def test_now_equals_start_immediately(self) -> None:
        """now() immediately after construction equals start."""
        start = datetime(2026, 6, 15, 12, 0, 0, tzinfo=UTC)
        clock = SimulatedClock(start=start)
        assert clock.now() == start


class TestSimulatedClockNow:
    """now() is non-destructive."""

    def test_now_is_idempotent(self) -> None:
        """Multiple now() calls without advance return the same value (A-4.4)."""
        clock = SimulatedClock(start=_EPOCH)
        results = [clock.now() for _ in range(5)]
        assert all(r == _EPOCH for r in results), f"now() is not idempotent — got {results!r}"

    def test_now_is_tz_aware(self) -> None:
        """now() always returns a tz-aware datetime with zero UTC offset."""
        clock = SimulatedClock(start=_EPOCH)
        result = clock.now()
        assert result.tzinfo is not None
        assert result.utcoffset() == timedelta(0)

    def test_now_is_utc(self) -> None:
        """now() returns a datetime in UTC (zero utcoffset)."""
        clock = SimulatedClock(start=_EPOCH)
        result = clock.now()
        assert result.utcoffset() == timedelta(0)


class TestSimulatedClockAdvance:
    """advance(delta) moves the clock forward."""

    def test_advance_by_one_hour(self) -> None:
        """advance(1h) moves now() forward by exactly one hour (A-4.1)."""
        clock = SimulatedClock(start=_EPOCH)
        clock.advance(_ONE_HOUR)
        assert clock.now() == _EPOCH + _ONE_HOUR

    def test_advance_by_one_day(self) -> None:
        """advance(1d) moves now() forward by exactly one day."""
        clock = SimulatedClock(start=_EPOCH)
        clock.advance(_ONE_DAY)
        assert clock.now() == _EPOCH + _ONE_DAY

    def test_advance_zero_is_noop(self) -> None:
        """advance(timedelta(0)) is permitted and does not change the time."""
        clock = SimulatedClock(start=_EPOCH)
        clock.advance(timedelta(0))
        assert clock.now() == _EPOCH

    def test_advance_accumulates(self) -> None:
        """Multiple advances accumulate correctly."""
        clock = SimulatedClock(start=_EPOCH)
        clock.advance(_ONE_HOUR)
        clock.advance(_ONE_HOUR)
        assert clock.now() == _EPOCH + timedelta(hours=2)

    def test_advance_negative_raises(self) -> None:
        """advance(negative) raises ValueError."""
        clock = SimulatedClock(start=_EPOCH)
        with pytest.raises(ValueError, match="non-negative"):
            clock.advance(timedelta(seconds=-1))

    def test_advance_negative_does_not_mutate(self) -> None:
        """advance(negative) raises before mutating internal state."""
        clock = SimulatedClock(start=_EPOCH)
        with pytest.raises(ValueError):
            clock.advance(timedelta(hours=-5))
        # State must be unchanged
        assert clock.now() == _EPOCH


class TestSimulatedClockSet:
    """set(t) jumps the clock to t, enforcing monotonicity."""

    def test_set_forward(self) -> None:
        """set(t) where t > current moves clock to t (A-4.2)."""
        clock = SimulatedClock(start=_EPOCH)
        future = _EPOCH + _ONE_DAY
        clock.set(future)
        assert clock.now() == future

    def test_set_to_equal_current(self) -> None:
        """set(current) is a no-op — equal is permitted."""
        clock = SimulatedClock(start=_EPOCH)
        clock.set(_EPOCH)
        assert clock.now() == _EPOCH

    def test_set_backward_raises(self) -> None:
        """set(t < current) raises ValueError (A-4.3, monotonicity policy D-4)."""
        clock = SimulatedClock(start=_EPOCH)
        clock.advance(_ONE_DAY)  # now = EPOCH + 1d
        past = _EPOCH  # 1 day behind current
        with pytest.raises(ValueError, match="backwards"):
            clock.set(past)

    def test_set_backward_does_not_mutate(self) -> None:
        """set(past) raises before mutating state."""
        clock = SimulatedClock(start=_EPOCH)
        target = _EPOCH + _ONE_DAY
        clock.set(target)  # move forward
        with pytest.raises(ValueError):
            clock.set(_EPOCH)
        # State must remain at target
        assert clock.now() == target

    def test_set_naive_raises(self) -> None:
        """set(naive datetime) raises ValueError."""
        clock = SimulatedClock(start=_EPOCH)
        naive_future = datetime(2030, 1, 1)  # no tzinfo
        with pytest.raises(ValueError, match="timezone-aware"):
            clock.set(naive_future)

    def test_set_non_utc_aware_is_normalised(self) -> None:
        """set(tz-aware non-UTC) is accepted and normalised to UTC."""
        one_hour_east = timezone(timedelta(hours=1))
        # 2025-01-02 01:00+01:00 == 2025-01-02 00:00 UTC > _EPOCH
        future_local = datetime(2025, 1, 2, 1, 0, tzinfo=one_hour_east)
        clock = SimulatedClock(start=_EPOCH)
        clock.set(future_local)
        # After normalisation, should equal 2025-01-02 00:00 UTC
        expected = datetime(2025, 1, 2, 0, 0, tzinfo=UTC)
        assert clock.now() == expected


class TestSimulatedClockThreadSafety:
    """SimulatedClock is safe for concurrent advance() calls."""

    def test_concurrent_advance_accumulates_correctly(self) -> None:
        """N threads each advancing by 1s should sum to N seconds total."""
        n_threads = 20
        delta = timedelta(seconds=1)
        clock = SimulatedClock(start=_EPOCH)
        threads = [threading.Thread(target=clock.advance, args=(delta,)) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        expected = _EPOCH + timedelta(seconds=n_threads)
        assert clock.now() == expected


# ──────────────────────────────────────────────────────────────────────────────
# Property-based tests
# ──────────────────────────────────────────────────────────────────────────────

# Strategy: non-negative timedelta up to 365 days
_non_negative_delta = st.timedeltas(min_value=timedelta(0), max_value=timedelta(days=365))

# Strategy: random tz-aware start datetime in a safe range
_utc_datetime = st.datetimes(
    min_value=datetime(2000, 1, 1),
    max_value=datetime(2090, 1, 1),
    timezones=st.just(UTC),
)


class TestSimulatedClockProperties:
    """Hypothesis property tests for SimulatedClock invariants."""

    @given(start=_utc_datetime, delta=_non_negative_delta)
    @settings(max_examples=200)
    def test_advance_then_now_equals_start_plus_delta(
        self, start: datetime, delta: timedelta
    ) -> None:
        """Property: advance(d).now() == prior_now + d (A-4.1)."""
        clock = SimulatedClock(start=start)
        prior = clock.now()
        clock.advance(delta)
        assert clock.now() == prior + delta

    @given(start=_utc_datetime, delta=_non_negative_delta)
    @settings(max_examples=200)
    def test_now_idempotent_after_advance(self, start: datetime, delta: timedelta) -> None:
        """Property: now() is stable — reads after advance always equal start + delta."""
        clock = SimulatedClock(start=start)
        clock.advance(delta)
        expected = start + delta
        assert clock.now() == expected
        assert clock.now() == expected

    @given(start=_utc_datetime, delta=_non_negative_delta)
    @settings(max_examples=200)
    def test_set_forward_sets_exactly(self, start: datetime, delta: timedelta) -> None:
        """Property: set(t) where t = start + d sets clock to exactly t."""
        clock = SimulatedClock(start=start)
        target = start + delta
        clock.set(target)
        assert clock.now() == target

    @given(
        start=_utc_datetime,
        backward_delta=st.timedeltas(min_value=timedelta(seconds=1), max_value=timedelta(days=365)),
    )
    @settings(max_examples=100)
    def test_set_backward_always_raises(self, start: datetime, backward_delta: timedelta) -> None:
        """Property: set(t < current) always raises ValueError (A-4.3)."""
        clock = SimulatedClock(start=start)
        past = start - backward_delta
        with pytest.raises(ValueError):
            clock.set(past)

    @given(
        start=_utc_datetime,
        deltas=st.lists(_non_negative_delta, min_size=1, max_size=5),
    )
    @settings(max_examples=100)
    def test_multiple_advances_accumulate(self, start: datetime, deltas: list[timedelta]) -> None:
        """Property: sum of N advances equals total offset from start."""
        clock = SimulatedClock(start=start)
        for d in deltas:
            clock.advance(d)
        expected = start + sum(deltas, timedelta(0))
        assert clock.now() == expected

    @given(start=_utc_datetime)
    @settings(max_examples=50)
    def test_now_always_tz_aware(self, start: datetime) -> None:
        """Property: now() is always UTC-aware — utcoffset is exactly zero."""
        clock = SimulatedClock(start=start)
        result = clock.now()
        assert result.tzinfo is not None
        assert result.utcoffset() == timedelta(0)
