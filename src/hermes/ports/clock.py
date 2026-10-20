"""Clock port — abstract Protocol for time sources.

Implementations live in :mod:`hermes.adapters.clock`.

- Production: :class:`~hermes.adapters.clock.WallClock` (wraps
  :func:`hermes.infrastructure.time.now_utc`).
- Testing / backtest: :class:`~hermes.adapters.clock.SimulatedClock`
  (deterministic, manually advanced).
- Replay: a future ``ReplayClock`` (advances with each replayed event).

Reference: SA-8 §4.1 S-01 — deterministic replay requires an injected
clock so domain logic never reads the wall clock directly.
"""

from datetime import datetime
from typing import Protocol, runtime_checkable

__all__ = ["ClockPort"]


@runtime_checkable
class ClockPort(Protocol):
    """Minimum contract every clock implementation must satisfy.

    Any object with a compliant :meth:`now` method is a valid
    ``ClockPort`` — no inheritance required (structural typing via
    :class:`typing.Protocol`).  :func:`~typing.runtime_checkable`
    enables ``isinstance`` checks in conformance tests.

    Domain code that needs the current time MUST accept a ``ClockPort``
    as a constructor argument and call ``self._clock.now()`` rather than
    importing :func:`hermes.infrastructure.time.now_utc` directly.  This
    is the single most important abstraction for replay determinism.
    """

    def now(self) -> datetime:
        """Return the current instant as a timezone-aware UTC datetime.

        Implementations MUST return a ``datetime`` with non-``None``
        ``tzinfo`` (always UTC).  Naive ``datetime`` objects are a
        contract violation.

        Returns:
            A timezone-aware :class:`~datetime.datetime` in UTC.
        """
        ...
