"""LotConsumption aggregate — the record of a SELL drawing from a lot.

A :class:`LotConsumption` is the append-only bookkeeping row emitted every
time a SELL trade draws some quantity from an existing :class:`Lot`. One SELL
that spans multiple lots produces multiple ``LotConsumption`` rows, one per
lot touched — this is the essence of FIFO tax accounting.

Realized gain is expressed in EUR (ADR-003 §03 requires EUR at row-write
time — reconstructing EUR from historical FX rates is not audit-defensible).

Reference: ADR-003 §03 (LOT_CONSUMPTIONS table + FIFO worked example),
ADR-002 (Spanish tax posture), code-conventions.md §1 (Value Object),
§3 (immutability), §6 (Decimal).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from returns.result import Failure, Result, Success

from hermes.domain.errors import DomainError, InvalidLot
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.lot_id import LotId
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.quantity import Quantity

__all__ = ["LotConsumption"]


@dataclass(frozen=True, slots=True)
class LotConsumption:
    """Immutable record of a SELL trade drawing from a specific :class:`Lot`.

    Fields:
        lot_id: The :class:`Lot` that supplied the consumed quantity.
        consumed_qty: The amount drawn from the lot. Positive, non-zero, and
            in the lot's asset.
        realized_gain: Gain (or loss) in EUR realised by this consumption.
            Computed as ``(sell_price_per_unit - lot_cost_basis) * consumed_qty
            - sell_fee_share``, with the buy fee inside the cost basis.
            Can be negative on a losing trade.
        consumed_at: UTC-aware moment of the SELL. Ties every consumption to
            the parent trade timestamp for reconciliation and tax reports.

    Reference: ADR-003 §03 LOT_CONSUMPTIONS table.
    """

    lot_id: LotId
    consumed_qty: Quantity
    realized_gain: Money
    consumed_at: datetime

    def __post_init__(self) -> None:
        """Enforce EUR-only realized gain, non-zero quantity, tz-aware time."""
        if self.realized_gain.currency != Currency.EUR:
            raise ValueError(
                f"LotConsumption.realized_gain must be in EUR (ADR-003 §03), "
                f"got {self.realized_gain.currency!r}"
            )
        if self.consumed_qty.amount == Decimal("0"):
            raise ValueError(
                "LotConsumption.consumed_qty must be > 0 — zero consumption is meaningless"
            )
        if self.consumed_at.tzinfo is None:
            raise ValueError(
                f"LotConsumption.consumed_at must be timezone-aware, got naive {self.consumed_at!r}"
            )

    @classmethod
    def record(
        cls,
        *,
        lot_id: LotId,
        consumed_qty: Quantity,
        sell_price_per_unit: Money,
        lot_cost_basis_per_unit: Money,
        consumed_at: datetime,
        sell_fee_eur: Money | None = None,
    ) -> Result[LotConsumption, DomainError]:
        """Compute realized gain and construct a validated :class:`LotConsumption`.

        Gain = ``(sell_price - cost_basis) * qty - sell_fee_eur`` (ADR-016
        item 3): the buy fee is already inside the lot's cost basis, the
        sell fee is this consumption's share of the SELL's fee.

        Args:
            lot_id: The lot being drawn from.
            consumed_qty: How much of the lot's asset the SELL took.
            sell_price_per_unit: EUR fill price per one unit of the lot's asset.
            lot_cost_basis_per_unit: EUR cost basis per one unit at lot open,
                buy fee included.
            consumed_at: UTC-aware moment of the SELL.
            sell_fee_eur: This consumption's share of the SELL's EUR fee.
                ``None`` means no fee.

        Returns:
            ``Success(LotConsumption)`` when every amount is EUR-denominated
            and ``consumed_qty > 0``; ``Failure(InvalidLot)`` otherwise.
        """
        if sell_fee_eur is not None and sell_fee_eur.currency != Currency.EUR:
            return Failure(
                InvalidLot(
                    reason=(
                        f"LotConsumption.record: sell_fee_eur must be EUR, "
                        f"got {sell_fee_eur.currency!r}"
                    )
                )
            )
        if sell_price_per_unit.currency != Currency.EUR:
            return Failure(
                InvalidLot(
                    reason=(
                        f"LotConsumption.record: sell_price_per_unit must be EUR "
                        f"(ADR-003 §03), got {sell_price_per_unit.currency!r}"
                    )
                )
            )
        if lot_cost_basis_per_unit.currency != Currency.EUR:
            return Failure(
                InvalidLot(
                    reason=(
                        f"LotConsumption.record: lot_cost_basis_per_unit must be EUR "
                        f"(ADR-003 §03), got {lot_cost_basis_per_unit.currency!r}"
                    )
                )
            )
        gain_per_unit = sell_price_per_unit.amount - lot_cost_basis_per_unit.amount
        fee = Decimal("0") if sell_fee_eur is None else sell_fee_eur.amount
        realized_gain = Money(
            amount=gain_per_unit * consumed_qty.amount - fee, currency=Currency.EUR
        )
        try:
            return Success(
                cls(
                    lot_id=lot_id,
                    consumed_qty=consumed_qty,
                    realized_gain=realized_gain,
                    consumed_at=consumed_at,
                )
            )
        except ValueError as exc:
            return Failure(InvalidLot(reason=str(exc)))
