"""Archive bars port — reference bars for one stream and UTC day.

The concrete implementation is
:class:`hermes.adapters.market_data.binance_vision_daily.BinanceVisionDailyArchive`.

Reference: code-conventions.md §5 (Ports as Protocols), GitHub issue #359.
"""

from datetime import date
from typing import Protocol, runtime_checkable

from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.value_objects.pair import Pair

__all__ = ["ArchiveBarsPort"]


@runtime_checkable
class ArchiveBarsPort(Protocol):
    """Contract for a source of archived reference bars."""

    def daily_bars(self, pair: Pair, interval: KlineInterval, day: date) -> tuple[Bar, ...] | None:
        """Return the archived bars that open on ``day`` (UTC).

        Returns:
            The bars, or ``None`` when the archive for that day is not
            published yet.

        Raises:
            hermes.domain.errors.InfraError: On a download, checksum or parse failure.
        """
        ...
