"""LotClosed domain event — emitted when a FIFO tax lot is fully consumed.

A lot closes when a SELL trade drains its ``remaining_qty`` to zero. The
event captures the identity of the exhausted lot plus the realized gain
attributable to its consumption so downstream subscribers (tax reporter,
LLM audit) do not need to re-query the ledger.

Reference: ADR-003 §03 (FIFO worked example — "lot #1 fully consumed"),
code-conventions.md §10.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from hermes.domain.events.base import DomainEvent
from hermes.domain.value_objects.lot_id import LotId
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.quantity import Quantity

__all__ = ["LotClosed"]


@dataclass(frozen=True, slots=True)
class LotClosed(DomainEvent):
    """A FIFO tax lot has been fully consumed by SELL trades.

    Emitted in the same SQLite transaction as the terminal ``TradeExecuted``
    and the ``lot_consumptions`` row that drained the lot (invariant I-06).

    Fields:
        lot_id: Identifier of the lot that hit ``remaining_qty == 0``.
        consumed_qty: Total quantity of the base asset ever consumed from
            this lot (matches ``TAX_LOTS.remaining_qty`` at creation time).
        realized_gain: Sum of realized gains across every ``LOT_CONSUMPTIONS``
            row that drew from this lot, in EUR. Can be negative (loss).

    Reference: ADR-003 §03 FIFO worked example.
    """

    __event_schema_version__: ClassVar[int] = 1

    lot_id: LotId
    consumed_qty: Quantity
    realized_gain: Money
