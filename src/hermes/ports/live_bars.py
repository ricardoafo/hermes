"""Live bar store port — append-only record of every closed live bar.

The serve loop records each bar it handles; the archive check (#359) and the
nightly parity replay (#358) read them back. Nothing in the strategy reads
this store. The concrete implementation is
:class:`hermes.adapters.ledger.sqlite_ledger.SqliteLedger`.

Reference: code-conventions.md §5 (Ports as Protocols), GitHub issue #421.
"""

from datetime import datetime
from typing import Protocol, runtime_checkable

from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.value_objects.pair import Pair

__all__ = ["LiveBarStorePort"]


@runtime_checkable
class LiveBarStorePort(Protocol):
    """Contract for the live bar store.

    Adapters MUST raise :class:`hermes.domain.errors.InfraError` (or a
    subclass) on any storage failure, and MUST NOT offer update or delete.
    """

    def record_live_bar(self, bar: Bar) -> Bar | None:
        """Store ``bar`` unless its (pair, interval, open_time) is already stored.

        Returns:
            ``None`` when the bar was stored or an identical bar was already
            there; the previously stored bar when it differs from ``bar``
            (the stored one is kept).
        """
        ...

    def live_bars(
        self, pair: Pair, interval: KlineInterval, start: datetime, end: datetime
    ) -> tuple[Bar, ...]:
        """Return stored bars with ``start <= open_time < end``, oldest first."""
        ...
