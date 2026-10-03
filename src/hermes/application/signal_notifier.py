"""SignalNotifier — fan one signal event out to the routed notifier channels.

The serve loop calls :meth:`SignalNotifier.dispatch` from a worker thread
(both channel adapters use a blocking HTTP client), after the event has been
written to the ledger. The ledger row is the record of truth; a notification
is a side effect, so a channel outage must not stop signal evaluation.

Failure policy:

- An :class:`~hermes.domain.errors.InfraError` from one channel (transport,
  HTTP or API failure) is collected and the next channel still runs. After
  the last channel one :class:`~hermes.domain.events.event_bus.EventDispatchError`
  carries every collected failure.
- Any other exception (a missing template is a ``ValueError``) is a bug and
  propagates at once.
- Zero wired channels is not an error: the router drops unwired slots.

Reference: 309-serve-bar-loop change.md D-3, D-4, D-5;
    ``docs/decisions/2026-10-03-signal-notification-routing.md``.
"""

from __future__ import annotations

from hermes.application.notification_router import EventClass, RouterConfig, route
from hermes.domain.errors import InfraError
from hermes.domain.events.event_bus import EventDispatchError
from hermes.domain.events.signal_emitted import SignalEmitted

__all__ = ["SignalNotifier"]


class SignalNotifier:
    """Send a :class:`SignalEmitted` to every channel its routing row names.

    Args:
        channels: The wired notifier adapters, as the router consumes them.
    """

    def __init__(self, channels: RouterConfig) -> None:
        """Bind to the wired channels."""
        self._channels = channels

    def dispatch(self, event: SignalEmitted) -> None:
        """Notify every routed channel, isolating per-channel infra failures.

        Args:
            event: The signal event to announce.

        Raises:
            EventDispatchError: At least one channel raised an
                :class:`InfraError`; every channel was still attempted.
            ValueError: No template exists for the event (not isolated).
        """
        failures: list[BaseException] = []
        for adapter in route(EventClass.SIGNAL_EMITTED, self._channels):
            try:
                adapter.notify(event)
            except InfraError as exc:
                failures.append(exc)
        if failures:
            raise EventDispatchError(
                event_type_name=type(event).__qualname__,
                failures=tuple(failures),
            )
