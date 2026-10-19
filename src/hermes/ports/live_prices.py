"""LiveTickerPort — the live public price the operator console shows between bars.

The engine acts only on closed bars. Between them, the console shows the
operator a live public price per coin (design spec "Live between bars", issue
#713). This port is the one call it needs: the current ticker of a pair.
``KrakenPublicAdapter.get_ticker`` satisfies it as it is; tests pass a fake.

Display only. Live prices exist in the console process alone: they are never
written to the ledger, never move a stop or an exit, and never reach the
supervisor, briefs, notifications or any AI. Import-linter contract
``live-prices-are-console-only`` keeps engine, supervisor and notification code
from importing this module.

Reference: openspec/archive/713-live-ticker/change.md (D-1, D-6).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from typing import Protocol, runtime_checkable

from hermes.domain.market_data.kline import Kline, KlineInterval
from hermes.domain.market_data.orderbook import Orderbook
from hermes.domain.market_data.ticker import Ticker
from hermes.domain.value_objects.pair import Pair

__all__ = ["LiveBarsPort", "LiveBookPort", "LiveTickerPort"]


@runtime_checkable
class LiveTickerPort(Protocol):
    """Read-only current ticker of a pair, for the console's display."""

    def get_ticker(self, pair: Pair) -> Ticker:
        """Return the current best bid, best ask and last trade of ``pair``.

        Blocking: the console calls it from a worker thread, never the UI thread.

        Raises:
            Exception: Any failure; the caller turns it into a failed tick.
        """
        ...


@runtime_checkable
class LiveBarsPort(Protocol):
    """Read-only candles of a pair, for the console's forming bar and 24 h context.

    ``KrakenPublicAdapter.fetch_klines`` satisfies it as it is (issue #1289).
    """

    def fetch_klines(
        self,
        *,
        pair: Pair,
        interval: KlineInterval,
        since: datetime,
        until: datetime,
    ) -> Iterator[Kline]:
        """Yield the candles of ``pair`` with an open time in ``[since, until)``.

        Blocking: the console calls it from a worker thread, never the UI thread.
        The newest candle may still be forming.

        Raises:
            Exception: Any failure; the caller turns it into a failed outcome.
        """
        ...


@runtime_checkable
class LiveBookPort(Protocol):
    """Read-only order book of a pair, for the console's book health and latency.

    ``KrakenPublicAdapter.get_orderbook`` satisfies it as it is (issue #1292).
    """

    def get_orderbook(self, pair: Pair, *, depth: int = 10) -> Orderbook:
        """Return the top ``depth`` levels per side of ``pair``'s book.

        Blocking: the console calls it from a worker thread, never the UI thread.

        Raises:
            Exception: Any failure; the caller turns it into a failed outcome.
        """
        ...
