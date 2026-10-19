"""The evidence trail: every sale back to the lots, fees and rates behind it.

One :class:`TrailLine` per stored FIFO consumption, the same pieces the
Modelo 100 report sums (change 568-tax-pack D-1), so a gestor can follow any
figure to the trades that produced it:

- transmission share = proceeds share - sell fee share
- gain = transmission share - acquisition share (buy fee included)

A piece with any pending EUR value has no gain; the Modelo 100 report leaves
its whole sale out of the totals.

Reference: openspec/archive/568-tax-pack/change.md, ADR-016 item 3.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from hermes.domain.filing.modelo100 import DisposalPiece
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.precision import exact

__all__ = ["TradeRate", "TrailLine", "build_trail"]

# Stored shares carry up to 30 decimals; the default 28-digit context would round them.


@dataclass(frozen=True, slots=True)
class TradeRate:
    """The ECB rate a trade was valued at.

    Attributes:
        rate: USD per EUR (USDT read as USD, ADR-016 item 5).
        rate_date: The ECB publication date used.
    """

    rate: Decimal
    rate_date: date


@dataclass(frozen=True, slots=True)
class TrailLine:
    """One part of a sale, drawn from one lot.

    Attributes:
        sell_ref: Ledger id of the SELL.
        asset: Coin sold.
        received: Currency received.
        disposed_at: Fill time of the SELL.
        buy_ref: Ledger id of the BUY that opened the lot.
        acquired_at: Opening time of the lot.
        qty: Quantity drawn from the lot.
        proceeds_eur: Share of the gross EUR proceeds; ``None`` while pending.
        sell_fee_eur: Share of the EUR sell fee; ``None`` while pending.
        acquisition_eur: Share of the lot's EUR acquisition value; ``None`` while pending.
        gain_eur: Net proceeds minus acquisition; ``None`` while any value is pending.
        sell_rate: ECB rate of the SELL, when stored.
        buy_rate: ECB rate of the BUY, when stored.
    """

    sell_ref: str
    asset: Currency
    received: Currency
    disposed_at: datetime
    buy_ref: str
    acquired_at: datetime
    qty: Decimal
    proceeds_eur: Decimal | None
    sell_fee_eur: Decimal | None
    acquisition_eur: Decimal | None
    gain_eur: Decimal | None
    sell_rate: TradeRate | None
    buy_rate: TradeRate | None


def build_trail(
    pieces: Iterable[DisposalPiece], rates: Mapping[str, TradeRate]
) -> tuple[TrailLine, ...]:
    """Return one trail line per piece, in the order given.

    Args:
        pieces: Stored FIFO consumptions of the year's sales.
        rates: ECB rate per trade id; a trade with no stored rate is absent.

    Returns:
        The trail.
    """
    return tuple(_line(piece, rates) for piece in pieces)


def _line(piece: DisposalPiece, rates: Mapping[str, TradeRate]) -> TrailLine:
    gain = None
    if piece.proceeds_eur is not None and piece.fee_eur is not None and piece.cost_eur is not None:
        with exact():
            gain = piece.proceeds_eur - piece.fee_eur - piece.cost_eur
    return TrailLine(
        sell_ref=piece.sell_ref,
        asset=piece.asset,
        received=piece.received,
        disposed_at=piece.disposed_at,
        buy_ref=piece.buy_ref,
        acquired_at=piece.acquired_at,
        qty=piece.qty,
        proceeds_eur=piece.proceeds_eur,
        sell_fee_eur=piece.fee_eur,
        acquisition_eur=piece.cost_eur,
        gain_eur=gain,
        sell_rate=rates.get(piece.sell_ref),
        buy_rate=rates.get(piece.buy_ref),
    )
