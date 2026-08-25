"""SimulatedClock — deterministic ClockPort for tests, backtest, and replay.

Time does NOT advance on reads.  Callers must explicitly call
:meth:`~SimulatedClock.advance` or :meth:`~SimulatedClock.set` to move
the clock forward.  This makes all domain decisions deterministic: given the
same sequence of ``advance`` / ``set`` calls, ``now()`` always returns the
same value at each point in time — regardless of wall-clock.

Thread safety: an internal :class:`threading.Lock` guards the internal
``_current`` state so the clock is safe to share across threads (e.g.
calibration or replay orchestration).

Reference: SA-8 §4.1 S-01, NautilusTrader ``TestClock`` pattern,
openspec/changes/spec-4a-clock-port/change.md D-4 (monotonicity policy).
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta

__all__ = ["SimulatedClock"]


class SimulatedClock:
    """Deterministic ClockPort for tests, backtest, and replay scenarios.

    Time does NOT advance on reads.  Callers advance the clock explicitly
    via :meth:`advance` (relative) or :meth:`set` (absolute).

    Args:
        start: Initial simulated time.  MUST be timezone-aware UTC;
            passing a naive ``datetime`` raises :class:`ValueError`.

    Example::

        from datetime import UTC, datetime, timedelta
        from hermes.adapters.clock import SimulatedClock

        clock = SimulatedClock(start=datetime(2025, 1, 1, tzinfo=UTC))
        assert clock.now() == datetime(2025, 1, 1, tzinfo=UTC)

        clock.advance(timedelta(hours=1))
        assert clock.now() == datetime(2025, 1, 1, 1, tzinfo=UTC)

        clock.set(datetime(2025, 6, 1, tzinfo=UTC))
        assert clock.now() == datetime(2025, 6, 1, tzinfo=UTC)
    """

    def __init__(self, *, start: datetime) -> None:
        """Initialise the clock at ``start``.

        Args:
            start: Initial simulated time.  MUST be timezone-aware.

        Raises:
            ValueError: If ``start`` is a naive ``datetime``.
        """
        if start.tzinfo is None:
            raise ValueError(
                f"SimulatedClock.start must be timezone-aware (UTC). Got naive datetime: {start!r}"
            )
        self._current: datetime = start.astimezone(UTC)
        self._lock: threading.Lock = threading.Lock()

    def now(self) -> datetime:
        """Return the current simulated time without advancing it.

        Repeated calls without an intervening :meth:`advance` or
        :meth:`set` return the same value — this is the property that
        makes replay deterministic.

        Returns:
            A timezone-aware :class:`~datetime.datetime` in UTC.
        """
        with self._lock:
            return self._current

    def advance(self, delta: timedelta) -> None:
        """Move the simulated clock forward by ``delta``.

        Args:
            delta: Time increment.  Must be non-negative; advancing by
                :class:`~datetime.timedelta` zero is a no-op but is
                permitted.

        Raises:
            ValueError: If ``delta`` is negative.
        """
        if delta < timedelta(0):
            raise ValueError(f"SimulatedClock.advance: delta must be non-negative, got {delta!r}")
        with self._lock:
            self._current = self._current + delta

    def set(self, t: datetime) -> None:
        """Jump the simulated clock to ``t`` (no backwards travel).

        Args:
            t: New simulated time.  Must be >= the current simulated time
                (backwards time travel is forbidden per SA-8 monotonicity
                policy D-4).  Must be timezone-aware.

        Raises:
            ValueError: If ``t < self.now()`` (backwards time travel) or
                if ``t`` is a naive ``datetime``.
        """
        if t.tzinfo is None:
            raise ValueError(f"SimulatedClock.set: t must be timezone-aware, got naive {t!r}")
        t_utc = t.astimezone(UTC)
        with self._lock:
            if t_utc < self._current:
                raise ValueError(
                    "SimulatedClock.set: cannot travel backwards. "
                    f"current={self._current!r}, requested={t_utc!r}"
                )
            self._current = t_utc
