"""FillModelPort — abstract Protocol for all fill implementations.

The FillModelPort is the central architectural abstraction that enforces
the same-code-path invariant: backtest, shadow, and live modes ALL call
:meth:`fill_order` with the same signature. The only thing that differs
across modes is WHAT is wired at the composition root:

- **Backtest**: synthetic :class:`~hermes.domain.market_data.orderbook.Orderbook`
  reconstructed from historical snapshots, a deterministic clock, no real
  capital.
- **Shadow**: live :class:`~hermes.domain.market_data.orderbook.Orderbook`
  from the exchange, wall-clock time, no real capital (fills are simulated
  against the live book without placing orders).
- **Live**: live :class:`~hermes.domain.market_data.orderbook.Orderbook`
  from the exchange, wall-clock time, real capital (the implementation calls
  :meth:`~hermes.ports.exchange.ExchangePort.place_market_order` through the
  exchange adapter, then reads back the actual fill through this same return
  type).

In all three cases the application-layer code is IDENTICAL — the port
guarantees that there is no ``if dry_run:`` branch anywhere in the
strategy or application layer. This avoids the divergence failure mode
described in the paper-trading engine research, where separate code
paths in backtest vs live accumulate subtle semantic differences over
time and destroy the reliability of simulation results.

:class:`FillModelPort` is decorated ``@runtime_checkable`` so that
conformance tests can use ``isinstance(impl, FillModelPort)`` to verify
structural satisfaction without importing the concrete class.

Reference: spec-2a-fill-model-port/change.md (D-1, D-2), ADR-003 §00
invariant I-08 (real-money guard at composition root, not in the port).
"""

from datetime import datetime
from typing import Protocol, runtime_checkable

from hermes.domain.fees import FeeSchedule
from hermes.domain.market_data.orderbook import Orderbook
from hermes.domain.trading.fill import Fill, Rejection
from hermes.domain.trading.order import Order

__all__ = ["FillModelPort"]


@runtime_checkable
class FillModelPort(Protocol):
    """Contract every fill implementation must satisfy.

    A fill implementation is a class that knows HOW to turn an order
    request into a fill result given a market snapshot, a clock, and a
    fee schedule. The three concrete implementations planned for MVP1 are:

    - ``OrderbookWalkFillModel`` (Spec 2b) — backtest + shadow mode; walks
      the orderbook levels in price order to compute a realistic fill with
      spread and slippage.
    - ``LiveExchangeFillModel`` (Spec 2c) — live mode; delegates to
      :class:`~hermes.ports.exchange.ExchangePort`, reads back the actual
      exchange-confirmed fill, and wraps it in a :class:`~hermes.domain.trading.fill.Fill`.

    Both implement exactly this interface — the application layer never
    sees which one is wired.

    Adapters MUST raise :class:`~hermes.domain.errors.InfraError` on any
    infrastructure failure (exchange unreachable, wire-level error). Only
    expected business outcomes (insufficient liquidity, limit not reachable)
    are returned as :class:`~hermes.domain.trading.fill.Rejection`.
    """

    def fill_order(
        self,
        order: Order,
        orderbook: Orderbook,
        clock: datetime,
        fees: FeeSchedule,
    ) -> Fill | Rejection:
        """Attempt to fill ``order`` against the provided market snapshot.

        This is the ONE method that all fill implementations (backtest,
        shadow, live) implement identically. The composition root decides
        which :class:`~hermes.domain.market_data.orderbook.Orderbook` source
        feeds this call; the fill model itself is mode-agnostic.

        Args:
            order: The order request — side, pair, quantity, optional
                limit price.
            orderbook: Depth snapshot for ``order.pair`` at the moment of
                the fill attempt. In backtest: reconstructed from historical
                data. In shadow/live: fetched from the exchange. The fill
                model uses this to simulate or verify the execution price.
            clock: UTC-aware timestamp representing "now" from the
                composition root's perspective. In backtest: the timestamp
                of the current historical bar. In shadow/live: wall-clock
                time. Naive datetimes are implementation-defined violations
                — callers MUST provide a UTC-aware value.
            fees: Fee schedule for ``order.pair``. The fill model applies
                the appropriate maker or taker rate to compute
                :attr:`~hermes.domain.trading.fill.Fill.fee`.

        Returns:
            :class:`~hermes.domain.trading.fill.Fill` on a successful fill
            (full or partial), or
            :class:`~hermes.domain.trading.fill.Rejection` when the order
            cannot be filled under current market conditions. Both are
            expected business outcomes — neither goes through the ``Result``
            error channel.

        Raises:
            hermes.domain.errors.InfraError: On any infrastructure failure
                (exchange unreachable, wire-level error). Only raised by
                live implementations that delegate to an exchange adapter;
                backtest / simulation implementations never raise it.
        """
        ...
