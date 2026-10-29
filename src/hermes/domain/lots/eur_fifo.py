"""FIFO replay that derives every lot and consumption EUR value from the trades.

FIFO matching depends only on quantities and execution order, so the EUR side of
the ledger is a pure function of the trade sequence of one asset: replay the
trades, open one lot per BUY, consume lots oldest first on every SELL, and value
each piece in EUR with fees included (ADR-016 item 3):

- acquisition value of a lot = ``price_eur * qty + buy_fee_eur``
- transmission value of a sale = ``price_eur * qty - sell_fee_eur``
- gain of a consumption = its share of the sale's gross proceeds, minus its
  share of the lot's acquisition value, minus its share of the sale's fee.

Shares are pro rata by quantity; the piece that closes a lot takes the rest of
the lot's acquisition value and the last piece of a sale takes the rest of the
sale's proceeds and fee, so totals add up exactly. A trade without EUR values
(rate pending) leaves the values it feeds as ``None``.

Reference: ADR-016 item 3, IRPF art. 35, change ``trade-eur-values`` D-5, D-6.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Final

from returns.result import Failure, Result, Success

from hermes.domain.errors import DomainError
from hermes.domain.lots.selection import InsufficientInventory
from hermes.domain.trading.order import OrderSide
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.precision import exact
from hermes.domain.value_objects.quantity import Quantity

__all__ = [
    "ConsumptionEurValue",
    "EurTrade",
    "FifoEurProjection",
    "LotEurValue",
    "acquisition_value",
    "exact_sum",
    "replay_fifo_eur",
    "transmission_value",
]

# Wide enough that no sum or difference of stored values is ever rounded;
# shares are cut to _SHARE_QUANTUM so their digit count stays bounded.
_SHARE_QUANTUM: Final[Decimal] = Decimal("1e-30")
_ZERO: Final[Decimal] = Decimal("0")


def exact_sum(values: Iterable[Decimal]) -> Decimal:
    """Add EUR values without rounding.

    Shares carry up to 30 decimals, more than the default 28-digit
    :mod:`decimal` context keeps; summing them there rounds. Callers that
    total ledger EUR values use this instead of :func:`sum`.

    Args:
        values: The values to add.

    Returns:
        Their exact sum.
    """
    with exact():
        return sum(values, start=_ZERO)


def acquisition_value(*, price: Decimal, qty: Decimal, fee: Decimal) -> Decimal:
    """Return ``price * qty + fee`` — what acquiring ``qty`` cost, fee included.

    Args:
        price: Price of one unit.
        qty: Quantity acquired.
        fee: Fee paid on the acquisition, in the price's currency.

    Returns:
        The acquisition value.
    """
    with exact():
        return price * qty + fee


def transmission_value(*, price: Decimal, qty: Decimal, fee: Decimal) -> Decimal:
    """Return ``price * qty - fee`` — what disposing of ``qty`` yielded, net of fee.

    Args:
        price: Price of one unit.
        qty: Quantity disposed of.
        fee: Fee paid on the disposal, in the price's currency.

    Returns:
        The transmission value.
    """
    with exact():
        return price * qty - fee


@dataclass(frozen=True, slots=True)
class EurTrade:
    """One trade of the replayed asset.

    Attributes:
        ref: Caller's stable id for the trade (the ledger row id).
        side: BUY opens a lot; SELL consumes lots.
        qty: Base quantity, strictly positive.
        executed_at: Fill time, copied to the consumptions of a SELL.
        price_eur: EUR price of one unit, ``None`` while pending.
        fee_eur: EUR fee, ``None`` while pending.
    """

    ref: str
    side: OrderSide
    qty: Decimal
    executed_at: datetime
    price_eur: Decimal | None
    fee_eur: Decimal | None

    def __post_init__(self) -> None:
        """Reject a non-positive quantity and half-priced trades.

        Raises:
            ValueError: On ``qty <= 0`` or exactly one of the EUR values set.
        """
        if self.qty <= _ZERO:
            raise ValueError(f"EurTrade.qty must be > 0, got {self.qty}")
        if (self.price_eur is None) != (self.fee_eur is None):
            raise ValueError("EurTrade.price_eur and fee_eur are both set or both pending")

    @property
    def priced(self) -> bool:
        """Return whether the trade has its EUR values."""
        return self.price_eur is not None


@dataclass(frozen=True, slots=True)
class LotEurValue:
    """State of the lot one BUY opened, after the replay.

    Attributes:
        buy_ref: Ref of the opening BUY.
        remaining_qty: Quantity not yet consumed.
        acquisition_eur: EUR acquisition value of the whole lot, fee included.
        cost_basis_per_unit_eur: ``acquisition_eur / original qty``, for display.
    """

    buy_ref: str
    remaining_qty: Decimal
    acquisition_eur: Decimal | None
    cost_basis_per_unit_eur: Decimal | None


@dataclass(frozen=True, slots=True)
class ConsumptionEurValue:
    """One piece of a SELL drawn from one lot.

    Attributes:
        sell_ref: Ref of the SELL.
        buy_ref: Ref of the BUY that opened the consumed lot.
        qty: Quantity drawn.
        consumed_at: Time of the SELL.
        proceeds_eur: Share of the SELL's gross EUR proceeds.
        cost_eur: Share of the lot's EUR acquisition value.
        fee_eur: Share of the SELL's EUR fee.
        gain_eur: ``proceeds_eur - cost_eur - fee_eur``; ``None`` when any part is pending.
    """

    sell_ref: str
    buy_ref: str
    qty: Decimal
    consumed_at: datetime
    proceeds_eur: Decimal | None
    cost_eur: Decimal | None
    fee_eur: Decimal | None
    gain_eur: Decimal | None


@dataclass(frozen=True, slots=True)
class FifoEurProjection:
    """Result of a replay: every lot in BUY order and every consumption in SELL order."""

    lots: tuple[LotEurValue, ...]
    consumptions: tuple[ConsumptionEurValue, ...]


@dataclass(slots=True)
class _OpenLot:
    buy_ref: str
    original_qty: Decimal
    remaining_qty: Decimal
    acquisition_eur: Decimal | None
    cost_allocated: Decimal = field(default=_ZERO)

    def take_cost(self, qty: Decimal) -> Decimal | None:
        if self.acquisition_eur is None:
            return None
        if qty == self.remaining_qty:
            cost = self.acquisition_eur - self.cost_allocated
        else:
            cost = (self.acquisition_eur * qty / self.original_qty).quantize(_SHARE_QUANTUM)
        self.cost_allocated += cost
        return cost

    def freeze(self) -> LotEurValue:
        basis = None
        if self.acquisition_eur is not None:
            basis = (self.acquisition_eur / self.original_qty).quantize(_SHARE_QUANTUM)
        return LotEurValue(
            buy_ref=self.buy_ref,
            remaining_qty=self.remaining_qty,
            acquisition_eur=self.acquisition_eur,
            cost_basis_per_unit_eur=basis,
        )


def replay_fifo_eur(
    asset: Currency, trades: Sequence[EurTrade]
) -> Result[FifoEurProjection, DomainError]:
    """Replay one asset's trades FIFO and value every lot and consumption in EUR.

    Args:
        asset: The base asset all ``trades`` trade (used in error reports).
        trades: The asset's trades in execution order; the order is
            authoritative (earlier BUYs are consumed first).

    Returns:
        ``Success(FifoEurProjection)``, or ``Failure(InsufficientInventory)``
        when a SELL exceeds the quantity still open at that point.
    """
    with exact():
        lots: list[_OpenLot] = []
        consumptions: list[ConsumptionEurValue] = []
        for trade in trades:
            if trade.side == OrderSide.BUY:
                lots.append(_open(trade))
                continue
            sold = _sell(asset, trade, lots)
            if isinstance(sold, Failure):
                return sold
            consumptions.extend(sold.unwrap())
        return Success(
            FifoEurProjection(
                lots=tuple(lot.freeze() for lot in lots),
                consumptions=tuple(consumptions),
            )
        )


def _open(trade: EurTrade) -> _OpenLot:
    acquisition = None
    if trade.price_eur is not None and trade.fee_eur is not None:
        acquisition = acquisition_value(price=trade.price_eur, qty=trade.qty, fee=trade.fee_eur)
    return _OpenLot(
        buy_ref=trade.ref,
        original_qty=trade.qty,
        remaining_qty=trade.qty,
        acquisition_eur=acquisition,
    )


def _sell(
    asset: Currency, trade: EurTrade, lots: list[_OpenLot]
) -> Result[tuple[ConsumptionEurValue, ...], DomainError]:
    available = sum((lot.remaining_qty for lot in lots), start=_ZERO)
    if available < trade.qty:
        return Failure(
            InsufficientInventory(
                requested=Quantity(amount=trade.qty, asset=asset),
                available=Quantity(amount=available, asset=asset),
            )
        )
    pieces: list[tuple[_OpenLot, Decimal]] = []
    left = trade.qty
    for lot in lots:
        if left == _ZERO:
            break
        take = min(lot.remaining_qty, left)
        if take > _ZERO:
            pieces.append((lot, take))
            left -= take

    gross = None if trade.price_eur is None else trade.price_eur * trade.qty
    proceeds_shares = _shares(gross, [take for _, take in pieces], trade.qty)
    fee_shares = _shares(trade.fee_eur, [take for _, take in pieces], trade.qty)

    out: list[ConsumptionEurValue] = []
    for (lot, take), proceeds, fee in zip(pieces, proceeds_shares, fee_shares, strict=True):
        cost = lot.take_cost(take)
        lot.remaining_qty -= take
        gain = None
        if proceeds is not None and cost is not None and fee is not None:
            gain = proceeds - cost - fee
        out.append(
            ConsumptionEurValue(
                sell_ref=trade.ref,
                buy_ref=lot.buy_ref,
                qty=take,
                consumed_at=trade.executed_at,
                proceeds_eur=proceeds,
                cost_eur=cost,
                fee_eur=fee,
                gain_eur=gain,
            )
        )
    return Success(tuple(out))


def _shares(total: Decimal | None, parts: list[Decimal], whole: Decimal) -> list[Decimal | None]:
    """Split ``total`` pro rata over ``parts``; the last share takes the rest."""
    if total is None:
        return [None] * len(parts)
    shares: list[Decimal | None] = []
    allocated = _ZERO
    for index, part in enumerate(parts):
        if index == len(parts) - 1:
            share = total - allocated
        else:
            share = (total * part / whole).quantize(_SHARE_QUANTUM)
        allocated += share
        shares.append(share)
    return shares
