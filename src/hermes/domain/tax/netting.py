"""Savings-base netting: gains against losses, the 25% cross-offset, carried losses.

Readings of IRPF art. 49 used here (change ``tax-estimates`` D-1):

1. Gains and losses from transmissions in the year net into one balance (the
   gains group): hermes net plus the operator's other gains.
2. A negative gains balance offsets up to 25% of the positive savings-income
   balance (interest, dividends: the RCM group).
3. Losses carried from the previous four years, oldest first, offset the
   positive gains balance without limit, then the savings-income balance
   within whatever is left of the same 25% room.
4. A carried loss older than four years is expired; one exactly four years old
   that is still unused at the end of the year expires with it. The year's own
   leftover loss carries forward with the year as its origin.

The operator's figures are private (ADR-016 decision 12): every value field is
excluded from ``repr``.

Reference: ADR-016 decisions 6 and 12, IRPF art. 49.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Final

from hermes.domain.value_objects.precision import exact

__all__ = [
    "CARRY_FORWARD_YEARS",
    "CROSS_OFFSET_SHARE",
    "CarriedLoss",
    "NettingResult",
    "TaxpayerYear",
    "net_savings_base",
]

CROSS_OFFSET_SHARE: Final[Decimal] = Decimal("0.25")
CARRY_FORWARD_YEARS: Final[int] = 4
_ZERO: Final[Decimal] = Decimal(0)


@dataclass(frozen=True, slots=True)
class CarriedLoss:
    """A net loss of the gains group carried from an earlier year.

    Attributes:
        origin_year: Year the loss arose.
        amount: Loss still usable, as a non-negative magnitude.
    """

    origin_year: int = field(repr=False)
    amount: Decimal = field(repr=False)

    def __post_init__(self) -> None:
        """Reject a negative amount.

        Raises:
            ValueError: When ``amount`` is negative (the value is not echoed).
        """
        if self.amount < _ZERO:
            raise ValueError("CarriedLoss.amount must be non-negative")


@dataclass(frozen=True, slots=True)
class TaxpayerYear:
    """The operator's other income for one tax year. All values are private.

    Attributes:
        general_income: General liquidable base (salary and the like).
        personal_minimum: Personal and family minimum for the year.
        other_savings_income: Positive savings income outside hermes (RCM group).
        other_capital_gains: Net gains from other transmissions; negative is a loss.
        carried_losses: Gains-group losses carried from earlier years.
    """

    general_income: Decimal = field(default=_ZERO, repr=False)
    personal_minimum: Decimal = field(default=_ZERO, repr=False)
    other_savings_income: Decimal = field(default=_ZERO, repr=False)
    other_capital_gains: Decimal = field(default=_ZERO, repr=False)
    carried_losses: tuple[CarriedLoss, ...] = field(default=(), repr=False)

    def __post_init__(self) -> None:
        """Reject negative income, minimum or savings income.

        Raises:
            ValueError: Naming the field only, never its value.
        """
        for name in ("general_income", "personal_minimum", "other_savings_income"):
            if getattr(self, name) < _ZERO:
                raise ValueError(f"TaxpayerYear.{name} must be non-negative")


@dataclass(frozen=True, slots=True)
class NettingResult:
    """Outcome of netting one year's savings base.

    Attributes:
        savings_base: Positive gains balance plus positive savings-income balance.
        carried_used: Carried losses applied this year, per origin year.
        carried_left: Losses usable next year (earlier years and this year's own).
        expiring: Carried losses that expire unused at the end of this year.
        expired: Carried losses already older than four years, ignored.
    """

    savings_base: Decimal = field(repr=False)
    carried_used: tuple[CarriedLoss, ...] = field(repr=False)
    carried_left: tuple[CarriedLoss, ...] = field(repr=False)
    expiring: tuple[CarriedLoss, ...] = field(repr=False)
    expired: tuple[CarriedLoss, ...] = field(repr=False)


def net_savings_base(year: int, hermes_net: Decimal, taxpayer: TaxpayerYear) -> NettingResult:
    """Net the year's savings base with hermes' result added to the operator's.

    Args:
        year: Tax year.
        hermes_net: Hermes net gain of the year; negative is a net loss.
        taxpayer: The operator's other income and carried losses.

    Returns:
        The savings base and the carried-loss bookkeeping.
    """
    with exact():
        gains = hermes_net + taxpayer.other_capital_gains
        income = taxpayer.other_savings_income
        room = income * CROSS_OFFSET_SHARE
        if gains < _ZERO:
            offset = min(-gains, room)
            gains += offset
            income -= offset
            room -= offset

        oldest_usable = year - CARRY_FORWARD_YEARS
        usable = sorted(
            (c for c in taxpayer.carried_losses if oldest_usable <= c.origin_year < year),
            key=lambda c: c.origin_year,
        )
        expired = tuple(c for c in taxpayer.carried_losses if c.origin_year < oldest_usable)

        used: list[CarriedLoss] = []
        left: list[CarriedLoss] = []
        expiring: list[CarriedLoss] = []
        for carried in usable:
            remaining = carried.amount
            if gains > _ZERO:
                take = min(remaining, gains)
                gains -= take
                remaining -= take
            if remaining > _ZERO and room > _ZERO:
                take = min(remaining, room)
                income -= take
                room -= take
                remaining -= take
            if remaining < carried.amount:
                used.append(CarriedLoss(carried.origin_year, carried.amount - remaining))
            if remaining > _ZERO:
                bucket = expiring if carried.origin_year == oldest_usable else left
                bucket.append(CarriedLoss(carried.origin_year, remaining))

        if gains < _ZERO:
            left.append(CarriedLoss(year, -gains))
        base = max(gains, _ZERO) + max(income, _ZERO)
    return NettingResult(
        savings_base=base,
        carried_used=tuple(used),
        carried_left=tuple(left),
        expiring=tuple(expiring),
        expired=expired,
    )
