"""Modelo 100: gains and losses from transmissions of virtual currencies.

Builds, for one tax year, what the Renta Web section on virtual-currency
transmissions asks for, from the FIFO consumptions the ledger already stores
(ADR-016 item 3, IRPF art. 35):

- transmission value of a sale = Σ (proceeds share - sell fee share)
- acquisition value of a sale = Σ cost share of the lots consumed, buy fee included
- gain = transmission value - acquisition value

One :class:`Transmission` per SELL, one :class:`CoinSummary` per coin and
kind of consideration (the lines typed into Renta Web), and the year's
gains, losses and net. A sale with any pending EUR value is listed in
``pending`` and left out of every total: the year is then incomplete.

A loss sale with a purchase of the same coin within two calendar months
either side is flagged for the gestor, never deferred (ADR-016 item 8).

Reference: openspec/changes/36-modelo-100-721/change.md D-1 to D-6.
"""

from __future__ import annotations

import calendar
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Final

from hermes.domain.filing.tax_year import tax_year_of
from hermes.domain.lots.eur_fifo import exact_sum
from hermes.domain.value_objects.currency import Currency

__all__ = [
    "CoinSummary",
    "ConsiderationKind",
    "DisposalPiece",
    "Modelo100Report",
    "PendingSale",
    "PurchaseRecord",
    "RepurchaseFlag",
    "Transmission",
    "add_months",
    "build_modelo100",
]

_ZERO: Final[Decimal] = Decimal("0")
_REPURCHASE_WINDOW_MONTHS: Final[int] = 2
_FIAT: Final[frozenset[Currency]] = frozenset({Currency.EUR})


class ConsiderationKind(StrEnum):
    """What the seller received in exchange for the coin."""

    FIAT = "fiat currency"
    VIRTUAL_CURRENCY = "other virtual currency"

    @classmethod
    def of(cls, received: Currency) -> ConsiderationKind:
        """Return the kind of consideration a sale into ``received`` is.

        Stablecoins are virtual currencies, not legal tender, so a sale for
        USDT is a swap into another virtual currency (DGT V0999-18).
        """
        return cls.FIAT if received in _FIAT else cls.VIRTUAL_CURRENCY


@dataclass(frozen=True, slots=True)
class DisposalPiece:
    """One stored FIFO consumption: part of a sale drawn from one lot.

    Attributes:
        sell_ref: Ledger id of the SELL trade.
        asset: Coin sold.
        received: Currency received (the SELL pair's quote).
        disposed_at: Fill time of the SELL.
        buy_ref: Ledger id of the BUY that opened the lot.
        acquired_at: Opening time of the lot.
        qty: Quantity drawn from the lot.
        proceeds_eur: Share of the sale's gross EUR proceeds; ``None`` while pending.
        cost_eur: Share of the lot's EUR acquisition value; ``None`` while pending.
        fee_eur: Share of the sale's EUR fee; ``None`` while pending.
    """

    sell_ref: str
    asset: Currency
    received: Currency
    disposed_at: datetime
    buy_ref: str
    acquired_at: datetime
    qty: Decimal
    proceeds_eur: Decimal | None
    cost_eur: Decimal | None
    fee_eur: Decimal | None

    def __post_init__(self) -> None:
        """Reject naive times and a non-positive quantity.

        Raises:
            ValueError: On a violated invariant.
        """
        if self.disposed_at.tzinfo is None or self.acquired_at.tzinfo is None:
            raise ValueError("DisposalPiece times must be timezone-aware")
        if self.qty <= _ZERO:
            raise ValueError(f"DisposalPiece.qty must be > 0, got {self.qty}")

    @property
    def priced(self) -> bool:
        """Return whether every EUR value of the piece is known."""
        return None not in (self.proceeds_eur, self.cost_eur, self.fee_eur)


@dataclass(frozen=True, slots=True)
class PurchaseRecord:
    """A BUY, for the two-month repurchase check.

    Attributes:
        ref: Ledger id of the BUY.
        asset: Coin bought.
        acquired_at: Fill time.
    """

    ref: str
    asset: Currency
    acquired_at: datetime


