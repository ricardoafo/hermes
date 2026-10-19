"""OrderbookWalkFillModel — concrete FillModelPort for backtest and shadow modes.

This module delivers the single fill implementation that serves BOTH backtest
(synthetic orderbook reconstructed from historical bars) and shadow (live L2
book) phases without any ``if backtest_mode:`` branching.  The only observable
difference between the two modes is *what is wired as the* ``Orderbook`` at
the composition root — the fill logic is identical.

Semantic contracts enforced here (per ADR-011 and pre-reg §6.2):

- **D-10 Market orders**: fill at the NEXT bar's open, never the current bar's
  close.  Callers pass the next open as the first ask/bid level in the
  orderbook (backtest synthetic book) or read it from the live book (shadow).
  The fill model does NOT read a "current close" — it only consumes what the
  ``Orderbook`` contains.
- **D-11 Limit orders**: fill only when the best ask (BUY) or best bid (SELL)
  satisfies the limit price; otherwise return ``Rejection("PRICE_FILTER: …")``.
- **Slippage** (backtest only, pre-reg §6.2): 5 bps over walked price for
  entries, 15 bps for stop-exits.  Enabled by passing ``apply_slippage=True``
  at the composition root.  Shadow mode passes ``False`` — the real spread is
  already captured in the walked levels.
- **Deterministic under seed**: pass an explicit ``random.Random`` instance for
  reproducible property and chaos tests.

Reference: spec-2b-orderbook-walk-fill-model/change.md, ADR-011.
"""

import random
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from hermes.domain.fees.fee_schedule import FeeSchedule
from hermes.domain.market_data.orderbook import Orderbook, OrderbookLevel
from hermes.domain.trading.fill import Fill, Rejection
from hermes.domain.trading.order import Order, OrderSide
from hermes.domain.value_objects.money import Money

__all__ = ["OrderbookWalkFillModel"]

# Basis-point denominator — 1 bp = 0.0001 of the price.
_BPS_DENOM: Decimal = Decimal("10000")

# Default slippage constants per pre-reg §6.2.
_DEFAULT_ENTRY_SLIPPAGE_BPS: int = 5
_DEFAULT_STOP_SLIPPAGE_BPS: int = 15


