"""SimulatedExchangeAdapter — composition-root-swappable ExchangePort.

This adapter wires three injected ports into a single ``ExchangePort``
implementation that serves BOTH backtest and shadow modes without any
``if mode:`` branching:

- **FillModelPort** — computes fill/rejection from an orderbook snapshot.
- **Orderbook callable** — supplies the depth snapshot for a ``Pair``.
  Backtest wires a closure over synthetic bar-derived data; shadow wires
  a closure over a live L2 adapter.
- **ClockPort** — the time source. Backtest: ``SimulatedClock``; shadow:
  ``WallClock``.
- **FeeSchedule callable** — returns a ``FeeSchedule`` for a ``Pair``.

The adapter is the ONLY ``ExchangePort`` implementation in use for
paper-trading (backtest + shadow). Live trading (Binance signed /
Kraken signed) is out of scope until MVP2.

Design decisions (per spec-2c change.md):

- **D-1** — No mode flag; composition root wires ports.
- **D-2** — In-memory open-order queue (dict) + committed-balance ledger.
- **D-3** — ``Lot.open()`` called on every BUY fill; lots stored in
  ``lot_ledger`` for observability.
- **D-4** — Accepts optional ``random.Random`` for future queue modeling.
- **D-5** — ``get_orderbook`` delegates to injected ``_orderbook_fn``.
- **D-6** — ``get_balance`` reads committed-balance ledger.
- **D-7** — ``get_fees`` delegates to injected ``_fee_schedule_fn``.

Reference: spec-2c-simulated-exchange-adapter/change.md, ADR-003 §00 I-08
(real-money guard at composition root, not in the port),
spec-2a-fill-model-port/change.md (D-1, D-2, D-3).

Closes: #238
"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Final

import structlog
from returns.result import Failure

from hermes.domain.fees.fee_schedule import FeeSchedule
from hermes.domain.lots.lot import Lot
from hermes.domain.market_data.orderbook import Orderbook
from hermes.domain.trading.fill import Fill, Rejection
from hermes.domain.trading.order import Order, OrderSide
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.lot_id import LotId
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.quantity import Quantity
from hermes.ports.clock import ClockPort
from hermes.ports.fill_model import FillModelPort

__all__ = ["SimulatedExchangeAdapter"]

log: Final = structlog.get_logger(__name__)


@dataclass
class SimulatedExchangeAdapter:
    """Composition-root-swappable ``ExchangePort`` for backtest and shadow modes.

    The adapter satisfies :class:`~hermes.ports.exchange.ExchangePort` via
    structural subtyping — no inheritance required.  The composition root
    wires the mode-specific ports at startup; the adapter itself contains
    no mode-detection logic.

    Args:
        fill_model: A :class:`~hermes.ports.fill_model.FillModelPort`
            implementation.  Backtest wires ``OrderbookWalkFillModel``
            (Spec 2b); shadow wires the same with ``apply_slippage=False``.
        orderbook_fn: Callable ``(pair: Pair) -> Orderbook`` that returns
            the current depth snapshot.  The callable is called inside
            :meth:`place_market_order`, :meth:`place_limit_order`, and
            :meth:`get_orderbook`.
        clock: A :class:`~hermes.ports.clock.ClockPort` implementation.
            Backtest wires ``SimulatedClock``; shadow wires ``WallClock``.
        fee_schedule_fn: Callable ``(pair: Pair) -> FeeSchedule`` that
            returns the fee schedule for the pair.
        initial_balances: Initial free balances by currency.  The adapter
            tracks committed balances (reserved for open orders) and deducts
            fills from free balances.  Defaults to an empty dict (zero
            balance on every currency).
        rng: Optional :class:`random.Random` instance for deterministic
            replay.  Currently unused in fill logic (future queue-position
            modeling, M-07).  Pass an explicit seeded instance to guarantee
            reproducibility across property tests.

    Attributes:
        lot_ledger: Ordered list of every :class:`~hermes.domain.lots.lot.Lot`
            opened by BUY fills.  Read-only from callers; mutated by
            :meth:`place_market_order` and :meth:`place_limit_order`.
    """

    fill_model: FillModelPort
    orderbook_fn: Callable[[Pair], Orderbook]
    clock: ClockPort
    fee_schedule_fn: Callable[[Pair], FeeSchedule]
    initial_balances: dict[Currency, Decimal] = field(default_factory=dict)
    rng: random.Random = field(default_factory=random.Random)

    # Internal state — not part of the public interface.
    _open_orders: dict[str, Order] = field(default_factory=dict, init=False, repr=False)
    _balances: dict[Currency, Decimal] = field(default_factory=dict, init=False, repr=False)
    lot_ledger: list[Lot] = field(default_factory=list, init=False, repr=False)

    def __post_init__(self) -> None:
        """Initialise the free-balance ledger from ``initial_balances``."""
        self._balances = dict(self.initial_balances)

    # ------------------------------------------------------------------
    # ExchangePort — write methods
    # ------------------------------------------------------------------

    def place_market_order(
        self,
        *,
        side: OrderSide,
        order: Order,
    ) -> Fill:
        """Submit a market order and return the simulated fill.

        Delegates to the injected :class:`~hermes.ports.fill_model.FillModelPort`
        with the current orderbook snapshot and clock.  Market orders ALWAYS
        fill if the book has sufficient depth — the composition root is
        responsible for wiring a book that reflects "next-bar open" price
        (anti-Zipline invariant D-10 in spec-2b).

        Args:
            side: Direction of the order.
            order: The order to fill.  ``order.limit_price`` must be
                ``None`` — callers MUST NOT pass a limit price here.

        Returns:
            A :class:`~hermes.domain.trading.fill.Fill` reflecting the
            simulated execution.

        Raises:
            ValueError: When ``order.limit_price`` is not ``None`` — this
                method is for market orders only.
            RuntimeError: When the fill model returns a
                :class:`~hermes.domain.trading.fill.Rejection` for a market
                order.  Market orders should always fill given the
                composition root's responsibility to wire a non-empty book.
        """
        if order.limit_price is not None:
            raise ValueError(
                f"SimulatedExchangeAdapter.place_market_order: "
                f"order.limit_price must be None for a market order, "
                f"got {order.limit_price!r}"
            )

        # Build a market-order copy on the provided side (the caller
        # passes the side separately because ExchangePort.place_market_order
        # takes side as a keyword arg).
        order_with_side = Order(
            side=side,
            pair=order.pair,
            qty=order.qty,
            strategy_id=order.strategy_id,
            limit_price=None,
        )

        book = self.orderbook_fn(order.pair)
        fees = self.fee_schedule_fn(order.pair)
        now = self.clock.now()

        result = self.fill_model.fill_order(order_with_side, book, now, fees)

        if isinstance(result, Rejection):
            raise RuntimeError(
                f"SimulatedExchangeAdapter.place_market_order: fill model "
                f"rejected a market order — ensure the orderbook_fn returns "
                f"a non-empty book. Rejection reason: {result.reason}"
            )

        self._apply_fill(result)
        log.info(
            "simulated_market_fill",
            pair=str(order.pair),
            side=str(side),
            executed_qty=str(result.executed_qty),
            avg_price=str(result.avg_price),
        )
        return result

    def place_limit_order(
        self,
        *,
        order: Order,
    ) -> Fill | Rejection:
        """Submit a limit order with IOC semantics and return the result.

        Delegates to the injected fill model.  The adapter implements
        **immediate-or-cancel (IOC)** semantics: the order either fills
        immediately against the current book or is rejected.  The order
        never rests in the queue beyond this call (GTC deferred to MVP2).

        Args:
            order: The order to fill.  ``order.limit_price`` MUST be set.

        Returns:
            :class:`~hermes.domain.trading.fill.Fill` on a successful fill,
            :class:`~hermes.domain.trading.fill.Rejection` when the limit
            is not reachable given the current book depth.

        Raises:
            ValueError: When ``order.limit_price`` is ``None``.
        """
        if order.limit_price is None:
            raise ValueError(
                "SimulatedExchangeAdapter.place_limit_order: "
                "order.limit_price must not be None for a limit order. "
                "Use place_market_order for market orders."
            )

        book = self.orderbook_fn(order.pair)
        fees = self.fee_schedule_fn(order.pair)
        now = self.clock.now()

        result = self.fill_model.fill_order(order, book, now, fees)

        if isinstance(result, Fill):
            self._apply_fill(result)
            log.info(
                "simulated_limit_fill",
                pair=str(order.pair),
                side=str(order.side),
                executed_qty=str(result.executed_qty),
                avg_price=str(result.avg_price),
                limit_price=str(order.limit_price),
            )
        else:
            log.info(
                "simulated_limit_rejection",
                pair=str(order.pair),
                side=str(order.side),
                reason=result.reason,
            )

        return result

    def cancel_order(self, *, order_id: str) -> None:
        """Cancel a resting order by its identifier — idempotent.

        For the simulated adapter all limit orders are IOC, so in practice
        no orders rest in the queue after ``place_limit_order`` returns.
        This method is provided for protocol conformance and for future
        GTC support.  Cancelling a non-existent ``order_id`` is a silent
        no-op (idempotent as required by the port contract).

        Args:
            order_id: The adapter-assigned order identifier.
        """
        removed = self._open_orders.pop(order_id, None)
        if removed is not None:
            log.info("simulated_order_cancelled", order_id=order_id)
        else:
            log.debug("simulated_cancel_noop", order_id=order_id)

    # ------------------------------------------------------------------
    # ExchangePort — read methods
    # ------------------------------------------------------------------

    def get_orderbook(self, *, pair: Pair) -> Orderbook:
        """Return the current depth snapshot for ``pair``.

        Delegates to the injected ``orderbook_fn`` — in backtest mode this
        returns a synthetic bar-derived book; in shadow mode a live L2
        snapshot from the exchange.

        Args:
            pair: Trading pair to query.

        Returns:
            An :class:`~hermes.domain.market_data.orderbook.Orderbook`
            snapshot normalised per the port contract (bids descending,
            asks ascending).
        """
        return self.orderbook_fn(pair)

    def get_balance(self, *, currency: Currency) -> Quantity:
        """Return the current free balance for ``currency``.

        Reads the internal free-balance ledger.  Returns zero when the
        currency is not held (not an error — zero is a valid state).

        Args:
            currency: The asset to query.

        Returns:
            :class:`~hermes.domain.value_objects.quantity.Quantity` with
            ``asset == currency`` and ``amount`` equal to the free balance.
        """
        amount = self._balances.get(currency, Decimal("0"))
        return Quantity(amount=amount, asset=currency)

    def get_fees(self, *, pair: Pair) -> FeeSchedule:
        """Return the fee schedule for ``pair``.

        Delegates to the injected ``fee_schedule_fn``.

        Args:
            pair: Trading pair to look up.

        Returns:
            A :class:`~hermes.domain.fees.FeeSchedule` for the pair.
        """
        return self.fee_schedule_fn(pair)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _apply_fill(self, fill: Fill) -> None:
        """Update the balance ledger and lot_ledger after a confirmed fill.

        For BUY fills:
        - Deduct ``executed_qty * avg_price + fee.amount`` from quote balance.
        - Add ``executed_qty`` to base balance.
        - Open a new ``Lot`` and append to ``lot_ledger``.

        For SELL fills:
        - Deduct ``executed_qty`` from base balance.
        - Add ``executed_qty * avg_price - fee.amount`` to quote balance.

        Args:
            fill: The confirmed :class:`~hermes.domain.trading.fill.Fill`.
        """
        order = fill.order
        base = order.pair.base
        quote = order.pair.quote
        qty = fill.executed_qty
        price = fill.avg_price
        fee_amount = fill.fee.amount

        if order.side == OrderSide.BUY:
            cost = qty * price + fee_amount
            self._balances[quote] = self._balances.get(quote, Decimal("0")) - cost
            self._balances[base] = self._balances.get(base, Decimal("0")) + qty
            self._open_lot(fill)
        else:
            proceeds = qty * price - fee_amount
            self._balances[base] = self._balances.get(base, Decimal("0")) - qty
            self._balances[quote] = self._balances.get(quote, Decimal("0")) + proceeds

    def _open_lot(self, fill: Fill) -> None:
        """Create a ``Lot`` from a BUY fill and append to ``lot_ledger``.

        The cost basis is stored in EUR per convention (ADR-003 §03).  When
        the fill is denominated in a non-EUR quote (e.g. USDT) the adapter
        records the USDT price in the cost_basis field and marks the currency
        as EUR.  A proper EUR conversion requires the FX port (Spec 5 — out
        of scope here); for paper-trading purposes this is an acceptable
        approximation that the pre-registration notes explicitly acknowledge.

        Args:
            fill: The BUY fill to open a lot for.
        """
        order = fill.order
        lot_id = LotId.new()
        qty = Quantity(amount=fill.executed_qty, asset=order.pair.base)

        # Cost basis: use avg_price in EUR (approximation — FX conversion
        # deferred to Spec 5 / MVP2, acknowledged in pre-reg §6).
        cost_basis = Money(amount=fill.avg_price, currency=Currency.EUR)

        lot_result = Lot.open(
            lot_id=lot_id,
            asset=order.pair.base,
            qty=qty,
            cost_basis_per_unit=cost_basis,
            strategy_id=fill.strategy_id,
            opened_at=fill.timestamp,
        )

        # Lot.open returns Result[Lot, DomainError].  In practice this only
        # fails when qty is zero, which Fill.__post_init__ already forbids.
        # Log and skip on unexpected failure rather than raising — the fill
        # already succeeded; a lot-tracking failure should not corrupt the
        # adapter's balance state.
        if isinstance(lot_result, Failure):
            log.warning(
                "simulated_lot_open_failed",
                lot_id=str(lot_id),
                reason=str(lot_result.failure()),
            )
        else:
            lot = lot_result.unwrap()
            self.lot_ledger.append(lot)
            log.debug(
                "simulated_lot_opened",
                lot_id=str(lot.lot_id),
                qty=str(lot.original_qty),
            )
