"""In-process pub/sub EventBus with synchronous dispatch.

The bus is the fan-out point for domain events. Publishers (use cases)
call ``publish(event)``; subscribers (notifier adapter, ledger listener,
LLM audit collector) call ``subscribe(EventType, handler)`` once at
composition time and receive every subsequent matching event.

Design invariants (property-tested — see tests/unit/domain/events/):

- **Synchronous**: ``publish`` returns only after every subscriber has run.
  No queues, no threads, no asyncio — domain is sync (code-conventions §5).
- **Registration order**: subscribers fire in the order they were registered
  for a given event type. Deterministic ordering matters for audit trails.
- **Fan-out per event type**: subscribing to ``TradeExecuted`` receives ONLY
  ``TradeExecuted`` events, not ``AnomalyDetected``. Exact-type match, no
  MRO walking — a subscriber that wants ``DomainEvent`` fan-in must
  subscribe explicitly to each concrete type.
- **Failure isolation**: a handler that raises does NOT prevent later
  handlers from running. Exceptions are collected and re-raised as a single
  ``EventDispatchError`` after every handler has run. This keeps the ledger
  transaction contract intact (invariant I-06) — one failing subscriber
  cannot silently drop side-effects for the rest. Only ``Exception`` is
  collected: ``KeyboardInterrupt``, ``SystemExit`` and asyncio cancellation
  stop the dispatch at once, so a shutdown is never deferred or disguised.

Reference: ADR-003 §00 (Domain Events pattern), §02 (data flow — every
trade fans out to ledger + notifier + audit), code-conventions.md §1
(Ports & Adapters — bus stays in the domain because it's pure Python).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from hermes.domain.errors import DomainError
from hermes.domain.events.base import DomainEvent

__all__ = ["EventBus", "EventDispatchError", "Handler"]


type Handler[E: DomainEvent] = Callable[[E], None]
"""Alias for the subscriber callable shape — sync, void-returning."""


@dataclass(frozen=True, slots=True)
class EventDispatchError(DomainError):
    """One or more subscribers raised while handling a published event.

    Aggregates every subscriber exception so callers see the full failure
    picture — not just the first one. The list preserves the dispatch
    order (which matches registration order for the failing event type).

    Fields:
        event_type_name: Qualified class name of the event that fanned out.
        failures: Ordered tuple of exceptions raised by subscribers.

    Reference: code-conventions.md §2 — DomainError for expected outcomes;
        a handler raising is expected in the same sense as any I/O failure
        an adapter can surface.
    """

    event_type_name: str
    failures: tuple[Exception, ...]

    def __str__(self) -> str:
        """Return human-readable summary listing every failure."""
        summary = ", ".join(f"{type(e).__name__}: {e}" for e in self.failures)
        return (
            f"EventBus dispatch of {self.event_type_name} raised "
            f"{len(self.failures)} handler error(s): {summary}"
        )


@dataclass(slots=True)
class EventBus:
    """Synchronous in-process publish/subscribe registry.

    One bus instance per application graph — constructed in ``entrypoints/``
    and injected into use cases and adapters. Not a singleton: composition
    passes the same instance to every collaborator that needs it.

    The bus is NOT thread-safe. hermes runs sync-per-task and multiplexes
    async I/O at the adapter edge (anyio) — sub-agents that need to publish
    from a threaded context must marshal back to the main task first.

    Reference: code-conventions.md §5 (sync domain), §1 (constructor DI).
    """

    _subscribers: dict[type[DomainEvent], list[Handler[DomainEvent]]] = field(default_factory=dict)

    def subscribe[E: DomainEvent](self, event_type: type[E], handler: Handler[E]) -> None:
        """Register ``handler`` to receive every subsequent ``event_type`` event.

        Handlers fire in registration order at publish time. Re-registering
        the same handler adds a second slot — the caller receives the event
        twice. That is intentional: idempotency is the handler's job, not
        the bus's.

        Args:
            event_type: Exact concrete subclass of ``DomainEvent`` to filter on.
                Subscription is by exact type — no MRO walking. To receive
                every event class, subscribe individually.
            handler: Sync callable ``(event) -> None``. May raise; exceptions
                are captured and re-raised as ``EventDispatchError`` (see
                module docstring).
        """
        # Cast is safe: at call time E == event_type, so Handler[E] is the
        # exact shape stored under event_type. Erased to the base bound so
        # the dict is homogeneous and mypy stops widening variance.
        bucket = self._subscribers.setdefault(event_type, [])
        bucket.append(handler)  # type: ignore[arg-type]

    def publish(self, event: DomainEvent) -> None:
        """Dispatch ``event`` to every subscriber registered for its exact type.

        Fan-out is synchronous: this method returns only after every handler
        has run (or raised). If ANY handler raises, the remaining handlers
        still run — then a single ``EventDispatchError`` is raised carrying
        every collected exception in dispatch order.

        Subscribers registered under a different concrete type do NOT
        receive the event, even if the types share a base class. This is
        deliberate: type-erased fan-in requires the subscriber to opt in
        via multiple ``subscribe`` calls.

        Args:
            event: A concrete ``DomainEvent`` instance. The bus looks up
                ``type(event)`` for dispatch — passing a base-class typed
                variable still routes to the concrete-type subscribers.

        Raises:
            EventDispatchError: When at least one subscriber raised.
                The exception carries every raised exception in dispatch
                order (which matches registration order).
        """
        bucket = self._subscribers.get(type(event), [])
        if not bucket:
            return
        failures: list[Exception] = []
        for handler in bucket:
            try:
                handler(event)
            except Exception as exc:
                failures.append(exc)
        if failures:
            raise EventDispatchError(
                event_type_name=type(event).__qualname__,
                failures=tuple(failures),
            )

    def subscriber_count(self, event_type: type[DomainEvent]) -> int:
        """Return how many handlers are registered for ``event_type``.

        Introspection helper for tests and health checks; production code
        should not branch on this value.

        Args:
            event_type: Concrete ``DomainEvent`` subclass to count.

        Returns:
            Number of handlers registered for the exact type (0 if none).
        """
        return len(self._subscribers.get(event_type, []))
