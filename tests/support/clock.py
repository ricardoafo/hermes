"""The one hand-moved clock tests pass where code reads the time.

Code under test reads the time either through ``ClockPort.now()`` or by
calling a ``Callable[[], datetime]``; this fake answers both, so a test needs
no private ``_Clock`` of its own (issue #1182).
"""

from datetime import datetime, timedelta

__all__ = ["FakeClock"]


class FakeClock:
    """Clock that always reads ``current`` until the test moves it."""

    def __init__(self, current: datetime) -> None:
        """Start at ``current``."""
        self.current = current

    def now(self) -> datetime:
        """Return the instant the test set."""
        return self.current

    def __call__(self) -> datetime:
        """Return the instant the test set, for code that takes a ``() -> datetime``."""
        return self.current

    def advance(self, delta: timedelta) -> None:
        """Move the clock on by ``delta``."""
        self.current += delta
