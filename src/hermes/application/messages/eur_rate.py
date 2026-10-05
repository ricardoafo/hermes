"""Look up the EUR rate of an event for a push, without ever failing it.

The notifier adapters hold an optional lookup that maps an event to the
official rate of its quote currency (units per euro). The rate travels as data
into the note builders; the notification port does not change (change
``668-eur-prices-in-pushes`` D-2). A missing lookup, a ``None`` answer and a
raising lookup all mean "no rate", so a push keeps its quote-currency text.

Reference: openspec/changes/668-eur-prices-in-pushes/change.md.
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal

import structlog

from hermes.domain.events.base import DomainEvent

__all__ = ["EurRateLookup", "resolve_eur_rate"]

EurRateLookup = Callable[[DomainEvent], Decimal | None]
"""Maps an event to its quote currency's units per euro, or ``None`` when unknown."""

_log = structlog.get_logger(__name__)


def resolve_eur_rate(lookup: EurRateLookup | None, event: DomainEvent) -> Decimal | None:
    """Return the EUR rate for ``event``, or ``None`` when none is available.

    Args:
        lookup: The adapter's injected lookup, or ``None``.
        event: The event about to be rendered.

    Returns:
        The rate, or ``None`` when there is no lookup, it has no answer, it
        answers a non-positive rate, or it raises.
    """
    if lookup is None:
        return None
    try:
        rate = lookup(event)
    except Exception as exc:
        _log.warning(
            "eur_rate_lookup_failed", event_type=type(event).__name__, error=type(exc).__name__
        )
        return None
    if rate is None or rate <= Decimal(0):
        return None
    return rate
