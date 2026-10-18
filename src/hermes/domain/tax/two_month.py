"""Two-month repurchase flags (IRPF art. 33.5.f), flagged and never applied.

Art. 33.5.f defers a loss when homogeneous securities are bought within two
months before or after the sale. It is written for listed securities and crypto
is not one, so hermes only flags such sales for the gestor; the loss still
counts in full (ADR-016 decision 8).

A buy whose lot the sale itself consumed is not a repurchase and never flags.

Reference: ADR-016 decision 8, change ``tax-estimates`` D-4.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Final

from hermes.domain.tax.tax_calendar import add_months, madrid_date
from hermes.domain.tax.year_position import Disposal

__all__ = [
    "REPURCHASE_WINDOW_MONTHS",
    "Acquisition",
    "TwoMonthFlag",
    "add_months",
    "in_repurchase_window",
    "repurchase_window",
    "two_month_flags",
]

REPURCHASE_WINDOW_MONTHS: Final[int] = 2
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


def repurchase_window(sold_at: datetime) -> tuple[date, date]:
    """Return the first and last Madrid date a repurchase of a sale can fall on.

    The window is two calendar months either side of the sale's Madrid date,
    both ends included; the time of day never matters (Code Owner, 18 Oct:
    the rule counts Madrid calendar dates).

    Args:
        sold_at: Timezone-aware time of the sale.

    Returns:
        ``(earliest, latest)``, the day clamped to each month's end.
    """
    sold_on = madrid_date(sold_at)
    return (
        add_months(sold_on, -REPURCHASE_WINDOW_MONTHS),
        add_months(sold_on, REPURCHASE_WINDOW_MONTHS),
    )


def in_repurchase_window(sold_at: datetime, bought_at: datetime) -> bool:
    """Return whether a buy falls within the repurchase window of a sale.

    Args:
        sold_at: Timezone-aware time of the sale.
        bought_at: Timezone-aware time of the buy.

    Returns:
        True when the buy's Madrid date is within :func:`repurchase_window`.
    """
    earliest, latest = repurchase_window(sold_at)
    return earliest <= madrid_date(bought_at) <= latest


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
        sold_on = madrid_date(sale.at)
        in_window = [
            madrid_date(buy.at)
            for buy in buys
            if buy.asset == sale.asset
            and buy.ref not in sale.consumed_refs
            and in_repurchase_window(sale.at, buy.at)
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
