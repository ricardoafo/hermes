"""MarketDataPort — abstract Protocol for live market-data stream adapters.

Defines the contract every live market-data adapter must satisfy to participate
in the swing-MVP1 paper-trading system. The port covers three streaming
channels and one REST-bootstrap call:

- :meth:`MarketDataPort.stream_bars` — closed-bar consolidated stream
- :meth:`MarketDataPort.stream_trades` — tick-by-tick trade stream
- :meth:`MarketDataPort.stream_orderbook_diffs` — L2 incremental depth-update stream
- :meth:`MarketDataPort.get_snapshot` — REST snapshot for L2 book bootstrap

Concrete implementations live under :mod:`hermes.adapters.exchanges` (spec-4c).
This module is a pure port: no I/O, no adapter code, no external dependencies.

Design decisions:

- ``@runtime_checkable`` Protocol (not ABC) — consistent with ``KlinesFetcherPort``
  and ``FeeScheduleProviderPort``; allows ``isinstance`` injection guards without
  requiring adapters to inherit from hermes types.
- All three stream methods return ``AsyncIterator[T]`` — the canonical Python
  streaming type (PEP 525); adapters may implement with async generators or
  any async-iterable object.
- ``get_snapshot`` is async — REST bootstrap requires an HTTP round-trip; all
  hermes adapters use async I/O (anyio-on-asyncio policy).

Reference: code-conventions.md §5 (Ports as Protocols).
spec-4b-market-data-port change.md D-1 through D-6.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.market_data.orderbook import Orderbook
from hermes.domain.market_data.orderbook_diff import OrderbookDiff
from hermes.domain.market_data.trade import Trade
from hermes.domain.value_objects.pair import Pair

__all__ = ["MarketDataPort"]


@runtime_checkable
class MarketDataPort(Protocol):
    """Contract every live market-data adapter satisfies.

    Adapters SHOULD:

    - manage WebSocket lifecycle (connect, authenticate if needed, heartbeat,
      reconnect on drop) internally — callers only consume the iterator;
    - honour exchange rate limits and subscription limits internally;
    - convert exchange-native timestamps and numeric precision to UTC
      :class:`datetime` and :class:`~decimal.Decimal` at the port boundary
      so callers never see wire-format leakage;
    - close the underlying connection when the async iterator is exhausted or
      explicitly ``aclose()``-ed by the caller.

    Adapters MUST raise :class:`hermes.domain.errors.InfraError` (or a
    subclass) on any wire, WebSocket, or schema failure. Callers are the
    application layer, which converts to :class:`returns.result.Result` per
    code-conventions §2.

    Reference: spec-4b-market-data-port change.md D-1 through D-6.
    """

    async def stream_bars(self, pair: Pair, interval: KlineInterval) -> AsyncIterator[Bar]:
        """Yield closed bars for ``pair`` at ``interval`` as they complete.

        Each yielded :class:`~hermes.domain.market_data.bar.Bar` represents one
        fully closed candle window. Adapters MUST NOT yield partial (open) bars;
        a bar is emitted exactly once, when the exchange signals its close.

        Args:
            pair: Trading pair to subscribe to.
            interval: Candle-duration bucket (e.g.
                :attr:`~hermes.domain.market_data.kline.KlineInterval.M1` for
                one-minute bars).

        Yields:
            :class:`~hermes.domain.market_data.bar.Bar` instances in
            ascending ``open_time`` order.

        Raises:
            hermes.domain.errors.InfraError: On any adapter-level failure
                (connection drop, parse error, subscription rejection).
        """
        ...  # pragma: no cover

    async def stream_trades(self, pair: Pair) -> AsyncIterator[Trade]:
        """Yield individual trade ticks for ``pair`` in real time.

        Each yielded :class:`~hermes.domain.market_data.trade.Trade` represents
        one matched trade as reported by the exchange. The stream is unbounded —
        it runs until the caller closes the iterator or the connection drops.

        Args:
            pair: Trading pair to subscribe to.

        Yields:
            :class:`~hermes.domain.market_data.trade.Trade` instances roughly
            in ascending ``trade_time`` order (minor reordering possible at
            adapter level due to WebSocket batching).

        Raises:
            hermes.domain.errors.InfraError: On any adapter-level failure.
        """
        ...  # pragma: no cover

    async def stream_orderbook_diffs(self, pair: Pair) -> AsyncIterator[OrderbookDiff]:
        """Yield L2 incremental depth-update events for ``pair``.

        Each yielded :class:`~hermes.domain.market_data.orderbook_diff.OrderbookDiff`
        carries a batch of price-level updates (inserts, replaces, and deletions)
        along with exchange sequence numbers. Callers are responsible for applying
        diffs to a local :class:`~hermes.domain.market_data.orderbook.Orderbook`
        snapshot obtained via :meth:`get_snapshot`. See the L2 bootstrap procedure
        described in spec-4b change.md D-5.

        Args:
            pair: Trading pair to subscribe to.

        Yields:
            :class:`~hermes.domain.market_data.orderbook_diff.OrderbookDiff`
            instances in ascending ``final_update_id`` order.

        Raises:
            hermes.domain.errors.InfraError: On any adapter-level failure.
        """
        ...  # pragma: no cover

    async def get_snapshot(self, pair: Pair) -> Orderbook:
        """Return a REST depth snapshot for ``pair`` to bootstrap L2 state.

        The snapshot is used as the initial local book state before applying
        incremental diffs from :meth:`stream_orderbook_diffs`. Callers SHOULD
        call this once at startup and after any detected sequence-number gap.

        Args:
            pair: Trading pair to snapshot.

        Returns:
            A :class:`~hermes.domain.market_data.orderbook.Orderbook` reflecting
            the exchange's current depth at call time.

        Raises:
            hermes.domain.errors.InfraError: On any adapter-level failure
                (HTTP error, schema mismatch, empty book).
        """
        ...  # pragma: no cover
