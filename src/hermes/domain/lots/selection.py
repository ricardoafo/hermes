"""Pure FIFO lot selection — oldest-first with deterministic tie-breaker.

The selection algorithm is expressed as a pure function so it can be
property-tested with hypothesis without any I/O. The SQLite adapter reads
the open-lots view, calls :func:`select_lots_fifo`, and persists the
returned consumptions inside the same transaction that persists the
``TradeExecuted`` event (invariant I-06).

FIFO tie-breaker
----------------

When two lots share the exact same ``opened_at`` timestamp, FIFO order is
undefined by "oldest first" alone. The tie-breaker is **lexical order on
:class:`LotId`**. Rationale:

- :class:`LotId` wraps a ULID whose 26 characters begin with a millisecond
  timestamp component followed by 80 bits of randomness. Two ``LotId``
  values generated in the same millisecond are extremely rare (2^80 space)
  and, when they do collide, the string comparison is deterministic and
  stable across processes.
- No hidden clock dependency and no reliance on database ``ROWID`` — the
  algorithm is pure and gives the same result offline (backtests) as
  in production.

This means: primary sort key is ``opened_at`` ascending; secondary sort
key is ``lot_id.value`` ascending.

Reference: ADR-003 §03 (FIFO worked example),
code-conventions.md §1 (Value Object), §3 (immutability).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from returns.result import Failure, Result, Success

from hermes.domain.errors import DomainError
from hermes.domain.lots.lot import Lot
from hermes.domain.lots.lot_consumption import LotConsumption
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.quantity import Quantity

__all__ = ["InsufficientInventory", "select_lots_fifo"]


@dataclass(frozen=True, slots=True)
class InsufficientInventory(DomainError):
    """Total open remaining inventory is smaller than the SELL quantity.

    Emitted by :func:`select_lots_fifo` when the caller asks to consume
    more than every open lot combined can supply. The SELL cannot proceed;
    the application-layer use case is responsible for translating this to
    a user-facing error (or, in the LLM supervisor context, a REJECT).

    Fields:
        requested: The quantity the SELL wanted to consume.
        available: The total ``remaining_qty`` across all supplied open lots.

    Reference: ADR-003 §03.
    """

    requested: Quantity
    available: Quantity

    def __str__(self) -> str:
        """Return human-readable summary."""
        return (
            f"Insufficient inventory: requested {self.requested.amount} "
            f"{self.requested.asset}, only {self.available.amount} available"
        )


def select_lots_fifo(
    open_lots: Iterable[Lot],
    quantity_to_consume: Quantity,
    sell_price_per_unit: Money,
    at_time: datetime,
) -> Result[tuple[LotConsumption, ...], DomainError]:
    """Choose FIFO lots to consume ``quantity_to_consume`` and record the draw.

    Algorithm:

    1. Filter to lots whose ``asset`` matches ``quantity_to_consume.asset``.
    2. Sort by ``opened_at`` ascending, then by ``lot_id.value`` ascending
       (see module docstring for tie-breaker rationale).
    3. Consume greedily from the head of the list until the requested amount
       is drained. The last lot touched may be consumed partially — earlier
       lots are always fully drained before moving on.
    4. Emit one :class:`LotConsumption` per lot touched.

    Args:
        open_lots: Iterable of currently-open :class:`Lot` instances. May
            include lots for other assets — they are filtered internally.
        quantity_to_consume: Non-zero quantity the SELL wants to draw.
        sell_price_per_unit: EUR fill price per one unit of the asset.
        at_time: UTC-aware moment of the SELL — attached to every emitted
            consumption for tax-report reconciliation.

    Returns:
        ``Success(tuple[LotConsumption, ...])`` in FIFO order (oldest lot
        first) when inventory is sufficient. ``Failure(InsufficientInventory)``
        when combined ``remaining_qty`` across matching open lots is less
        than ``quantity_to_consume.amount``. ``Failure(InvalidLot)`` when
        arguments violate the LotConsumption invariants (e.g. non-EUR sell
        price, or zero consumption requested).
    """
    # 1. Filter to matching asset only.
    matching = [lot for lot in open_lots if lot.asset == quantity_to_consume.asset]

    # 2. Sort — primary opened_at asc, secondary lot_id.value asc (see docstring).
    matching.sort(key=lambda lot: (lot.opened_at, lot.lot_id.value))

    # 3. Verify inventory is sufficient before touching anything.
    available_total = sum((lot.remaining_qty.amount for lot in matching), start=Decimal("0"))
    if available_total < quantity_to_consume.amount:
        return Failure(
            InsufficientInventory(
                requested=quantity_to_consume,
                available=Quantity(amount=available_total, asset=quantity_to_consume.asset),
            )
        )

    # 4. Greedy consumption — earlier lots always drained before later ones.
    remaining_to_consume = quantity_to_consume.amount
    consumptions: list[LotConsumption] = []
    for lot in matching:
        if remaining_to_consume == Decimal("0"):
            break
        take = min(lot.remaining_qty.amount, remaining_to_consume)
        consumption_result = LotConsumption.record(
            lot_id=lot.lot_id,
            consumed_qty=Quantity(amount=take, asset=quantity_to_consume.asset),
            sell_price_per_unit=sell_price_per_unit,
            lot_cost_basis_per_unit=lot.cost_basis_per_unit,
            consumed_at=at_time,
        )
        if isinstance(consumption_result, Failure):
            return Failure(consumption_result.failure())
        consumptions.append(consumption_result.unwrap())
        remaining_to_consume -= take

    return Success(tuple(consumptions))
