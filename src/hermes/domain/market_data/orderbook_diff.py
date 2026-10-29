"""OrderbookDiff value object — L2 order-book update event.

An :class:`OrderbookDiff` carries the incremental update to a local order-book
state as emitted by an exchange WebSocket depth-update stream (e.g. Binance
``depthUpdate``, Kraken ``book`` subscription). The update is applied on top
of an :class:`~hermes.domain.market_data.orderbook.Orderbook` REST snapshot
retrieved at stream-bootstrap time.

Each updated price level is represented as an :class:`OrderbookDiffLevel`:

- ``delete=False``: insert or update the local book at ``price`` with the new
  ``quantity``.
- ``delete=True``: remove the price level from the local book entirely.
  The wire convention for many exchanges sends ``quantity=0`` to signal deletion;
  adapters MUST set ``delete=True`` and preserve the price so the application
  layer knows which level to remove.

Sequence numbers (``first_update_id``, ``final_update_id``) allow callers to
detect missed events and trigger a re-snapshot. The gap-detection algorithm is
an adapter concern (deferred to spec-4c); the invariant ``final >= first``
is enforced here.

Domain purity: this module imports only domain value objects and stdlib. No
pydantic, no httpx (enforced by import-linter contracts).

Reference: code-conventions.md §1 (Value Object) and §9 (UTC internally).
spec-4b-market-data-port change.md §Design and D-5.
"""

from dataclasses import dataclass
from decimal import Decimal

from hermes.domain.value_objects.pair import Pair

__all__ = ["OrderbookDiff", "OrderbookDiffLevel"]


@dataclass(frozen=True, slots=True)
class OrderbookDiffLevel:
    """One price-level update within an :class:`OrderbookDiff` event.

    Attributes:
        price: The price level being updated or removed. Must be > 0 — a resting
            order at zero or negative price cannot appear in a real book.
        quantity: The new aggregate quantity at this price level. For
            ``delete=False`` (update/insert) this is the new resting quantity,
            which must be >= 0. For ``delete=True`` (level removal) the quantity
            carried on the wire is typically 0; adapters should preserve it as-is
            for auditability. Must be >= 0.
        delete: True when this event removes the price level from the local book;
            False when it inserts or updates the level.

    Reference: spec-4b-market-data-port change.md D-5.
    """

    price: Decimal
    quantity: Decimal
    delete: bool

    def __post_init__(self) -> None:
        """Reject non-positive price or negative quantity.

        Raises:
            ValueError: On ``price <= 0`` or ``quantity < 0``.
        """
        if self.price <= Decimal("0"):
            raise ValueError(f"OrderbookDiffLevel.price must be > 0, got {self.price}")
        if self.quantity < Decimal("0"):
            raise ValueError(f"OrderbookDiffLevel.quantity must be >= 0, got {self.quantity}")


@dataclass(frozen=True, slots=True)
class OrderbookDiff:
    """Immutable L2 depth-update event for one pair at one sequence interval.

    Attributes:
        pair: The trading pair the update belongs to.
        bids: Bid-side level updates. Each entry is a price/quantity/delete
            tuple. May be empty when only the ask side changed.
        asks: Ask-side level updates. Each entry is a price/quantity/delete
            tuple. May be empty when only the bid side changed.
        first_update_id: Exchange sequence number of the first update in this
            event (Binance ``U`` field; Kraken equivalent for the first seq
            number in the batch). Callers use this to detect gaps vs. the
            previous event's ``final_update_id + 1``.
        final_update_id: Exchange sequence number of the last update in this
            event (Binance ``u`` field). Must be >= ``first_update_id``.

    Reference: spec-4b-market-data-port change.md D-5.
    """

    pair: Pair
    bids: tuple[OrderbookDiffLevel, ...]
    asks: tuple[OrderbookDiffLevel, ...]
    first_update_id: int
    final_update_id: int

    def __post_init__(self) -> None:
        """Enforce sequence number ordering.

        Raises:
            ValueError: On ``final_update_id < first_update_id``.
        """
        if self.final_update_id < self.first_update_id:
            raise ValueError(
                f"OrderbookDiff.final_update_id ({self.final_update_id}) must be >= "
                f"first_update_id ({self.first_update_id})"
            )