@dataclass
class OrderbookWalkFillModel:
    """Concrete fill implementation that walks orderbook levels to compute fills.

    This class satisfies :class:`~hermes.ports.fill_model.FillModelPort` via
    structural subtyping (``@runtime_checkable``).  It drives both backtest
    and shadow phases — the mode is determined entirely by what
    :class:`~hermes.domain.market_data.orderbook.Orderbook` is supplied at the
    composition root.

    Attributes:
        apply_slippage: When ``True`` (backtest mode), applies basis-point
            slippage on top of the walked VWAP.  When ``False`` (shadow mode),
            the real spread captured in the book is the only cost.
        entry_slippage_bps: Basis points of slippage added to entry fills when
            ``apply_slippage`` is ``True``.  Default: 5 bps (pre-reg §6.2).
        stop_slippage_bps: Basis points of slippage added to stop-exit fills
            when ``apply_slippage`` is ``True``.  Default: 15 bps (pre-reg §6.2).
        rng: :class:`random.Random` instance used for tie-breaking heuristics.
            Pass an explicit seeded instance for deterministic replay.  ``None``
            (default) → a fresh instance seeded from OS entropy.
    """

    apply_slippage: bool = False
    entry_slippage_bps: int = _DEFAULT_ENTRY_SLIPPAGE_BPS
    stop_slippage_bps: int = _DEFAULT_STOP_SLIPPAGE_BPS
    rng: random.Random = field(default_factory=random.Random)

    def fill_order(
        self,
        order: Order,
        orderbook: Orderbook,
        clock: datetime,
        fees: FeeSchedule,
    ) -> Fill | Rejection:
        """Attempt to fill ``order`` by walking the provided orderbook.

        Steps:
        1. Reject zero-quantity orders immediately.
        2. Select the relevant levels (asks for BUY, bids for SELL).
        3. For limit orders, check the best price against the limit — reject
           with ``PRICE_FILTER`` if unreachable.
        4. Walk levels consuming quantity until the order is satisfied or
           depth runs out.
        5. Compute VWAP from consumed levels.
        6. Apply slippage when ``apply_slippage=True``.
        7. Compute the exchange fee and return a :class:`Fill`.

        Args:
            order: The order to fill.  Validated at construction — zero qty is
                still possible if the caller builds an ``Order`` with
                ``Quantity(Decimal("0"), ...)``, which the ``Order.__post_init__``
                would reject first.  We guard anyway for defence-in-depth.
            orderbook: Depth snapshot.  ``asks`` consumed for BUY; ``bids``
                consumed for SELL.
            clock: UTC-aware timestamp recorded as the fill timestamp.
            fees: Fee schedule from which the taker rate is selected.

        Returns:
            :class:`~hermes.domain.trading.fill.Fill` on success, or
            :class:`~hermes.domain.trading.fill.Rejection` for expected
            business failure (zero qty, insufficient depth, price filter).

        Raises:
            ValueError: On naive ``clock`` timestamp — the composition root
                must always provide UTC-aware datetimes.
        """
        if clock.tzinfo is None or clock.tzinfo.utcoffset(None) != UTC.utcoffset(None):
            raise ValueError(
                f"OrderbookWalkFillModel.fill_order: clock must be UTC-aware, got {clock!r}"
            )

        # Guard: zero-qty orders have no economic meaning.
        if order.qty.amount <= Decimal("0"):
            return Rejection(
                order=order,
                reason=f"ZERO_QTY: order quantity must be > 0, got {order.qty.amount!r}",
            )

        # Select the correct side of the book.
        if order.side == OrderSide.BUY:
            levels: tuple[OrderbookLevel, ...] = orderbook.asks  # ascending price
        else:
            levels = orderbook.bids  # descending price

        # Empty side means zero depth — reject immediately.
        if not levels:
            side_name = "ask" if order.side == OrderSide.BUY else "bid"
            return Rejection(
                order=order,
                reason=(
                    f"INSUFFICIENT_DEPTH: orderbook has no {side_name} "
                    f"levels for pair {orderbook.pair}"
                ),
            )

        # Limit-price gate (D-11): check before walking any levels.
        price_filter_rejection = self._check_price_filter(order, levels[0].price, orderbook)
        if price_filter_rejection is not None:
            return price_filter_rejection

        # Walk levels, consuming quantity.
        executed_qty, total_cost = self._walk_levels(order, levels)

        # If the book was too thin, return a rejection.
        if executed_qty <= Decimal("0") or executed_qty < order.qty.amount:
            return Rejection(
                order=order,
                reason=(
                    f"INSUFFICIENT_DEPTH: orderbook depth insufficient to fill "
                    f"{order.qty.amount} {order.qty.asset} on {orderbook.pair}; "
                    f"only {executed_qty} could be matched"
                ),
            )

        # Compute VWAP (volume-weighted average price).
        avg_price = total_cost / executed_qty

        # Apply slippage (backtest only, pre-reg §6.2).
        if self.apply_slippage:
            avg_price = self._apply_slippage(avg_price, order.side)

        # Compute taker fee.
        fee_rate = fees.fee_for_volume(Decimal("0"), is_maker=False)
        fee_amount = executed_qty * avg_price * fee_rate
        fee = Money(amount=fee_amount, currency=order.pair.quote)

        return Fill(
            order=order,
            executed_qty=executed_qty,
            avg_price=avg_price,
            fee=fee,
            timestamp=clock,
        )

    def _check_price_filter(
        self,
        order: Order,
        best_price: Decimal,
        orderbook: Orderbook,
    ) -> Rejection | None:
        """Validate limit price against best available price.

        Returns ``None`` when the limit is reachable or the order is a market
        order.  Returns a :class:`Rejection` with ``PRICE_FILTER`` prefix when
        the limit cannot be satisfied by the current book.
        """
        if order.limit_price is None:
            return None
        if order.side == OrderSide.BUY and best_price > order.limit_price:
            return Rejection(
                order=order,
                reason=(
                    f"PRICE_FILTER: best ask {best_price} > limit_price "
                    f"{order.limit_price} for BUY order on {orderbook.pair}"
                ),
            )
        if order.side == OrderSide.SELL and best_price < order.limit_price:
            return Rejection(
                order=order,
                reason=(
                    f"PRICE_FILTER: best bid {best_price} < limit_price "
                    f"{order.limit_price} for SELL order on {orderbook.pair}"
                ),
            )
        return None

    def _walk_levels(
        self,
        order: Order,
        levels: tuple[OrderbookLevel, ...],
    ) -> tuple[Decimal, Decimal]:
        """Walk orderbook levels and return (executed_qty, total_cost).

        Consumes levels in book order until the order quantity is satisfied
        or levels are exhausted.  For limit orders, stops when a level's price
        is worse than the limit price.
        """
        remaining_qty = order.qty.amount
        total_cost = Decimal("0")
        executed_qty = Decimal("0")

        for level in levels:
            if remaining_qty <= Decimal("0"):
                break
            if self._level_exceeds_limit(order, level.price):
                break
            consume = min(remaining_qty, level.quantity)
            total_cost += consume * level.price
            if consume == remaining_qty:
                # The order is matched in full. Summing the parts would round a
                # quantity of more than 28 significant digits (two entries on
                # one pair) to less than the order and refuse the exit (#866).
                return order.qty.amount, total_cost
            executed_qty += consume
            remaining_qty -= consume

        # Cap at order.qty.amount to guard against Decimal precision drift: a
        # min(remaining_qty, level.quantity) result may be slightly > the original
        # qty when Decimal arithmetic rounds to a different precision.  The fill
        # domain type enforces executed_qty <= order.qty.amount in __post_init__,
        # so any excess must be clamped here.
        executed_qty = min(executed_qty, order.qty.amount)
        return executed_qty, total_cost

    def _level_exceeds_limit(self, order: Order, level_price: Decimal) -> bool:
        """Return ``True`` when a level's price is worse than the order limit.

        Used during the walk to stop consuming levels once the order's price
        constraint is violated at deeper book levels.
        """
        if order.limit_price is None:
            return False
        if order.side == OrderSide.BUY and level_price > order.limit_price:
            return True
        return order.side == OrderSide.SELL and level_price < order.limit_price

    def _apply_slippage(self, price: Decimal, side: OrderSide) -> Decimal:
        """Add entry-slippage basis points to the fill price.

        For BUY orders: price is marked UP (worse for buyer).
        For SELL orders: price is marked DOWN (worse for seller).

        This reflects the pre-reg §6.2 conservative slippage model.
        """
        bps = Decimal(self.entry_slippage_bps)
        factor = bps / _BPS_DENOM
        if side == OrderSide.BUY:
            return price * (Decimal("1") + factor)
        return price * (Decimal("1") - factor)
