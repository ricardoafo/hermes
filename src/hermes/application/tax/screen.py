"""What the console's Tax screen shows, built from the tax estimate (issue #719).

The Tax screen draws figures; it never computes tax. :class:`TaxScreenService`
asks :class:`~hermes.application.tax.estimate.TaxService` for the year's
estimate and adds what the screen lists beside it, all from the same ledger
rows the estimate replays:

- each lot of the FIFO replay with the sales that used it (proceeds, cost,
  fees, gain) and whether the two-month rule flagged that sale;
- the year's trade fees;
- how far the taxable savings base fills each band of the savings scale.

Every money figure stays a ``Decimal``; rounding is the renderer's job. The
module does no I/O: the clock, the scales and the taxpayer figures are
parameters, so over ``--vps`` the caller simply passes no taxpayer figures.

Reference: openspec/changes/719-tax-screen/change.md.
"""

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, tzinfo
from decimal import Decimal
from typing import Final, Literal, Protocol

from returns.result import Success

from hermes.application.console.views import TaxPending
from hermes.application.tax.estimate import TaxService, TaxYearEstimate
from hermes.domain.lots.eur_fifo import EurTrade, exact_sum, replay_fifo_eur
from hermes.domain.tax.netting import TaxpayerYear
from hermes.domain.tax.precision import exact
from hermes.domain.tax.scale import SavingsScale
from hermes.domain.tax.year_position import tax_year_of
from hermes.domain.trading.order import OrderSide
from hermes.domain.value_objects.currency import Currency
from hermes.ports.ledger import EurTradeRow, TaxTradeSourcePort

__all__ = [
    "BracketFill",
    "LotSale",
    "LotView",
    "MonthSubtotal",
    "TaxScreen",
    "TaxScreenService",
    "TaxScreenSource",
    "TaxpayerProfileView",
    "bracket_fills",
    "build_lots",
    "month_subtotals",
    "year_fees",
]

_ZERO: Final[Decimal] = Decimal(0)
_HUNDRED: Final[Decimal] = Decimal(100)

type LotState = Literal["open", "closed"]


@dataclass(frozen=True, slots=True)
class LotSale:
    """One sale that drew on a lot: the part of the sale this lot covered.

    Attributes:
        ref: The sale's ledger ref.
        at: When the sale filled.
        qty: Quantity taken from this lot.
        proceeds: EUR proceeds of that quantity; ``None`` while pending.
        cost: EUR acquisition cost of that quantity; ``None`` while pending.
        fee: EUR fee share of that quantity; ``None`` while pending.
        gain: Proceeds minus cost, fees in; ``None`` while pending.
        two_month: The estimate flagged this sale under the two-month rule.
    """

    ref: str
    at: datetime
    qty: Decimal
    proceeds: Decimal | None
    cost: Decimal | None
    fee: Decimal | None
    gain: Decimal | None
    two_month: bool


@dataclass(frozen=True, slots=True)
class LotView:
    """One FIFO lot: a buy and the sales that used it.

    Attributes:
        ref: The buy's ledger ref.
        coin: Asset bought (``"BTC"``).
        bought_at: When the buy filled.
        qty: Quantity bought.
        remaining: Quantity still held.
        cost: EUR acquisition value of the whole buy, fees in; ``None`` while pending.
        fee: EUR fee of the buy; ``None`` while pending.
        sales: The sales that used the lot, oldest first.
    """

    ref: str
    coin: str
    bought_at: datetime
    qty: Decimal
    remaining: Decimal
    cost: Decimal | None
    fee: Decimal | None
    sales: tuple[LotSale, ...]

    @property
    def state(self) -> LotState:
        """Return ``open`` while any quantity is held, else ``closed``."""
        return "open" if self.remaining > _ZERO else "closed"

    @property
    def gain(self) -> Decimal | None:
        """Return the realised gain of the lot's sales; ``None`` when one is pending."""
        gains = [sale.gain for sale in self.sales]
        if any(gain is None for gain in gains):
            return None
        return exact_sum(gain for gain in gains if gain is not None)

    @property
    def sale_fees(self) -> Decimal | None:
        """Return the fees of the lot's sales; ``None`` when one is pending."""
        fees = [sale.fee for sale in self.sales]
        if any(fee is None for fee in fees):
            return None
        return exact_sum(fee for fee in fees if fee is not None)

    @property
    def two_month(self) -> bool:
        """Return whether any sale of the lot was flagged under the two-month rule."""
        return any(sale.two_month for sale in self.sales)