@dataclass(frozen=True, slots=True)
class Transmission:
    """One sale, valued for Modelo 100.

    Attributes:
        sell_ref: Ledger id of the SELL.
        asset: Coin sold.
        kind: Kind of consideration received.
        received: Currency received.
        disposed_at: Fill time.
        qty: Quantity sold.
        transmission_eur: Proceeds net of the sell fee.
        acquisition_eur: Cost of the lots consumed, buy fees included.
        gain_eur: ``transmission_eur - acquisition_eur``; negative for a loss.
        first_acquired_at: Opening time of the oldest lot consumed.
        buy_refs: Ledger ids of the BUYs whose lots the sale consumed.
    """

    sell_ref: str
    asset: Currency
    kind: ConsiderationKind
    received: Currency
    disposed_at: datetime
    qty: Decimal
    transmission_eur: Decimal
    acquisition_eur: Decimal
    gain_eur: Decimal
    first_acquired_at: datetime
    buy_refs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PendingSale:
    """A sale in the year whose EUR values are not all known yet.

    Attributes:
        sell_ref: Ledger id of the SELL.
        asset: Coin sold.
        disposed_at: Fill time.
        qty: Quantity sold.
    """

    sell_ref: str
    asset: Currency
    disposed_at: datetime
    qty: Decimal


@dataclass(frozen=True, slots=True)
class CoinSummary:
    """One Renta Web line: all of a year's sales of one coin for one kind of consideration.

    Attributes:
        asset: Coin.
        kind: Kind of consideration.
        sales: Number of sales aggregated.
        qty: Total quantity sold.
        transmission_eur: Sum of transmission values.
        acquisition_eur: Sum of acquisition values.
        gain_eur: Sum of gains; negative for a net loss.
    """

    asset: Currency
    kind: ConsiderationKind
    sales: int
    qty: Decimal
    transmission_eur: Decimal
    acquisition_eur: Decimal
    gain_eur: Decimal


@dataclass(frozen=True, slots=True)
class RepurchaseFlag:
    """A loss sale with a same-coin purchase within two months either side.

    Attributes:
        sell_ref: Ledger id of the loss sale.
        asset: Coin.
        disposed_at: Fill time of the sale.
        loss_eur: The loss, as a positive amount.
        purchase_refs: Ledger ids of the purchases inside the window.
    """

    sell_ref: str
    asset: Currency
    disposed_at: datetime
    loss_eur: Decimal
    purchase_refs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Modelo100Report:
    """The year's virtual-currency transmissions.

    Attributes:
        year: Tax year.
        transmissions: Fully priced sales, in fill order.
        summaries: One line per coin and kind of consideration, by coin then kind.
        pending: Sales left out because an EUR value is pending.
        gains_eur: Sum of the positive gains.
        losses_eur: Sum of the losses, as a positive amount.
        flags: Loss sales the gestor should check against art. 33.5.f.
    """

    year: int
    transmissions: tuple[Transmission, ...]
    summaries: tuple[CoinSummary, ...]
    pending: tuple[PendingSale, ...]
    gains_eur: Decimal
    losses_eur: Decimal
    flags: tuple[RepurchaseFlag, ...]

    @property
    def net_eur(self) -> Decimal:
        """Return gains minus losses."""
        return self.gains_eur - self.losses_eur

    @property
    def complete(self) -> bool:
        """Return whether no sale of the year is pending."""
        return not self.pending


def add_months(at: datetime, months: int) -> datetime:
    """Shift ``at`` by whole calendar months, clamping the day to the month's end.

    Args:
        at: The instant to shift.
        months: Months to add; negative moves back.

    Returns:
        The shifted instant, same time of day and timezone.
    """
    index = at.year * 12 + at.month - 1 + months
    year, month = divmod(index, 12)
    last_day = calendar.monthrange(year, month + 1)[1]
    return at.replace(year=year, month=month + 1, day=min(at.day, last_day))


