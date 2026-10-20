"""WallClock — production ClockPort backed by the real system clock.

Delegates to :func:`hermes.infrastructure.time.now_utc` so the
UTC-awareness guarantee is inherited from the centralised helper.  Inject
:class:`~hermes.adapters.clock.SimulatedClock` in tests and backtest
scenarios to keep domain logic replayable.

Reference: SA-8 §4.1 S-01, openspec/archive/spec-4a-clock-port/change.md.
"""

from datetime import datetime

from hermes.infrastructure.time import now_utc

__all__ = ["WallClock"]


class WallClock:
    """ClockPort implementation backed by the real system clock.

    Delegates to :func:`hermes.infrastructure.time.now_utc` so the
    UTC-awareness guarantee is inherited from the centralised helper.
    Suitable for production use.

    Example::

        from hermes.adapters.clock import WallClock

        clock = WallClock()
        ts = clock.now()   # timezone-aware datetime in UTC
    """

    def now(self) -> datetime:
        """Return the current UTC instant from the system clock.

        Returns:
            A timezone-aware :class:`~datetime.datetime` in UTC.
        """
        return now_utc()
