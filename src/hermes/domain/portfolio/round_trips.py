"""Closed round trips of a fill sequence: the one loop both sides of M-09 use.

A round trip runs from a pair's first BUY while flat to the SELL that brings
it back to flat. Its gross P&L is sale proceeds minus the average cost of what
was sold; its fees are every fee paid on the way. A position still open at the
end is not a round trip.

The backtest's performance file and the shadow window's M-09 parity check
used to carry a copy each, so the two sides of the parity check could count
the same fills differently (issue #1179). Both now read this loop. A fee in
any currency but the pair's quote is refused: adding it to quote amounts
would mix currencies in the P&L.

Reference: pre-registration-v1.md §8.1, §9; ADR-021 Done rule 3;
openspec/changes/1179-one-pearson-round-trips/change.md.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, localcontext
from typing import Final

from hermes.domain.trading.order import OrderSide
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.precision import EXACT_CONTEXT

__all__ = ["ClosedRoundTrip", "RoundTripError", "RoundTripFill", "round_trips"]

_ZERO: Final = Decimal(0)


class RoundTripError(ValueError):
    """A fill cannot be counted: it sells more than is held, or its fee is not in the quote."""


@dataclass(frozen=True, slots=True)
class RoundTripFill:
    """The parts of one fill a round trip reads, whatever recorded it."""

    pair: Pair
    side: OrderSide
    qty: Decimal
    price: Decimal
    fee: Money
    at: datetime


@dataclass(frozen=True, slots=True)
class ClosedRoundTrip:
    """One closed round trip in one pair, in the quote currency."""

    pair: Pair
    entered_at: datetime
    """Time of the BUY that opened it."""

    gross_pnl: Decimal
    """Sale proceeds minus the cost of what was sold."""

    fees: Decimal
    """Every fee paid on the entry and the exit."""

    @property
    def net_pnl(self) -> Decimal:
        """Gross P&L minus every fee."""
        with localcontext(EXACT_CONTEXT):
            return self.gross_pnl - self.fees


@dataclass(slots=True)
class _Open:
    entered_at: datetime
    qty: Decimal = _ZERO
    cost: Decimal = _ZERO
    gross: Decimal = _ZERO
    fees: Decimal = _ZERO


def round_trips(fills: Iterable[RoundTripFill]) -> list[ClosedRoundTrip]:
    """Return the closed round trips of ``fills``, in the order they closed.

    Args:
        fills: One strategy's fills, oldest first.

    Returns:
        One :class:`ClosedRoundTrip` per pair that went from flat back to flat.

    Raises:
        RoundTripError: A SELL sells more than its pair holds, or a fee is not
            in the pair's quote currency.
    """
    held: dict[Pair, _Open] = {}
    out: list[ClosedRoundTrip] = []
    with localcontext(EXACT_CONTEXT):
        for fill in fills:
            if fill.fee.currency is not fill.pair.quote:
                raise RoundTripError(
                    f"{fill.pair}: fee at {fill.at.isoformat()} is in {fill.fee.currency}, "
                    f"not the quote {fill.pair.quote}"
                )
            if fill.side is OrderSide.BUY:
                position = held.setdefault(fill.pair, _Open(entered_at=fill.at))
                position.qty += fill.qty
                position.cost += fill.qty * fill.price
                position.fees += fill.fee.amount
                continue
            position = held.get(fill.pair) or _Open(entered_at=fill.at)
            if fill.qty > position.qty:
                raise RoundTripError(
                    f"{fill.pair}: sell of {fill.qty} at {fill.at.isoformat()} "
                    f"exceeds the {position.qty} held"
                )
            sold_cost = position.cost * fill.qty / position.qty
            position.gross += fill.qty * fill.price - sold_cost
            position.fees += fill.fee.amount
            position.cost -= sold_cost
            position.qty -= fill.qty
            if position.qty == _ZERO:
                out.append(
                    ClosedRoundTrip(fill.pair, position.entered_at, position.gross, position.fees)
                )
                del held[fill.pair]
    return out
