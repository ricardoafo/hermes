"""DataSplitEnforcer — OOS read-refusal wrapper for MarketDataPort.

Wraps any :class:`~hermes.ports.market_data.MarketDataPort`-compatible object and
intercepts every bar/trade/orderbook-diff yielded by the underlying port. If the
item's timestamp falls inside the OOS test window and ``certification_mode``
context variable is ``False``, raises :class:`~hermes.domain.errors.OOSDataLeakageError`.

This is the code-enforced read refusal mandated by
``docs/experiments/swing-mvp1/pre-registration-v1.md`` §4 discipline gate:

    "OOS Test: Touched EXACTLY ONCE at certification. Code physically refuses to
    read this window outside certification mode."

Usage::

    from hermes.application.strategy_protocol.data_split_enforcer import (
        DataSplitEnforcer,
        certification_context,
    )

    enforcer = DataSplitEnforcer(inner_port, pre_reg)

    # Normal mode — OOS reads raise OOSDataLeakageError:
    async for bar in enforcer.stream_bars(pair, interval):
        process(bar)  # bars in OOS window are blocked

    # Certification mode — OOS reads are permitted:
    with certification_context():
        async for bar in enforcer.stream_bars(pair, interval):
            certify(bar)

Reference:
  pre-registration-v1.md §4.
  spec-9-strategy-development-protocol change.md D-2, D-3.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from typing import TYPE_CHECKING

from hermes.domain.errors import OOSDataLeakageError
from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.market_data.orderbook import Orderbook
from hermes.domain.market_data.orderbook_diff import OrderbookDiff
from hermes.domain.market_data.trade import Trade
from hermes.domain.value_objects.pair import Pair

if TYPE_CHECKING:
    from hermes.application.strategy_protocol.preregistration import (
        DataWindows,
        PreRegistration,
    )

__all__ = [
    "DataSplitEnforcer",
    "certification_context",
]

# ---------------------------------------------------------------------------
# Context variable for certification mode
# ---------------------------------------------------------------------------

_certification_mode: ContextVar[bool] = ContextVar("hermes_certification_mode", default=False)


@contextmanager
def certification_context() -> Iterator[None]:
    """Context manager that enables OOS data access for certification.

    Sets the ``hermes_certification_mode`` context variable to ``True`` for the
    duration of the ``with`` block. All OOS read-refusal checks by
    :class:`DataSplitEnforcer` are suspended inside this block.

    This context manager MUST only be used by the certification harness.
    Any other use constitutes a pre-registration deviation.

    Example::

        with certification_context():
            async for bar in enforcer.stream_bars(pair, interval):
                certify(bar)

    Reference: pre-registration-v1.md §4, spec-9 change.md D-2.
    """
    token = _certification_mode.set(True)
    try:
        yield
    finally:
        _certification_mode.reset(token)


def is_certification_mode() -> bool:
    """Return True if currently inside a :func:`certification_context` block.

    Returns:
        The current value of the ``hermes_certification_mode`` context variable.
    """
    return _certification_mode.get()


# ---------------------------------------------------------------------------
# Enforcer
# ---------------------------------------------------------------------------


class DataSplitEnforcer:
    """Transparent wrapper around any ``MarketDataPort`` that enforces OOS isolation.

    Delegates all method calls to the wrapped ``inner`` port after checking that the
    items' timestamps do not fall inside the OOS test window when outside
    ``certification_context()``.

    The enforcer checks timestamps of every :class:`~hermes.domain.market_data.bar.Bar`,
    :class:`~hermes.domain.market_data.trade.Trade`, and
    :class:`~hermes.domain.market_data.orderbook_diff.OrderbookDiff` yielded by the
    inner port before forwarding them to the caller.

    Args:
        inner: Any object satisfying the ``MarketDataPort`` Protocol.
        pre_reg: :class:`~hermes.application.strategy_protocol.preregistration.PreRegistration`
            instance providing the OOS window boundaries.

    Reference:
      spec-9-strategy-development-protocol change.md D-3.
    """

    def __init__(self, inner: object, pre_reg: PreRegistration) -> None:
        """Initialise the enforcer.

        Args:
            inner: Inner ``MarketDataPort`` to wrap.
            pre_reg: Pre-registration instance providing data window constants.
        """
        self._inner = inner
        self._windows: DataWindows = pre_reg.data_windows()

    # ------------------------------------------------------------------
    # Private timestamp guard
    # ------------------------------------------------------------------

    def _guard_timestamp(self, ts: datetime) -> None:
        """Raise if ``ts`` is in the OOS window and not in certification mode.

        Args:
            ts: Timestamp extracted from a bar, trade, or orderbook diff.

        Raises:
            OOSDataLeakageError: If the timestamp is in the OOS window and
                ``certification_mode`` is False.
        """
        if _certification_mode.get():
            return
        if self._windows.is_in_oos(ts):
            raise OOSDataLeakageError(
                attempted_timestamp=ts.isoformat(),
                oos_start=self._windows.oos_start.isoformat(),
            )

    # ------------------------------------------------------------------
    # MarketDataPort delegation with enforcement
    # ------------------------------------------------------------------

    async def stream_bars(self, pair: Pair, interval: KlineInterval) -> AsyncIterator[Bar]:
        """Yield bars from the inner port, blocking OOS bars outside certification.

        Args:
            pair: Trading pair to subscribe to.
            interval: Candle-duration bucket.

        Yields:
            :class:`~hermes.domain.market_data.bar.Bar` instances that pass the
            OOS enforcement check.

        Raises:
            OOSDataLeakageError: If a yielded bar's ``open_time`` is in the OOS
                window and certification mode is inactive.
            hermes.domain.errors.InfraError: Propagated from the inner port on
                adapter-level failures.
        """
        async for bar in self._inner.stream_bars(pair, interval):  # type: ignore[attr-defined]
            self._guard_timestamp(bar.open_time)
            yield bar

    async def stream_trades(self, pair: Pair) -> AsyncIterator[Trade]:
        """Yield trades from the inner port, blocking OOS trades outside certification.

        Args:
            pair: Trading pair to subscribe to.

        Yields:
            :class:`~hermes.domain.market_data.trade.Trade` instances that pass the
            OOS enforcement check.

        Raises:
            OOSDataLeakageError: If a trade's timestamp is in the OOS window and
                certification mode is inactive.
            hermes.domain.errors.InfraError: Propagated from the inner port.
        """
        async for trade in self._inner.stream_trades(pair):  # type: ignore[attr-defined]
            self._guard_timestamp(trade.trade_time)
            yield trade

    async def stream_orderbook_diffs(self, pair: Pair) -> AsyncIterator[OrderbookDiff]:
        """Yield orderbook diffs from the inner port (no timestamp check).

        :class:`~hermes.domain.market_data.orderbook_diff.OrderbookDiff` carries
        only sequence numbers, not timestamps. OOS enforcement is not applied
        to diffs — this stream is used for live L2 book maintenance only, and
        the OOS boundary is enforced at the bar/trade level where timestamps exist.

        Args:
            pair: Trading pair to subscribe to.

        Yields:
            :class:`~hermes.domain.market_data.orderbook_diff.OrderbookDiff`
            instances from the inner port.

        Raises:
            hermes.domain.errors.InfraError: Propagated from the inner port.
        """
        async for diff in self._inner.stream_orderbook_diffs(pair):  # type: ignore[attr-defined]
            yield diff

    async def get_snapshot(self, pair: Pair) -> Orderbook:
        """Return a snapshot from the inner port (no OOS check — snapshot is stateless).

        Order book snapshots do not carry a timestamp in the current ``Orderbook``
        schema, so no OOS check is applied. The method delegates transparently.

        Args:
            pair: Trading pair to snapshot.

        Returns:
            :class:`~hermes.domain.market_data.orderbook.Orderbook` from the inner port.

        Raises:
            hermes.domain.errors.InfraError: Propagated from the inner port.
        """
        result: Orderbook = await self._inner.get_snapshot(pair)  # type: ignore[attr-defined]
        return result
