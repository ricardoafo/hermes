"""Synthetic order book for backtests over data without L2 depth.

Binance Vision ships klines but no depth snapshots, so a backtest cannot walk a
recorded book. :func:`synthesize_orderbook` builds a deterministic stand-in: a
symmetric book around a reference price whose touch sits a fixed number of
basis points away, with deeper levels one step further out and each side's
depth split geometrically so the touch is the thinnest level (real books are
deeper away from the touch).

The caller chooses the reference price and the half-spread. The backtest
places the touch at the pre-registered execution price (pre-registration §6.2),
so an order that fits the touch fills exactly at that price and larger orders
walk deeper or are rejected.

Reference: openspec/archive/backtest-driver-mvp1-phase2/change.md D-1, R-3;
ADR-011 D-07.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import ROUND_DOWN, Decimal

from hermes.domain.market_data.orderbook import Orderbook, OrderbookLevel
from hermes.domain.value_objects.pair import Pair

__all__ = ["LEVEL_QUANTUM", "synthesize_orderbook"]

LEVEL_QUANTUM = Decimal("1e-12")
"""Grid of every level quantity except the outermost, which takes the remainder."""

_BPS = Decimal("10000")
_ONE = Decimal("1")


def synthesize_orderbook(
    pair: Pair,
    reference_price: Decimal,
    half_spread_bps: Decimal,
    depth_per_side: Decimal,
    snapshot_time: datetime,
    *,
    levels: int = 10,
    level_growth: Decimal = Decimal("1.5"),
    level_step_bps: Decimal = Decimal("1"),
) -> Orderbook:
    """Return a symmetric book of ``levels`` levels per side around ``reference_price``.

    Level ``i`` (0 is the touch) sits ``half_spread_bps + i * level_step_bps``
    basis points from the reference, below it for bids and above it for asks.
    Its quantity is proportional to ``level_growth ** i``; each side sums
    exactly to ``depth_per_side``.

    Args:
        pair: Pair the book belongs to.
        reference_price: Price the book is centred on. Must be > 0.
        half_spread_bps: Distance of the touch from the reference, in bps.
            Must be > 0.
        depth_per_side: Total base quantity on each side. Must be > 0 and
            large enough that the touch level is at least
            :data:`LEVEL_QUANTUM`.
        snapshot_time: UTC-aware time stamped on the book.
        levels: Levels per side. Must be >= 1.
        level_growth: Ratio between consecutive level sizes, touch outwards.
            Must be >= 1.
        level_step_bps: Extra distance of each deeper level, in bps. Must be
            > 0 so prices strictly move away from the touch.

    Returns:
        The synthetic :class:`Orderbook`.

    Raises:
        ValueError: On a non-positive price, half-spread, depth or step,
            ``levels < 1``, ``level_growth < 1``, an outermost level at or
            beyond 10000 bps (a bid at or below zero), a depth too small for
            the level grid, or a naive or non-UTC ``snapshot_time``.
    """
    _validate(reference_price, half_spread_bps, depth_per_side, levels, level_growth)
    _validate_step(half_spread_bps, levels, level_step_bps)
    if snapshot_time.tzinfo is None or snapshot_time.utcoffset() != UTC.utcoffset(None):
        raise ValueError(f"snapshot_time must be UTC-aware, got {snapshot_time!r}")
    sizes = _level_sizes(depth_per_side, levels, level_growth)
    bids: list[OrderbookLevel] = []
    asks: list[OrderbookLevel] = []
    for i, size in enumerate(sizes):
        offset = (half_spread_bps + level_step_bps * Decimal(i)) / _BPS
        bids.append(OrderbookLevel(price=reference_price * (_ONE - offset), quantity=size))
        asks.append(OrderbookLevel(price=reference_price * (_ONE + offset), quantity=size))
    return Orderbook(pair=pair, bids=tuple(bids), asks=tuple(asks), snapshot_time=snapshot_time)


def _validate(
    reference_price: Decimal,
    half_spread_bps: Decimal,
    depth_per_side: Decimal,
    levels: int,
    level_growth: Decimal,
) -> None:
    """Raise ``ValueError`` on an input outside its documented range."""
    for name, value in (
        ("reference_price", reference_price),
        ("half_spread_bps", half_spread_bps),
        ("depth_per_side", depth_per_side),
    ):
        if value <= 0:
            raise ValueError(f"{name} must be > 0, got {value}")
    if levels < 1:
        raise ValueError(f"levels must be >= 1, got {levels}")
    if level_growth < _ONE:
        raise ValueError(f"level_growth must be >= 1, got {level_growth}")


def _validate_step(half_spread_bps: Decimal, levels: int, level_step_bps: Decimal) -> None:
    """Raise ``ValueError`` unless every level price stays strictly positive."""
    if level_step_bps <= 0:
        raise ValueError(f"level_step_bps must be > 0, got {level_step_bps}")
    outermost = half_spread_bps + level_step_bps * Decimal(levels - 1)
    if outermost >= _BPS:
        raise ValueError(f"outermost level is {outermost} bps from the reference; must be < 10000")


def _level_sizes(depth: Decimal, levels: int, growth: Decimal) -> list[Decimal]:
    """Split ``depth`` geometrically; the outermost level takes the exact remainder.

    Inner levels are rounded down to :data:`LEVEL_QUANTUM`, so the remainder
    is never smaller than its unrounded share and the sizes stay
    non-decreasing.
    """
    weights = [growth**i for i in range(levels)]
    total = sum(weights, Decimal("0"))
    inner = [(depth * w / total).quantize(LEVEL_QUANTUM, rounding=ROUND_DOWN) for w in weights[:-1]]
    if inner and inner[0] <= 0:
        raise ValueError(f"depth_per_side {depth} is too small for {levels} levels")
    return [*inner, depth - sum(inner, Decimal("0"))]
