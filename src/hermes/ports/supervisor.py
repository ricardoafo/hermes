"""SupervisorReadPort — the ledger questions the read-only supervisor may ask.

Extends :class:`~hermes.ports.status.StatusReadPort` with the signal history,
the bars the loop handled and the fills it made, without prices, sizes or
fees. Implementations open the ledger read-only.

Reference: issue #470 (part of #34); issue #365; ADR-003 §08; ADR-012 D-06, D-07.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.value_objects.pair import Pair
from hermes.ports.status import StatusReadPort

__all__ = [
    "BarCount",
    "CertificationFact",
    "CertificationLookup",
    "FillFact",
    "SignalFact",
    "SupervisorReadPort",
]


@dataclass(frozen=True, slots=True)
class SignalFact:
    """One recorded signal, without prices or sizes.

    Attributes:
        pair: Trading pair.
        kind: ``entry`` or ``exit``.
        occurred_at: When the signal's bar closed, UTC-aware.
        exit_reason: The exit rule that fired (``chandelier``, ``time_stop``,
            ``regime_break``), or ``None`` for an entry.
    """

    pair: Pair
    kind: str
    occurred_at: datetime
    exit_reason: str | None = None


@dataclass(frozen=True, slots=True)
class BarCount:
    """How many bars of one stream the loop handled.

    Attributes:
        pair: Trading pair.
        interval: Candle-duration bucket.
        count: Bars handled.
    """

    pair: Pair
    interval: KlineInterval
    count: int


@dataclass(frozen=True, slots=True)
class FillFact:
    """One simulated fill, without price, quantity or fee.

    Attributes:
        pair: Trading pair.
        side: ``BUY`` or ``SELL``.
        occurred_at: When the fill was recorded, UTC-aware.
    """

    pair: Pair
    side: str
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class CertificationFact:
    """A strategy's certification state, without the metrics behind it.

    Attributes:
        state: Lifecycle state name, e.g. ``SHADOW_CERTIFIED``.
        entered_at: When the strategy entered ``state``, or ``None``.
    """

    state: str
    entered_at: datetime | None


type CertificationLookup = Callable[[str], CertificationFact | None]
"""Return a strategy's certification state by id, or ``None`` without a record."""


@runtime_checkable
class SupervisorReadPort(StatusReadPort, Protocol):
    """Read-only ledger access for the supervisor."""

    def signals_since(self, since: datetime) -> tuple[SignalFact, ...]:
        """Return the signals at or after ``since``, oldest first.

        Args:
            since: Inclusive lower bound, UTC-aware.
        """
        ...

    def bars_since(self, since: datetime) -> tuple[BarCount, ...]:
        """Return how many bars each stream handled at or after ``since``.

        Args:
            since: Inclusive lower bound on the bar's close, UTC-aware.
        """
        ...

    def fills_since(self, since: datetime) -> tuple[FillFact, ...]:
        """Return the fills recorded at or after ``since``, oldest first.

        Args:
            since: Inclusive lower bound, UTC-aware.
        """
        ...
