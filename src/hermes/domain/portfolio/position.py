"""Position — one open long holding inside a portfolio snapshot.

A position keeps the base-asset quantity, the total cost paid for it in the
quote currency (average-cost accounting, no lots), and the latest mark. All
amounts are :class:`~decimal.Decimal`.

Reference: 308-signal-event-portfolio-state change.md D-5.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, localcontext

from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.precision import EXACT_CONTEXT

__all__ = ["Position"]


@dataclass(frozen=True, slots=True)
class Position:
    """An open long position valued at its latest mark.

    Attributes:
        pair: The pair held. Quantity is in ``pair.base``; cost and marks
            are in ``pair.quote``.
        quantity: Base-asset quantity held. Always > 0; a position that
            reaches zero is dropped from the snapshot, not kept.
        cost_basis: Total quote amount paid for ``quantity``, fees excluded
            (fees go to cash). Always >= 0.
        mark_price: Latest price used to value the position (a fill price or
            a bar close). Always > 0.
        marked_at: UTC-aware time of ``mark_price``.

    Raises:
        ValueError: On non-positive quantity or mark, negative cost, or a
            naive ``marked_at``.
    """

    pair: Pair
    quantity: Decimal
    cost_basis: Decimal
    mark_price: Decimal
    marked_at: datetime

    def __post_init__(self) -> None:
        """Validate the position invariants.

        Raises:
            ValueError: On ``quantity <= 0``, ``mark_price <= 0``,
                ``cost_basis < 0`` or a naive ``marked_at``.
        """
        if self.quantity <= Decimal("0"):
            raise ValueError(f"Position.quantity must be > 0, got {self.quantity}")
        if self.mark_price <= Decimal("0"):
            raise ValueError(f"Position.mark_price must be > 0, got {self.mark_price}")
        if self.cost_basis < Decimal("0"):
            raise ValueError(f"Position.cost_basis must be >= 0, got {self.cost_basis}")
        if self.marked_at.tzinfo is None:
            raise ValueError("Position.marked_at must be UTC-aware")

    @property
    def market_value(self) -> Decimal:
        """Quantity valued at the latest mark, in the quote currency."""
        with localcontext(EXACT_CONTEXT):
            return self.quantity * self.mark_price

    @property
    def unrealized_pnl(self) -> Decimal:
        """Market value minus cost basis, in the quote currency."""
        with localcontext(EXACT_CONTEXT):
            return self.market_value - self.cost_basis
