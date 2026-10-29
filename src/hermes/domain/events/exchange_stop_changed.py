"""ExchangeStopChanged domain event: what hermes did to a stop the exchange holds (#1080).

The ledger's record of each exchange-held stop: placed, adopted after a lost
answer or a crash, cancelled, found filled or gone, or failed. The newest row
of a pair that is not a failure is what hermes believes rests on the exchange,
and the restart reconciles the exchange against it before any bar is handled.

The row decides no trade: the strategy's own exit stays the one the ledger
records as a trade (Code Owner decision, 2026-10-19). It holds the stop price,
so like a stop mark it must not reach the journal or an AI-facing tool before
certification (ADR-012 D-02).

Reference: GitHub issue #1080; openspec/changes/1080-exchange-stops/change.md.
"""

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import ClassVar, Final

from hermes.domain.events.base import DomainEvent
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.strategy_id import StrategyId
from hermes.domain.value_objects.time import require_utc

__all__ = ["ExchangeStopChanged", "StopAction"]

_REASON_SHAPE: Final = re.compile(r"^[a-z0-9]+(_[a-z0-9]+)*$")


class StopAction(StrEnum):
    """What happened to the stop."""

    PLACED = "placed"
    """Hermes rested a new stop."""
    ADOPTED = "adopted"
    """A resting stop the ledger did not name already protects the position."""
    CANCELLED = "cancelled"
    """Hermes cancelled a resting stop: the stop moved, the position exited, or it was extra."""
    FILLED = "filled"
    """The stop hermes rested was triggered and sold on the exchange."""
    GONE = "gone"
    """The stop hermes rested is no longer on the exchange and sold nothing."""
    FAILED = "failed"
    """Hermes could not read, cancel or place a stop; the position may be unprotected."""


_RESTS: Final = frozenset({StopAction.PLACED, StopAction.ADOPTED})


@dataclass(frozen=True, slots=True)
class ExchangeStopChanged(DomainEvent):
    """One change to a stop the exchange holds for a strategy's position.

    Fields:
        strategy_id: The strategy holding the position.
        pair: The position's pair.
        action: What happened.
        entry_time: Close time of the entry bar of the position the stop
            protects, or ``None`` when no held position is known (an extra
            stop cancelled while flat).
        order_id: The exchange's order id, or ``None`` when no order exists
            (a failed placement).
        qty: Base quantity of the stop, or ``None`` with no order.
        stop_price: Trigger price of the stop, or ``None`` with no order.
        reason: Fixed snake_case reason, such as ``stop_moved``, or ``None``.

    Raises:
        ValueError: ``reason`` is not snake_case, ``entry_time`` is naive, or
            a placed or adopted stop has no order id, quantity or price.
    """

    __event_schema_version__: ClassVar[int] = 1

    strategy_id: StrategyId
    pair: Pair
    action: StopAction
    entry_time: datetime | None
    order_id: str | None
    qty: Decimal | None
    stop_price: Decimal | None
    reason: str | None

    def __post_init__(self) -> None:
        """Refuse a free-text reason or a resting stop without its order, then hash."""
        if self.reason is not None and not _REASON_SHAPE.match(self.reason):
            raise ValueError(f"ExchangeStopChanged.reason must be snake_case: {self.reason!r}")
        if self.entry_time is not None:
            require_utc(self.entry_time, name="ExchangeStopChanged.entry_time")
        if self.action in _RESTS and None in (self.order_id, self.qty, self.stop_price):
            raise ValueError(f"a {self.action} stop needs its order id, quantity and price")
        DomainEvent.__post_init__(self)

    @property
    def rests(self) -> bool:
        """Return whether this row says the stop rests on the exchange."""
        return self.action in _RESTS
