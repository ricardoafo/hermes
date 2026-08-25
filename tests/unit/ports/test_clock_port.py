"""Port-conformance tests for :class:`hermes.ports.clock.ClockPort`.

Verifies that both concrete adapters — :class:`WallClock` and
:class:`SimulatedClock` — structurally satisfy the ``ClockPort`` protocol
and that the protocol's runtime-checkable ``isinstance`` check passes.

Reference: openspec/changes/spec-4a-clock-port/change.md A-2.1, A-3.1.
"""

from __future__ import annotations

import typing
from datetime import UTC, datetime, timedelta

import pytest

from hermes.adapters.clock import SimulatedClock, WallClock
from hermes.ports.clock import ClockPort

_EPOCH = datetime(2025, 1, 1, tzinfo=UTC)


class TestClockPortConformance:
    """Both adapters must satisfy ClockPort at runtime and statically."""

    def test_wall_clock_is_clock_port(self) -> None:
        """WallClock satisfies ClockPort via isinstance (runtime_checkable)."""
        clock = WallClock()
        assert isinstance(clock, ClockPort), (
            "WallClock does not satisfy ClockPort — check that it exposes now() -> datetime"
        )

    def test_simulated_clock_is_clock_port(self) -> None:
        """SimulatedClock satisfies ClockPort via isinstance (runtime_checkable)."""
        clock = SimulatedClock(start=_EPOCH)
        assert isinstance(clock, ClockPort), (
            "SimulatedClock does not satisfy ClockPort — check that it exposes now() -> datetime"
        )

    def test_wall_clock_now_returns_datetime(self) -> None:
        """WallClock.now() returns a datetime instance."""
        clock = WallClock()
        result = clock.now()
        assert isinstance(result, datetime)

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

    def test_simulated_clock_now_returns_datetime(self) -> None:
        """SimulatedClock.now() returns a datetime instance."""
        clock = SimulatedClock(start=_EPOCH)
        result = clock.now()
        assert isinstance(result, datetime)

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

    @pytest.mark.parametrize(
        "adapter_instance",
        [
            WallClock(),
            SimulatedClock(start=datetime(2025, 1, 1, tzinfo=UTC)),
        ],
        ids=["WallClock", "SimulatedClock"],
    )
    def test_all_adapters_are_clock_port_instances(self, adapter_instance: ClockPort) -> None:
        """Parametrised: every adapter satisfies ClockPort."""
        assert isinstance(adapter_instance, ClockPort)
        ts = adapter_instance.now()
        assert isinstance(ts, datetime)
        assert ts.tzinfo is not None