def build_modelo100(
    year: int,
    pieces: Iterable[DisposalPiece],
    purchases: Iterable[PurchaseRecord] = (),
) -> Modelo100Report:
    """Build the Modelo 100 figures of one tax year.

    Args:
        year: Tax year (Madrid calendar year).
        pieces: Stored consumptions; those outside ``year`` are ignored.
        purchases: BUYs around the year, for the repurchase flag. Pass at
            least those within two months of the year's bounds.

    Returns:
        The report.
    """
    by_sale: dict[str, list[DisposalPiece]] = defaultdict(list)
    for piece in pieces:
        if tax_year_of(piece.disposed_at) == year:
            by_sale[piece.sell_ref].append(piece)

    transmissions: list[Transmission] = []
    pending: list[PendingSale] = []
    for sale in by_sale.values():
        first = sale[0]
        if all(piece.priced for piece in sale):
            transmissions.append(_transmission(sale))
        else:
            pending.append(
                PendingSale(
                    sell_ref=first.sell_ref,
                    asset=first.asset,
                    disposed_at=first.disposed_at,
                    qty=exact_sum(piece.qty for piece in sale),
                )
            )
    transmissions.sort(key=lambda t: (t.disposed_at, t.sell_ref))
    pending.sort(key=lambda p: (p.disposed_at, p.sell_ref))

    gains = exact_sum(t.gain_eur for t in transmissions if t.gain_eur > _ZERO)
    losses = -exact_sum(t.gain_eur for t in transmissions if t.gain_eur < _ZERO)
    return Modelo100Report(
        year=year,
        transmissions=tuple(transmissions),
        summaries=_summaries(transmissions),
        pending=tuple(pending),
        gains_eur=gains,
        losses_eur=losses,
        flags=_repurchase_flags(transmissions, tuple(purchases)),
    )


def _transmission(sale: Sequence[DisposalPiece]) -> Transmission:
    first = sale[0]
    proceeds = exact_sum(p.proceeds_eur for p in sale if p.proceeds_eur is not None)
    fees = exact_sum(p.fee_eur for p in sale if p.fee_eur is not None)
    acquisition = exact_sum(p.cost_eur for p in sale if p.cost_eur is not None)
    transmission = exact_sum((proceeds, -fees))
    return Transmission(
        sell_ref=first.sell_ref,
        asset=first.asset,
        kind=ConsiderationKind.of(first.received),
        received=first.received,
        disposed_at=first.disposed_at,
        qty=exact_sum(p.qty for p in sale),
        transmission_eur=transmission,
        acquisition_eur=acquisition,
        gain_eur=exact_sum((transmission, -acquisition)),
        first_acquired_at=min(p.acquired_at for p in sale),
        buy_refs=tuple(dict.fromkeys(p.buy_ref for p in sale)),
    )


def _summaries(transmissions: Sequence[Transmission]) -> tuple[CoinSummary, ...]:
    groups: dict[tuple[Currency, ConsiderationKind], list[Transmission]] = defaultdict(list)
    for t in transmissions:
        groups[(t.asset, t.kind)].append(t)
    return tuple(
        CoinSummary(
            asset=asset,
            kind=kind,
            sales=len(group),
            qty=exact_sum(t.qty for t in group),
            transmission_eur=exact_sum(t.transmission_eur for t in group),
            acquisition_eur=exact_sum(t.acquisition_eur for t in group),
            gain_eur=exact_sum(t.gain_eur for t in group),
        )
        for (asset, kind), group in sorted(groups.items(), key=lambda kv: (kv[0][0], kv[0][1]))
    )


def _repurchase_flags(
    transmissions: Sequence[Transmission], purchases: Sequence[PurchaseRecord]
) -> tuple[RepurchaseFlag, ...]:
    flags: list[RepurchaseFlag] = []
    for t in transmissions:
        if t.gain_eur >= _ZERO:
            continue
        start = add_months(t.disposed_at, -_REPURCHASE_WINDOW_MONTHS)
        end = add_months(t.disposed_at, _REPURCHASE_WINDOW_MONTHS)
        # The lots the sale itself consumed are what was sold, not a repurchase.
        inside = tuple(
            p.ref
            for p in sorted(purchases, key=lambda p: (p.acquired_at, p.ref))
            if p.asset == t.asset and p.ref not in t.buy_refs and start <= p.acquired_at <= end
        )
        if inside:
            flags.append(
                RepurchaseFlag(
                    sell_ref=t.sell_ref,
                    asset=t.asset,
                    disposed_at=t.disposed_at,
                    loss_eur=-t.gain_eur,
                    purchase_refs=inside,
                )
            )
    return tuple(flags)
