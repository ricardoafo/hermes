"""Port-conformance tests for :class:`hermes.ports.clock.ClockPort`.

Verifies that both concrete adapters — :class:`WallClock` and
:class:`SimulatedClock` — return the time the ``ClockPort`` contract asks
for: a timezone-aware UTC instant, read from the right source.

Reference: openspec/archive/spec-4a-clock-port/change.md A-2.1, A-3.1.
"""

from __future__ import annotations

import typing
from datetime import UTC, datetime, timedelta

from hermes.adapters.clock import SimulatedClock, WallClock
from hermes.ports.clock import ClockPort

_EPOCH = datetime(2025, 1, 1, tzinfo=UTC)


class TestClockPortConformance:
    """Both adapters return the instant ClockPort promises."""

    def test_wall_clock_now_reads_the_system_clock(self) -> None:
        """WallClock.now() lies between two readings of the system clock taken around it."""
        before = datetime.now(UTC)
        result = WallClock().now()
        after = datetime.now(UTC)
        assert before <= result <= after

    def test_wall_clock_now_is_timezone_aware(self) -> None:
        """WallClock.now() always returns a tz-aware datetime with zero UTC offset (A-2.2)."""
        clock = WallClock()
        result = clock.now()
        assert result.tzinfo is not None, (
            "WallClock.now() returned a naive datetime — tzinfo must not be None"
        )
        assert result.utcoffset() == timedelta(0), (
            f"WallClock.now() is not UTC — utcoffset={result.utcoffset()!r}"
        )

    def test_simulated_clock_now_is_its_start(self) -> None:
        """SimulatedClock.now() returns the start it was built with, and keeps returning it."""
        clock = SimulatedClock(start=_EPOCH)
        assert [clock.now(), clock.now()] == [_EPOCH, _EPOCH]

    def test_simulated_clock_now_is_timezone_aware(self) -> None:
        """SimulatedClock.now() always returns a tz-aware datetime with zero UTC offset."""
        clock = SimulatedClock(start=_EPOCH)
        result = clock.now()
        assert result.tzinfo is not None, (
            "SimulatedClock.now() returned a naive datetime — tzinfo must not be None"
        )
        assert result.utcoffset() == timedelta(0), (
            f"SimulatedClock.now() is not UTC — utcoffset={result.utcoffset()!r}"
        )

    def test_clock_port_protocol_has_now_method(self) -> None:
        """ClockPort protocol exposes now() in its annotations."""
        hints = typing.get_type_hints(ClockPort.now)
        assert "return" in hints, "ClockPort.now must have a return type annotation"
        assert hints["return"] is datetime, (
            f"ClockPort.now must return datetime, got {hints['return']!r}"
        )
