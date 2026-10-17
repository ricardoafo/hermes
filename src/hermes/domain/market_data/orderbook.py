"""Order-book snapshot value object — top-of-book depth for slippage modeling.

An :class:`Orderbook` is a point-in-time depth snapshot: two lists of
:class:`OrderbookLevel` (bids descending by price, asks ascending) for a
:class:`~hermes.domain.value_objects.pair.Pair`. Used by the backtest
harness for spread + slippage modeling; the shape is deliberately narrow
(price + quantity per level) — no aggregated-order-count or timestamps
per level, because backtests only replay what the wire returned.

Domain purity: this module imports only value_objects and stdlib. No
pydantic, no httpx (enforced by import-linter contracts).

Reference: code-conventions.md §1 (Value Object) and §9 (UTC internally).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from hermes.domain.value_objects.pair import Pair

__all__ = ["Orderbook", "OrderbookLevel"]


@dataclass(frozen=True, slots=True)
class OrderbookLevel:
    """One resting-order aggregate at a specific price on one side of the book.

    Attributes:
        price: The price level in quote currency. Must be > 0 — a resting
            order at zero or negative price cannot appear in a real book.
        quantity: The aggregate base-asset quantity resting at ``price``.
            Must be > 0 — a level with zero size would be a book delete,
            never a snapshot row.
    """

    price: Decimal
    quantity: Decimal

    def __post_init__(self) -> None:
        """Reject non-positive price or quantity — both would be nonsensical.

        Raises:
            ValueError: On ``price <= 0`` or ``quantity <= 0``.
        """
        if self.price <= Decimal("0"):
            raise ValueError(f"OrderbookLevel.price must be > 0, got {self.price}")
        if self.quantity <= Decimal("0"):
            raise ValueError(f"OrderbookLevel.quantity must be > 0, got {self.quantity}")


@dataclass(frozen=True, slots=True)
class Orderbook:
    """Immutable depth snapshot for one pair at one point in time.

    Attributes:
        pair: The trading pair the snapshot belongs to.
        bids: Bid levels sorted DESCENDING by price (best bid first).
        asks: Ask levels sorted ASCENDING by price (best ask first).
        snapshot_time: UTC-aware :class:`~datetime.datetime` when the
            snapshot was captured (adapter-side clock — the venue's server
            time when the response arrived).
        last_update_id: The venue's book sequence number at the snapshot
            (Binance ``lastUpdateId``), or ``None`` when the source has
            none (a replayed or modelled book). A depth-diff stream needs
            it to drop the diffs the snapshot already contains.
    """

    pair: Pair
    bids: tuple[OrderbookLevel, ...]
    asks: tuple[OrderbookLevel, ...]
    snapshot_time: datetime
    last_update_id: int | None = None

    def __post_init__(self) -> None:
        """Enforce UTC-aware timestamp and correct bid/ask ordering.

        Raises:
            ValueError: On naive / non-UTC ``snapshot_time``, empty book
                (both sides empty is not a valid snapshot), bids not
                descending, asks not ascending, or crossed book
                (``best_bid >= best_ask``), or a negative
                ``last_update_id``.
        """
        if self.last_update_id is not None and self.last_update_id < 0:
            raise ValueError(
                f"Orderbook.last_update_id must be >= 0, got {self.last_update_id} "
                f"(pair={self.pair})"
            )
        if self.snapshot_time.tzinfo is None or self.snapshot_time.tzinfo.utcoffset(
            None
        ) != UTC.utcoffset(None):
            raise ValueError(
                f"Orderbook.snapshot_time must be UTC-aware, got {self.snapshot_time!r}"
            )
        if not self.bids and not self.asks:
            raise ValueError(
                f"Orderbook must have at least one bid or ask level (pair={self.pair})"
            )
        for prev, curr in zip(self.bids, self.bids[1:], strict=False):
            if curr.price >= prev.price:
                raise ValueError(
                    "Orderbook.bids must be strictly descending by price, "
                    f"got {prev.price} then {curr.price} (pair={self.pair})"
                )
        for prev, curr in zip(self.asks, self.asks[1:], strict=False):
            if curr.price <= prev.price:
                raise ValueError(
                    "Orderbook.asks must be strictly ascending by price, "
                    f"got {prev.price} then {curr.price} (pair={self.pair})"
                )
        if self.bids and self.asks and self.bids[0].price >= self.asks[0].price:
            raise ValueError(
                "Orderbook is crossed: best bid "
                f"{self.bids[0].price} >= best ask {self.asks[0].price} (pair={self.pair})"
            )
