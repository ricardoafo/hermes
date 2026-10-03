"""PriceLookupPort — read-only "price now" for the operator console.

The engine trades on closed 4h and 1d bars only, so nothing in it knows the
current price. The console needs one to value open positions and show
today's change. This port gives it a read-only last-trade price.

It is for display only. The strategy never reads it: a signal computed from a
live tick instead of a closed bar would break the closed-bar rule
(pre-registration §5.1) and backtest parity (ADR-011). A test guard keeps
``hermes.domain.strategy`` from importing it.

Reference: issue #389.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol, runtime_checkable

from hermes.domain.value_objects.pair import Pair

__all__ = ["PriceLookupPort", "PriceQuote"]


@dataclass(frozen=True, slots=True)
class PriceQuote:
    """The last traded price of a pair, and when it was fetched."""

    pair: Pair
    price: Decimal
    as_of: datetime


@runtime_checkable
class PriceLookupPort(Protocol):
    """Read-only current price, for display."""

    async def last_price(self, pair: Pair) -> PriceQuote:
        """Return the latest traded price of ``pair``.

        Raises:
            hermes.domain.errors.InfraError: The price could not be read.
        """
        ...
