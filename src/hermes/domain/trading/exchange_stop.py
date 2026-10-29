"""Exchange-held stops: what should rest on the exchange, and what does (#1080).

Every real-money position carries a stop-loss sell held by the exchange, so a
hermes outage never leaves it unprotected (ADR-024 §7). The stop is a backstop
at the strategy's own stop level, not a new exit rule (Code Owner decision,
2026-10-19): the strategy's exit stays the one the ledger records.

- :class:`Protection` is the stop one held position should rest: its pair,
  the entry it protects, the quantity held and the strategy's stop.
- :class:`RestingStop` is a stop-loss sell the exchange holds.
- :class:`StopFate` is what became of a stop that no longer rests.

Reference: GitHub issue #1080; openspec/changes/1080-exchange-stops/change.md.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_DOWN, Decimal
from enum import StrEnum
from typing import Final

from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.time import require_utc

__all__ = ["EXCHANGE_STEP", "Protection", "RestingStop", "StopFate"]

EXCHANGE_STEP: Final = Decimal("0.00000001")
"""The finest price and quantity step the exchange prints (Binance: eight decimals)."""


@dataclass(frozen=True, slots=True)
class Protection:
    """The stop one held position should rest on the exchange.

    Attributes:
        pair: The position's pair.
        entry_time: Close time of the position's entry bar (UTC).
        qty: Base quantity held.
        stop_price: The strategy's stop in force.
    """

    pair: Pair
    entry_time: datetime
    qty: Decimal
    stop_price: Decimal

    def __post_init__(self) -> None:
        """Refuse a naive entry time and a quantity or stop that is not positive.

        Raises:
            ValueError: On any of those.
        """
        require_utc(self.entry_time, name="Protection.entry_time")
        if not self.qty > 0 or not self.stop_price > 0:
            raise ValueError(
                f"Protection needs a positive qty and stop, got {self.qty} and {self.stop_price}"
            )

    def on_exchange_steps(self, step: Decimal = EXCHANGE_STEP) -> Protection:
        """Return this protection rounded down to ``step``.

        Down, for both: a stop never rests above the strategy's own, so it
        never fires before the strategy's exit would, and the exchange is never
        asked to sell more than is held.

        Raises:
            ValueError: Rounding leaves a quantity or stop of zero.
        """
        return Protection(
            pair=self.pair,
            entry_time=self.entry_time,
            qty=self.qty.quantize(step, rounding=ROUND_DOWN),
            stop_price=self.stop_price.quantize(step, rounding=ROUND_DOWN),
        )


@dataclass(frozen=True, slots=True)
class RestingStop:
    """A stop-loss sell the exchange holds.

    Attributes:
        order_id: The exchange's order id, as the venue adapter cancels by it.
        pair: The pair it sells.
        qty: Base quantity it sells.
        stop_price: The price that triggers it.
    """

    order_id: str
    pair: Pair
    qty: Decimal
    stop_price: Decimal

    def protects(self, wanted: Protection) -> bool:
        """Return whether this stop is ``wanted``: same pair, quantity and stop price."""
        return (self.pair, self.qty, self.stop_price) == (
            wanted.pair,
            wanted.qty,
            wanted.stop_price,
        )


class StopFate(StrEnum):
    """What the exchange says became of a stop order."""

    RESTING = "resting"
    """Still on the book, untriggered or partly filled."""
    FILLED = "filled"
    """Triggered and sold."""
    GONE = "gone"
    """Cancelled, expired, rejected or unknown to the exchange: nothing sold."""
