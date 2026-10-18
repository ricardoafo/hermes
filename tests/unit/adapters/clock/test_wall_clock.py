"""Unit and property tests for :class:`hermes.adapters.clock.WallClock`.

Tests:
- ``now()`` always returns a tz-aware UTC ``datetime``.
- ``now()`` values are monotonically non-decreasing across successive calls.
- Structural conformance to ``ClockPort``.

Reference: openspec/archive/spec-4a-clock-port/change.md A-2.1, A-2.2.
"""

from __future__ import annotations

from datetime import timedelta

from hypothesis import given, settings
from hypothesis import strategies as st

from hermes.adapters.clock import WallClock
from hermes.ports.clock import ClockPort


class TestWallClockUnit:
    """Unit tests for WallClock."""

    def test_now_returns_tz_aware_datetime(self) -> None:
        """now() must return a tz-aware datetime with zero UTC offset (A-2.2)."""
        clock = WallClock()
        result = clock.now()
        assert result.tzinfo is not None, (
            "WallClock.now() returned a naive datetime — must be UTC-aware"
        )
        assert result.utcoffset() == timedelta(0), (
            f"WallClock.now() is not UTC — utcoffset={result.utcoffset()!r}"
        )

    def test_now_is_utc(self) -> None:
        """now() must be in UTC timezone (zero utcoffset)."""
        clock = WallClock()
        result = clock.now()
        utc_offset = result.utcoffset()
        assert utc_offset is not None
        assert utc_offset == timedelta(0), f"WallClock.now() is not UTC — utcoffset={utc_offset!r}"

    def test_now_monotonically_non_decreasing(self) -> None:
        """Two successive now() calls must be non-decreasing."""
        clock = WallClock()
        t1 = clock.now()
        t2 = clock.now()
        assert t2 >= t1, f"WallClock.now() is not monotonic: t1={t1!r}, t2={t2!r}"

    def test_now_three_calls_non_decreasing(self) -> None:
        """Three successive now() calls must be monotonically non-decreasing."""
        clock = WallClock()
        timestamps = [clock.now() for _ in range(3)]
        for i in range(len(timestamps) - 1):
            assert timestamps[i] <= timestamps[i + 1], (
                f"WallClock.now() violated monotonicity at index {i}: "
                f"{timestamps[i]!r} > {timestamps[i + 1]!r}"
            )

    def test_isinstance_clock_port(self) -> None:
        """WallClock satisfies ClockPort via runtime_checkable isinstance."""
        clock = WallClock()
        assert isinstance(clock, ClockPort)

    def test_now_has_zero_utc_offset(self) -> None:
        """now() UTC offset is exactly zero — not just non-None tzinfo."""
        clock = WallClock()
        result = clock.now()
        assert result.utcoffset() == timedelta(0), (
            f"Expected UTC offset of 0, got {result.utcoffset()!r}"
        )


class TestWallClockProperty:
    """Property-based tests for WallClock using Hypothesis."""

    @given(st.integers(min_value=1, max_value=10))
    @settings(max_examples=20)
    def test_now_always_utc_across_multiple_calls(self, n_calls: int) -> None:
        """Property: n consecutive now() calls all have zero UTC offset (A-2.2)."""
        clock = WallClock()
        results = [clock.now() for _ in range(n_calls)]
        for i, ts in enumerate(results):
            assert ts.tzinfo is not None, (
                f"WallClock.now() returned naive datetime on call {i}: {ts!r}"
            )
            assert ts.utcoffset() == timedelta(0), (
                f"WallClock.now() returned non-UTC offset on call {i}: utcoffset={ts.utcoffset()!r}"
            )

    @given(st.integers(min_value=2, max_value=5))
    @settings(max_examples=10)
    def test_now_monotonic_across_n_calls(self, n_calls: int) -> None:
        """Property: n consecutive now() calls are monotonically non-decreasing."""
        clock = WallClock()
        timestamps = [clock.now() for _ in range(n_calls)]
        for i in range(len(timestamps) - 1):
            assert timestamps[i] <= timestamps[i + 1], (
                f"Monotonicity violated between calls {i} and {i + 1}: "
                f"{timestamps[i]!r} > {timestamps[i + 1]!r}"
            )
