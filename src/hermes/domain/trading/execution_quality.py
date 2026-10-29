"""Execution-quality measures for a simulated fill against an order book.

Shadow trading walks the live order book; the backtest walks the
pre-registration §6.2 book. Two numbers describe one shadow fill:

- **drift** — how far the shadow fill price sits from the §6.2 model price of
  the same order, in basis points. This is the per-fill input of the
  SHADOW_CERTIFIED gate (pre-registration §7: p50 below 30 bps).
- **slippage** — how far the fill price sits from the book mid, as a fraction.
  This is the single-fill input of risk rule RO-03.

Both are signed in the cost direction: a positive value means the fill was
worse for the strategy (a BUY above, or a SELL below, the reference).

Reference: openspec/archive/353-fill-kernel-in-serve/change.md D-3, D-4.
"""

from decimal import Decimal

from hermes.domain.market_data.orderbook import Orderbook
from hermes.domain.trading.order import OrderSide

__all__ = ["book_mid", "book_spread", "drift_bps", "slippage_vs_mid"]

_BPS = Decimal("10000")
_TWO = Decimal("2")


def _adverse(side: OrderSide, price: Decimal, reference: Decimal) -> Decimal:
    """Return ``price - reference`` for a BUY and ``reference - price`` for a SELL."""
    return price - reference if side is OrderSide.BUY else reference - price


def drift_bps(side: OrderSide, fill_price: Decimal, model_price: Decimal) -> Decimal:
    """Return the drift of ``fill_price`` from ``model_price`` in basis points.

    Args:
        side: Side of the order both prices belong to.
        fill_price: Price the shadow fill got. Must be > 0.
        model_price: Price the §6.2 model gives the same order. Must be > 0.

    Returns:
        ``(fill - model) / model * 10 000`` for a BUY, the negation for a SELL.

    Raises:
        ValueError: On a non-positive price.
    """
    if fill_price <= 0 or model_price <= 0:
        raise ValueError(f"prices must be > 0, got fill={fill_price} model={model_price}")
    return _adverse(side, fill_price, model_price) / model_price * _BPS


def slippage_vs_mid(side: OrderSide, fill_price: Decimal, mid: Decimal) -> Decimal:
    """Return the slippage of ``fill_price`` from the book mid as a fraction.

    Args:
        side: Side of the filled order.
        fill_price: Average fill price. Must be > 0.
        mid: Mid price of the book the order walked. Must be > 0.

    Returns:
        ``(fill - mid) / mid`` for a BUY, the negation for a SELL.

    Raises:
        ValueError: On a non-positive price.
    """
    if fill_price <= 0 or mid <= 0:
        raise ValueError(f"prices must be > 0, got fill={fill_price} mid={mid}")
    return _adverse(side, fill_price, mid) / mid


def book_mid(book: Orderbook) -> Decimal | None:
    """Return the mid of the best bid and ask, or ``None`` for a one-sided book."""
    if not book.bids or not book.asks:
        return None
    return (book.bids[0].price + book.asks[0].price) / _TWO


def book_spread(book: Orderbook) -> Decimal:
    """Return ``(best ask - best bid) / mid``, or ``0`` for a one-sided book."""
    mid = book_mid(book)
    if mid is None:
        return Decimal("0")
    return (book.asks[0].price - book.bids[0].price) / mid