@dataclass(frozen=True, slots=True)
class BracketFill:
    """How much of one savings band the taxable base fills.

    Attributes:
        lower: The band's lower limit in EUR.
        upper: Its upper limit; ``None`` for the open top band.
        rate: Its rate in percent (``19``).
        filled: EUR of the base inside the band.
    """

    lower: Decimal
    upper: Decimal | None
    rate: Decimal
    filled: Decimal


@dataclass(frozen=True, slots=True)
class MonthSubtotal:
    """The lots bought in one month, totalled.

    Attributes:
        month: ``YYYY-MM``.
        lots: How many lots.
        cost: Their EUR cost; pending lots are left out.
        gain: Their realised gain; pending sales are left out.
    """

    month: str
    lots: int
    cost: Decimal
    gain: Decimal


@dataclass(frozen=True, slots=True)
class TaxScreen:
    """Everything the Tax screen shows for one tax year.

    Attributes:
        year: Tax year.
        estimate: The tax service's estimate; the source of every tax figure.
        fees: EUR fees of the year's trades.
        brackets: The savings bands and how far the taxable base fills each.
        lots: Every FIFO lot, oldest buy first.
    """

    year: int
    estimate: TaxYearEstimate
    fees: Decimal
    brackets: tuple[BracketFill, ...]
    lots: tuple[LotView, ...]


@dataclass(frozen=True, slots=True)
class TaxpayerProfileView:
    """What the Tax screen may say about the taxpayer profile.

    Over ``--vps`` the console never reads the profile: ``remote`` is set and
    ``figures`` stays empty, so only ``exists`` can show.

    Attributes:
        exists: A profile file is present.
        remote: The console runs on the VPS (``--vps``).
        figures: ``(label, EUR)`` per figure for the year, read on the PC only.
        error: Why the profile could not be read, without any value.
    """

    exists: bool
    remote: bool = False
    figures: tuple[tuple[str, Decimal], ...] = ()
    error: str | None = None


class TaxScreenSource(Protocol):
    """Where the console reads the Tax screen from."""

    def tax_screen(
        self, *, as_of: datetime, prices: Mapping[str, Decimal]
    ) -> TaxScreen | TaxPending:
        """Return the screen of ``as_of``'s tax year, or why there is none.

        Args:
            as_of: Now.
            prices: Booked EUR price per held coin, for "open if sold now".
        """
        ...


def _percent(rate: Decimal) -> Decimal:
    value = rate * _HUNDRED
    return value.quantize(Decimal(1)) if value == value.to_integral_value() else value


def bracket_fills(scale: SavingsScale, base: Decimal) -> tuple[BracketFill, ...]:
    """Return each band of ``scale`` with the part of ``base`` inside it.

    Args:
        scale: The year's savings scale.
        base: The taxable savings base; below zero fills nothing.

    Returns:
        One fill per band, lowest first.
    """
    fills: list[BracketFill] = []
    lower = _ZERO
    with exact():
        for band in scale.bands:
            top = base if band.up_to is None else min(base, band.up_to)
            filled = max(top - lower, _ZERO)
            fills.append(
                BracketFill(lower=lower, upper=band.up_to, rate=_percent(band.rate), filled=filled)
            )
            lower = band.up_to if band.up_to is not None else lower
    return tuple(fills)


def year_fees(trades: Iterable[EurTradeRow], year: int) -> Decimal:
    """Return the EUR fees of the trades filled in ``year``.

    A trade's quote leg repeats the trade's fee, so only base rows count.

    Args:
        trades: Ledger rows, any year.
        year: Tax year.

    Returns:
        The sum; pending fees are left out.
    """
    return exact_sum(
        row.fee_eur
        for row in trades
        if not row.quote_leg and row.fee_eur is not None and tax_year_of(row.executed_at) == year
    )


