"""Trade value object — single matched tick from a live trade stream.

A :class:`Trade` represents one executed trade event as reported by an exchange
WebSocket trade stream. The aggressor direction is encoded as a
:class:`TradeSide` enum to keep the domain free of wire-specific boolean
conventions (e.g. Binance's ``is_buyer_maker`` is inverted relative to
intuition — an adapter translates to ``TradeSide.BUY`` / ``SELL`` once at
the port boundary).

Domain purity: this module imports only domain value objects and stdlib. No
pydantic, no httpx (enforced by import-linter contracts).

Reference: code-conventions.md §1 (Value Object) and §9 (UTC internally).
spec-4b-market-data-port change.md §Design.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum

from hermes.domain.value_objects.pair import Pair

__all__ = ["Trade", "TradeSide"]


class TradeSide(StrEnum):
    """Aggressor direction of a matched trade.

    The value identifies the taker side — the party that crossed the spread.

    Attributes:
        BUY: Taker is the buyer; the order lifted an existing ask.
        SELL: Taker is the seller; the order hit an existing bid.

    Reference: spec-4b-market-data-port change.md D-4.
    """

    BUY = "buy"
    SELL = "sell"


@dataclass(frozen=True, slots=True)
class Trade:
    """Immutable single-tick trade event for one pair.

    Attributes:
        pair: Trading pair the trade occurred on (e.g. ``BTC/USDT``).
        price: Execution price in quote currency. Must be > 0.
        quantity: Base-asset quantity that changed hands. Must be > 0.
        side: Aggressor direction (:class:`TradeSide`). Identifies the taker.
        trade_time: UTC-aware :class:`~datetime.datetime` of the trade execution
            (exchange server clock).
        trade_id: Exchange-assigned unique identifier for the trade (opaque
            string — Binance uses numeric IDs; Kraken uses hex digests).

    Reference: code-conventions.md §1.
    spec-4b-market-data-port change.md §Design.
    """

    pair: Pair
    price: Decimal
    quantity: Decimal
    side: TradeSide
    trade_time: datetime
    trade_id: str

    def __post_init__(self) -> None:
        """Enforce UTC-aware timestamp and positive price / quantity.

        Raises:
            ValueError: On naive / non-UTC ``trade_time``, ``price <= 0``,
                or ``quantity <= 0``.
        """
        if self.trade_time.tzinfo is None or self.trade_time.tzinfo.utcoffset(
            None
        ) != UTC.utcoffset(None):
            raise ValueError(f"Trade.trade_time must be UTC-aware, got {self.trade_time!r}")
        if self.price <= Decimal("0"):
            raise ValueError(f"Trade.price must be > 0, got {self.price}")
        if self.quantity <= Decimal("0"):
            raise ValueError(f"Trade.quantity must be > 0, got {self.quantity}")
