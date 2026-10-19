"""SignalNotifier — fan one signal event out to the routed notifier channels.

The serve loop calls :meth:`SignalNotifier.deliver` from a worker thread
(both channel adapters use a blocking HTTP client), after the event has been
written to the ledger. The ledger row is the record of truth; a notification
is a side effect, so a channel failure must not stop signal evaluation.

Failure policy:

- Any exception from one channel is collected and the next channel still
  runs: an :class:`~hermes.domain.errors.InfraError` (transport, HTTP or API
  failure) and a bug alike, such as the ``ValueError`` of a missing template
  (issue #854). A bug that escaped would stop serve after the fill and lose
  the push for good.
- :meth:`deliver` reports which channels took the push and which raised, so
  the caller can record it and push the failed ones again later.
- Zero wired channels is not an error: the router drops unwired slots.

Reference: 309-serve-bar-loop change.md D-3, D-4, D-5;
    ``docs/decisions/2026-10-03-signal-notification-routing.md``.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass

from hermes.application.notification_router import EventClass, RouterConfig, channels_for
from hermes.domain.events.signal_emitted import SignalEmitted
from hermes.ports.notifier import NotifierPort

__all__ = ["Delivery", "SignalNotifier"]


@dataclass(frozen=True, slots=True)
class Delivery:
    """Outcome of one push of a signal.

    Fields:
        delivered: Channels that took the push, in push order.
        failures: ``(channel, exception)`` for each channel that raised.
    """

    delivered: tuple[str, ...]
    failures: tuple[tuple[str, Exception], ...]

    @property
    def failed(self) -> tuple[str, ...]:
        """Return the channels that raised, in push order."""
        return tuple(name for name, _ in self.failures)


class SignalNotifier:
    """Send a :class:`SignalEmitted` to every channel its routing row names.

    Args:
        channels: The wired notifier adapters, as the router consumes them.
    """

    def __init__(self, channels: RouterConfig) -> None:
        """Bind to the wired channels."""
        self._channels = channels

    def deliver(self, event: SignalEmitted, only: Collection[str] | None = None) -> Delivery:
        """Push ``event`` to every routed channel, isolating each channel's failure.

        Args:
            event: The signal event to announce.
            only: Push to these channels alone (a retry), or to all when ``None``.

        Returns:
            Which channels took the push and which raised.
        """
        delivered: list[str] = []
        failures: list[tuple[str, Exception]] = []
        for name, adapter in self._routed():
            if only is not None and name not in only:
                continue
            try:
                adapter.notify(event)
            except Exception as exc:
                failures.append((name, exc))
            else:
                delivered.append(name)
        return Delivery(delivered=tuple(delivered), failures=tuple(failures))

    def _routed(self) -> tuple[tuple[str, NotifierPort], ...]:
        """Return the wired channels the signal row routes to, Telegram first."""
        telegram_fires, slack_fires = channels_for(EventClass.SIGNAL_EMITTED)
        slots = (
            ("telegram", self._channels.telegram if telegram_fires else None),
            ("slack", self._channels.slack if slack_fires else None),
        )
        return tuple((name, adapter) for name, adapter in slots if adapter is not None)
