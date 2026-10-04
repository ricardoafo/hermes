"""Two-month repurchase flags (IRPF art. 33.5.f), flagged and never applied.

Art. 33.5.f defers a loss when homogeneous securities are bought within two
months before or after the sale. It is written for listed securities and crypto
is not one, so hermes only flags such sales for the gestor; the loss still
counts in full (ADR-016 decision 8).

A buy whose lot the sale itself consumed is not a repurchase and never flags.

Reference: ADR-016 decision 8, change ``tax-estimates`` D-4.
"""

from __future__ import annotations

import calendar
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Final
from zoneinfo import ZoneInfo

from hermes.domain.tax.year_position import Disposal

__all__ = [
    "REPURCHASE_WINDOW_MONTHS",
    "Acquisition",
    "TwoMonthFlag",
    "add_months",
    "two_month_flags",
]

REPURCHASE_WINDOW_MONTHS: Final[int] = 2
_MADRID: Final = ZoneInfo("Europe/Madrid")
_ZERO: Final[Decimal] = Decimal(0)


@dataclass(frozen=True, slots=True)
class Acquisition:
    """One buy of an asset.

    Attributes:
        asset: Asset bought.
        ref: Stable id of the buy (the ledger trade id).
        at: Time of the buy, timezone-aware.
    """

    asset: str
    ref: str
    at: datetime


@dataclass(frozen=True, slots=True)
class TwoMonthFlag:
    """A loss sale with a same-asset buy within two months either side.

    Attributes:
        asset: Asset sold and bought.
        sale_ref: Ref of the loss sale.
        sold_on: Madrid date of the sale.
        loss: The loss, as a positive magnitude.
        bought_on: Madrid date of the nearest flagged buy.
    """

    asset: str
    sale_ref: str
    sold_on: date
    loss: Decimal
    bought_on: date


def add_months(day: date, months: int) -> date:
    """Shift ``day`` by whole calendar months, clamping to the month's last day.

    Args:
        day: Start date.
        months: Months to add; negative goes back.

    Returns:
        The shifted date (31 March minus one month is 28 or 29 February).
    """
    index = day.year * 12 + (day.month - 1) + months
    year, month = divmod(index, 12)
    last = calendar.monthrange(year, month + 1)[1]
    return date(year, month + 1, min(day.day, last))


def two_month_flags(
    disposals: Iterable[Disposal], acquisitions: Iterable[Acquisition]
) -> tuple[TwoMonthFlag, ...]:
    """Flag every loss sale with a same-asset buy within two months either side.

    Args:
        disposals: Sales to check; only those with a negative gain can flag.
        acquisitions: Every buy known to the ledger, any asset.

    Returns:
        One flag per flagged sale, naming the buy nearest to it, in sale order.
    """
    buys = tuple(acquisitions)
    flags: list[TwoMonthFlag] = []
    for sale in sorted(disposals, key=lambda d: d.at):
        if sale.gain >= _ZERO:
            continue
        sold_on = sale.at.astimezone(_MADRID).date()
        earliest = add_months(sold_on, -REPURCHASE_WINDOW_MONTHS)
        latest = add_months(sold_on, REPURCHASE_WINDOW_MONTHS)
        in_window = [
            bought_on
            for buy in buys
            if buy.asset == sale.asset
            and buy.ref not in sale.consumed_refs
            and earliest <= (bought_on := buy.at.astimezone(_MADRID).date()) <= latest
        ]
        if in_window:
            nearest = min(in_window, key=lambda d: (abs((d - sold_on).days), d))
            flags.append(
                TwoMonthFlag(
                    asset=sale.asset,
                    sale_ref=sale.ref,
                    sold_on=sold_on,
                    loss=-sale.gain,
                    bought_on=nearest,
                )
            )
    return tuple(flags)
