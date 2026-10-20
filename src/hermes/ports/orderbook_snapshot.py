"""OrderbookSnapshotPort — one current order-book snapshot on demand.

The shadow loop walks a live book only when it executes a signal, a few times a
week, so it asks for one snapshot at that moment instead of keeping a book up to
date from a diff stream. A snapshot is market data only: an adapter behind this
port reads a public endpoint and holds no key.

Reference: openspec/archive/353-fill-kernel-in-serve/change.md D-1.
"""

from typing import Protocol, runtime_checkable

from hermes.domain.market_data.orderbook import Orderbook
from hermes.domain.value_objects.pair import Pair

__all__ = ["OrderbookSnapshotPort"]


@runtime_checkable
class OrderbookSnapshotPort(Protocol):
    """Source of current depth snapshots.

    Adapters raise :class:`~hermes.domain.errors.InfraError` (or a subclass)
    on any transport, HTTP or schema failure; the caller falls back.
    """

    async def snapshot(self, pair: Pair) -> Orderbook:
        """Return the current book of ``pair``, stamped when it was received.

        Args:
            pair: Trading pair.

        Returns:
            The depth snapshot.

        Raises:
            hermes.domain.errors.InfraError: On any failure.
        """
        ...

    async def aclose(self) -> None:
        """Release the adapter's resources; safe to call twice."""
        ...
