"""SignalNotified domain event — serve tried to push one signal to its channels.

Delivery rule (issue #854): each signal within the catch-up window is pushed
to each routed channel exactly once in practice, which is at least once with
dedupe. Serve pushes first and writes this row after, so a crash between the
two pushes that signal again on the restart; a crash before the push loses
nothing, because the signal has no row yet and the restart pushes it.

One row is written per push attempt. ``delivered`` names the channels that
took the push, ``failed`` the ones that raised; the newest row of a signal
says which channels still owe it the push. A row with nothing failed settles
the signal, even when no channel was wired.

The row shares the signal's ``correlation_id`` and ``occurred_at`` (the signal
bar's close time), so it carries no wall clock and replays deterministically.

Reference: code-conventions.md §10 (event schema versioning).
"""

from dataclasses import dataclass
from typing import ClassVar

from hermes.domain.events.base import DomainEvent
from hermes.domain.events.signal_emitted import SignalEmitted

__all__ = ["SignalNotified"]


@dataclass(frozen=True, slots=True)
class SignalNotified(DomainEvent):
    """Serve pushed one signal: which channels took it and which raised.

    Build it with :meth:`after` so the signal's identity is copied in one place.

    Fields:
        delivered: Channels that took the push, in push order.
        failed: Channels that raised; the next attempt pushes only these.

    Raises:
        ValueError: On a naive ``occurred_at``, a negative ``event_seq``, or a
            channel named in both ``delivered`` and ``failed``.
    """

    __event_schema_version__: ClassVar[int] = 1

    delivered: tuple[str, ...]
    failed: tuple[str, ...]

    def __post_init__(self) -> None:
        """Compute the payload hash, then check the time and the channel sets.

        Raises:
            ValueError: On a naive ``occurred_at`` or a channel in both sets.
        """
        DomainEvent.__post_init__(self)
        if self.occurred_at.tzinfo is None:
            raise ValueError("SignalNotified.occurred_at must be UTC-aware")
        if set(self.delivered) & set(self.failed):
            raise ValueError("SignalNotified: a channel cannot be delivered and failed")

    @property
    def settled(self) -> bool:
        """Return whether every attempted channel took the push."""
        return not self.failed

    @classmethod
    def after(
        cls, signal: SignalEmitted, *, delivered: tuple[str, ...], failed: tuple[str, ...]
    ) -> SignalNotified:
        """Record one push attempt of ``signal``.

        Args:
            signal: The stored signal that was pushed.
            delivered: Channels that took the push.
            failed: Channels that raised.

        Returns:
            An event keyed to the signal by its ``correlation_id`` and time.
        """
        return cls(
            occurred_at=signal.occurred_at,
            correlation_id=signal.correlation_id,
            delivered=delivered,
            failed=failed,
        )
