"""Clock adapters — public re-export surface.

Usage::

    from hermes.adapters.clock import WallClock, SimulatedClock

- :class:`WallClock` — production adapter backed by the real system clock.
- :class:`SimulatedClock` — deterministic adapter for tests and backtest.

Both satisfy :class:`hermes.ports.clock.ClockPort` (structural typing).
"""

from hermes.adapters.clock.simulated import SimulatedClock
from hermes.adapters.clock.wall import WallClock

__all__ = ["SimulatedClock", "WallClock"]
