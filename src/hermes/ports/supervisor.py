"""SupervisorReadPort — the ledger questions the read-only supervisor may ask.

Extends :class:`~hermes.ports.status.StatusReadPort` with the signal history,
without prices. Implementations open the ledger read-only.

Reference: issue #470 (part of #34); ADR-003 §08; ADR-012 D-06, D-07.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from hermes.domain.value_objects.pair import Pair
from hermes.ports.status import StatusReadPort

__all__ = ["SignalFact", "SupervisorReadPort"]


@dataclass(frozen=True, slots=True)
class SignalFact:
    """One recorded signal, without prices or sizes.

    Attributes:
        pair: Trading pair.
        kind: ``entry`` or ``exit``.
        occurred_at: When the signal's bar closed, UTC-aware.
    """

    pair: Pair
    kind: str
    occurred_at: datetime


@runtime_checkable
class SupervisorReadPort(StatusReadPort, Protocol):
    """Read-only ledger access for the supervisor."""

    def signals_since(self, since: datetime) -> tuple[SignalFact, ...]:
        """Return the signals at or after ``since``, oldest first.

        Args:
            since: Inclusive lower bound, UTC-aware.
        """
        ...