def build_lots(trades: Sequence[EurTradeRow], flagged: Iterable[str] = ()) -> tuple[LotView, ...]:
    """Replay the trades FIFO per asset and return every lot with its sales.

    Args:
        trades: Every ledger row the estimate replays.
        flagged: Sale refs the estimate flagged under the two-month rule.

    Returns:
        The lots, oldest buy first; an asset whose replay fails is left out
        (the estimate reports it as pending).
    """
    flagged_refs = frozenset(flagged)
    by_asset: dict[Currency, list[EurTradeRow]] = defaultdict(list)
    for row in sorted(trades, key=lambda t: t.executed_at):
        by_asset[row.asset].append(row)
    lots: list[LotView] = []
    for asset, rows in by_asset.items():
        replayed = replay_fifo_eur(
            asset,
            [
                EurTrade(
                    ref=row.ref,
                    side=row.side,
                    qty=row.qty,
                    executed_at=row.executed_at,
                    price_eur=row.price_eur,
                    fee_eur=row.fee_eur,
                )
                for row in rows
            ],
        )
        if not isinstance(replayed, Success):
            continue
        projection = replayed.unwrap()
        remaining = {lot.buy_ref: lot.remaining_qty for lot in projection.lots}
        acquisition = {lot.buy_ref: lot.acquisition_eur for lot in projection.lots}
        sales: dict[str, list[LotSale]] = defaultdict(list)
        for piece in projection.consumptions:
            sales[piece.buy_ref].append(
                LotSale(
                    ref=piece.sell_ref,
                    at=piece.consumed_at,
                    qty=piece.qty,
                    proceeds=piece.proceeds_eur,
                    cost=piece.cost_eur,
                    fee=piece.fee_eur,
                    gain=piece.gain_eur,
                    two_month=piece.sell_ref in flagged_refs,
                )
            )
        buys = [row for row in rows if row.side == OrderSide.BUY]
        lots.extend(
            LotView(
                ref=row.ref,
                coin=asset.value,
                bought_at=row.executed_at,
                qty=row.qty,
                remaining=remaining.get(row.ref, _ZERO),
                cost=acquisition.get(row.ref),
                fee=row.fee_eur,
                sales=tuple(sales.get(row.ref, ())),
            )
            for row in buys
        )
    return tuple(sorted(lots, key=lambda lot: (lot.bought_at, lot.coin, lot.ref)))


def month_subtotals(lots: Iterable[LotView], tz: tzinfo | None = None) -> tuple[MonthSubtotal, ...]:
    """Total lots per month of their buy, newest month first.

    Args:
        lots: The lots to total (a filtered list totals only what is shown).
        tz: Timezone of the month; ``None`` keeps each buy's own offset.

    Returns:
        One subtotal per month with at least one lot.
    """
    groups: dict[str, list[LotView]] = defaultdict(list)
    for lot in lots:
        at = lot.bought_at if tz is None else lot.bought_at.astimezone(tz)
        groups[f"{at:%Y-%m}"].append(lot)
    out: list[MonthSubtotal] = []
    for month in sorted(groups, reverse=True):
        members = groups[month]
        out.append(
            MonthSubtotal(
                month=month,
                lots=len(members),
                cost=exact_sum(lot.cost for lot in members if lot.cost is not None),
                gain=exact_sum(
                    gain for lot in members if (gain := lot.gain) is not None and lot.sales
                ),
            )
        )
    return tuple(out)


class TaxScreenService:
    """Builds the Tax screen from the ledger through the tax service.

    Args:
        source: The ledger's tax read port.
        scales: Savings scale per tax year.
        taxpayer: The operator's figures for a year, or ``None`` (no profile,
            or a console run over ``--vps``, which never reads them).
        default_personal_minimum: Minimum used when there are no figures.
        is_shadow: Whether the trades are shadow trades.
        service: The tax service; one over ``source`` by default.
    """

    def __init__(
        self,
        source: TaxTradeSourcePort,
        *,
        scales: Mapping[int, SavingsScale],
        taxpayer: Mapping[int, TaxpayerYear],
        default_personal_minimum: Decimal,
        is_shadow: bool = True,
        service: TaxService | None = None,
    ) -> None:
        """Bind the sources and the year-independent inputs."""
        self._source = source
        self._scales = scales
        self._taxpayer = taxpayer
        self._minimum = default_personal_minimum
        self._is_shadow = is_shadow
        self._service = service if service is not None else TaxService(source)

    def tax_screen(
        self, *, as_of: datetime, prices: Mapping[str, Decimal]
    ) -> TaxScreen | TaxPending:
        """Return the screen of ``as_of``'s tax year.

        Args:
            as_of: Now.
            prices: Booked EUR price per held coin; a held coin without one
                leaves "open if sold now" pending.

        Returns:
            The screen, or the estimate's :class:`TaxPending`.

        Raises:
            LedgerError: When the ledger cannot be read.
        """
        year = tax_year_of(as_of)
        estimate = self._service.estimate(
            year=year,
            scales=self._scales,
            taxpayer=self._taxpayer.get(year),
            default_personal_minimum=self._minimum,
            prices=prices,
            as_of=as_of,
            is_shadow=self._is_shadow,
        )
        if isinstance(estimate, TaxPending):
            return estimate
        trades = self._source.list_eur_trades()
        return TaxScreen(
            year=year,
            estimate=estimate,
            fees=year_fees(trades, year),
            brackets=bracket_fills(self._scales[year], estimate.savings_base),
            lots=build_lots(trades, (flag.sale_ref for flag in estimate.two_month)),
        )
