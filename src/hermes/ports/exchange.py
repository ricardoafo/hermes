"""ExchangePort — abstract Protocol for exchange adapters.

Every exchange adapter (Binance, Kraken, or a simulated adapter for paper
trading) must implement this Protocol. Concrete implementations live in
``hermes.adapters.exchanges``.

The interface covers:

- **Order placement**: :meth:`place_market_order`, :meth:`place_limit_order`
- **Order management**: :meth:`cancel_order`
- **Market data reads**: :meth:`get_orderbook`
- **Account reads**: :meth:`get_balance`, :meth:`get_fees`

Real-money guards (invariant I-08 from ADR-003 §00) are the composition
root's responsibility, not this port's. The adapter knows whether it is
connected to a testnet or a mainnet account; the port only defines the
contract shape. The operator configures the mode at startup; the port
never inspects a ``dry_run`` flag.

:class:`ExchangePort` is ``@runtime_checkable`` so conformance tests can
use ``isinstance(adapter, ExchangePort)`` to verify structural satisfaction
without importing the concrete class. This also makes the port testable in
isolation via minimal stub implementations.

Reference: spec-2a-fill-model-port/change.md (D-3 — read + write in one
port, safety at composition root), ADR-003 §00 I-08 (real-money guard),
ADR-004 (exchange-adapter choice).

Closes: #199 (empty ExchangePort stub with no methods).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from hermes.domain.fees import FeeSchedule
from hermes.domain.market_data.orderbook import Orderbook
from hermes.domain.trading.fill import Fill, Rejection
from hermes.domain.trading.order import Order, OrderSide
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.quantity import Quantity

__all__ = ["ExchangePort"]


@runtime_checkable
class ExchangePort(Protocol):
    """Contract every exchange adapter must satisfy.

    Adapters SHOULD:

    - Enforce real-money guards internally (verify the account is on the
      correct network — testnet vs mainnet — before placing any order).
    - Handle rate limiting internally (token bucket or equivalent) so
      callers never need to sleep.
    - Map exchange-native error responses to
      :class:`~hermes.domain.errors.InfraError` (or an exchange-specific
      subclass) at the port boundary, so callers in the application layer
      convert to ``Result`` per code-conventions §2.
    - Convert wire-format numeric fields to :class:`~decimal.Decimal` at
      the port boundary so callers never see float precision loss.

    Adapters MUST raise :class:`~hermes.domain.errors.InfraError` (or a
    subclass — typically a venue-specific error like ``BinanceError`` or
    ``KrakenPublicError``) on any wire, HTTP-status, or schema failure.

    The :class:`~hermes.domain.trading.fill.Rejection` return type on
    :meth:`place_limit_order` is an expected business outcome (the limit was
    not reachable given current market conditions), NOT an error — it does
    NOT go through the ``InfraError`` raise path.
    """

    def place_market_order(
        self,
        *,
        side: OrderSide,
        order: Order,
    ) -> Fill:
        """Submit a market order and return the exchange-confirmed fill.

        A market order executes immediately at the best available price.
        The exchange is responsible for price discovery; the returned
        :class:`~hermes.domain.trading.fill.Fill` reflects the actual
        executed quantity and volume-weighted average price, which may
        differ from the orderbook snapshot the strategy used to decide.

        Args:
            side: Direction of the order (:attr:`~hermes.domain.trading.order.OrderSide.BUY`
                or :attr:`~hermes.domain.trading.order.OrderSide.SELL`).
            order: The original order request. The adapter uses
                ``order.pair`` and ``order.qty``; ``order.limit_price``
                is ignored for market orders.

        Returns:
            A :class:`~hermes.domain.trading.fill.Fill` carrying the
            exchange-confirmed execution details (quantity, average price,
            fee, timestamp).

        Raises:
            hermes.domain.errors.InfraError: On any exchange or transport
                failure (non-2xx response, network error, malformed payload).
        """
        ...

    def place_limit_order(
        self,
        *,
        order: Order,
    ) -> Fill | Rejection:
        """Submit a limit order and return the result synchronously.

        A limit order executes only at ``order.limit_price`` or better.
        For MVP1 the adapter may implement IOC (immediate-or-cancel)
        semantics — the order is cancelled if it cannot fill immediately
        — and return :class:`~hermes.domain.trading.fill.Rejection` when
        the book does not have sufficient liquidity at the limit. GTC
        (good-till-cancelled) semantics are deferred to a later milestone.

        Args:
            order: The order request. ``order.limit_price`` MUST be set
                (non-``None``) — calling this method with a market order
                (``limit_price is None``) is a programming error and
                adapters MAY raise :class:`ValueError` in that case.

        Returns:
            :class:`~hermes.domain.trading.fill.Fill` when the limit order
            fills (fully or partially). :class:`~hermes.domain.trading.fill.Rejection`
            when the order cannot fill at the specified limit — the limit
            price was not reachable given current book depth.

        Raises:
            hermes.domain.errors.InfraError: On any exchange or transport
                failure.
            ValueError: When ``order.limit_price is None`` — callers must
                provide a limit price.
        """
        ...

    def cancel_order(self, *, order_id: str) -> None:
        """Cancel a resting order by its exchange-assigned identifier.

        Idempotent: cancelling an order that has already filled or been
        cancelled is NOT an error — the adapter silently succeeds. This
        allows callers to cancel without checking the order state first
        (read-then-cancel races on concurrent fills).

        Args:
            order_id: The exchange-assigned order identifier returned by
                :meth:`place_limit_order` (inside the ``Fill``). The
                identifier format is venue-specific (string representation
                so the port is not coupled to any exchange's integer vs UUID
                choice).

        Raises:
            hermes.domain.errors.InfraError: On any transport or exchange
                failure that prevents the cancel from completing.
        """
        ...

    def get_orderbook(self, *, pair: Pair) -> Orderbook:
        """Fetch the current depth snapshot for ``pair``.

        The adapter normalises the response to a
        :class:`~hermes.domain.market_data.orderbook.Orderbook` with bids
        descending and asks ascending (the canonical domain ordering). Level
        count is implementation-defined — adapters should request enough
        depth to cover the expected fill-model walk depth without over-fetching.

        Args:
            pair: Trading pair to query.

        Returns:
            A :class:`~hermes.domain.market_data.orderbook.Orderbook`
            snapshot with ``snapshot_time`` set to the UTC time the adapter
            received the response.

        Raises:
            hermes.domain.errors.InfraError: On any transport or exchange
                failure.
        """
        ...

    def get_balance(self, *, currency: Currency) -> Quantity:
        """Return the available balance for ``currency`` in the exchange account.

        "Available" means free (not reserved in open orders). The adapter
        converts the exchange's native balance representation to
        :class:`~hermes.domain.value_objects.quantity.Quantity`.

        Args:
            currency: The asset to query.

        Returns:
            :class:`~hermes.domain.value_objects.quantity.Quantity` with
            ``asset == currency`` and ``amount`` equal to the free balance.
            Returns a zero :class:`~hermes.domain.value_objects.quantity.Quantity`
            when the currency is not held (not an error — zero balance is a
            valid state).

        Raises:
            hermes.domain.errors.InfraError: On any transport or exchange
                failure.
        """
        ...

    def get_fees(self, *, pair: Pair) -> FeeSchedule:
        """Return the volume-tiered fee schedule for ``pair``.

        Adapters MAY cache the fee schedule with a TTL appropriate to how
        often the venue changes fees (typically hours-to-days). The port
        does not mandate a specific TTL — callers should not assume the
        result is real-time.

        Args:
            pair: Trading pair to look up.

        Returns:
            A :class:`~hermes.domain.fees.FeeSchedule` whose ``tiers`` are
            normalised ascending by ``volume_threshold_usd``. The standard
            (zero-volume) tier is always ``tiers[0]``.

        Raises:
            hermes.domain.errors.InfraError: On any transport or exchange
                failure.
        """
        ...
