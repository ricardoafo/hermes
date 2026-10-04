"""Yearly tax estimate of hermes gains, built from the ledger's EUR trades.

:func:`estimate_year` replays the trades FIFO per asset (fees in the values,
ADR-016 decision 3), folds each sale into one transmission, and computes for
one Madrid tax year:

- the hermes tax: marginal over the taxpayer profile (decision 7);
- today's tax: the year's hermes tax after today minus before today;
- tax if sold now: the marginal tax of selling every open lot at the given
  EUR prices, split per asset pro rata to unrealised gain;
- carried-loss bookkeeping and two-month flags (decision 8);
- optionally the total IRPF view (decision 11), built from the hermes figures
  and never feeding back into them.

Every figure is a console :class:`TaxEstimate` or :class:`TaxPending`, so the
read model can place it straight into its views. Any EUR-pending trade in the
year, or a missing scale, turns the whole estimate into :class:`TaxPending`
(change ``tax-estimates`` D-5). The module does no I/O: prices and the clock
are parameters.

Measurement only (decision 9): trading code never imports this package.

Reference: ADR-016, change ``tax-estimates``.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Final, Literal
from zoneinfo import ZoneInfo

from returns.result import Failure

from hermes.application.console.views import Tax, TaxBand, TaxEstimate, TaxPending
from hermes.domain.lots.eur_fifo import (
    ConsumptionEurValue,
    EurTrade,
    FifoEurProjection,
    exact_sum,
    replay_fifo_eur,
)
from hermes.domain.tax.marginal import marginal_tax
from hermes.domain.tax.netting import CarriedLoss, TaxpayerYear
from hermes.domain.tax.precision import exact
from hermes.domain.tax.scale import SavingsScale
from hermes.domain.tax.total import GeneralScales, TotalIrpf, TotalIrpfUnavailable, total_irpf
from hermes.domain.tax.two_month import Acquisition, TwoMonthFlag, two_month_flags
from hermes.domain.tax.year_position import Disposal, YearPosition, tax_year_of
from hermes.domain.trading.order import OrderSide
from hermes.domain.value_objects.currency import Currency
from hermes.ports.ledger import EurTradeRow, TaxTradeSourcePort

__all__ = [
    "ESTIMATE_LABEL",
    "NO_PROFILE_NOTICE",
    "SHADOW_LABEL",
    "PositionTax",
    "TaxService",
    "TaxYearEstimate",
    "estimate_year",
]

ESTIMATE_LABEL: Final[str] = "estimate"
SHADOW_LABEL: Final[str] = "shadow (no real tax due)"
NO_PROFILE_NOTICE: Final[str] = "no taxpayer profile, assuming no other income"

_MADRID: Final = ZoneInfo("Europe/Madrid")
_ZERO: Final[Decimal] = Decimal(0)
_CENT: Final[Decimal] = Decimal("0.01")
_HUNDRED: Final[Decimal] = Decimal(100)


@dataclass(frozen=True, slots=True)
class PositionTax:
    """Tax if one open position were sold now.

    Attributes:
        asset: Base asset held.
        unrealised_gain: EUR gain if sold at the given price, fees in; ``None`` when unknown.
        tax_if_sold_now: Its pro-rata share of the total; negative lowers the total.
    """

    asset: str
    unrealised_gain: Decimal | None
    tax_if_sold_now: Tax


@dataclass(frozen=True, slots=True)
class TaxYearEstimate:
    """Every tax figure of one tax year, labelled an estimate.

    Attributes:
        year: Tax year (Madrid calendar year).
        realised_gain: Hermes net realised gain of the year, fees included.
        hermes_tax: Marginal tax of the year's hermes gains; negative is a credit.
        today_tax: Part of ``hermes_tax`` that today's sales added; negative is a credit.
        tax_if_sold_now: Extra tax if every open position were sold now, or pending.
        per_position_if_sold_now: The same figure split per asset.
        carry_forward_used: Carried losses applied this year (derived from the profile).
        carry_forward_left: Losses usable next year, oldest first.
        band: Band the next euro of hermes gain falls in.
        flags: Items for the gestor (two-month rule, expiring losses).
        notices: Assumptions the figure rests on (e.g. no profile).
        is_shadow: The trades are shadow trades; no real tax is due.
        total_irpf: The total IRPF view, why it is unavailable, or ``None`` when off.
        is_estimate: Always ``True``: never a filing (ADR-016 decision 10).
    """

    year: int
    realised_gain: Decimal
    hermes_tax: TaxEstimate
    today_tax: TaxEstimate
    tax_if_sold_now: Tax
    per_position_if_sold_now: tuple[PositionTax, ...]
    carry_forward_used: Decimal = field(repr=False)
    carry_forward_left: tuple[CarriedLoss, ...] = field(repr=False)
    band: TaxBand
    flags: tuple[str, ...]
    notices: tuple[str, ...]
    is_shadow: bool
    total_irpf: TotalIrpf | TotalIrpfUnavailable | None = None
    is_estimate: Literal[True] = True

    @property
    def label(self) -> str:
        """Return the label every rendering of the figures must carry."""
        return f"{ESTIMATE_LABEL} · {SHADOW_LABEL}" if self.is_shadow else ESTIMATE_LABEL


def _pending(reason: str) -> TaxPending:
    return TaxPending(label=f"tax pending: {reason}")


@dataclass(frozen=True, slots=True)
class _Replay:
    disposals: tuple[Disposal, ...]
    pending_disposal_years: frozenset[int]
    projections: Mapping[Currency, FifoEurProjection]
    buy_qty: Mapping[str, Decimal]
    acquisitions: tuple[Acquisition, ...]


def _replay(trades: Sequence[EurTradeRow]) -> _Replay | TaxPending:
    by_asset: dict[Currency, list[EurTrade]] = defaultdict(list)
    for row in sorted(trades, key=lambda t: (t.executed_at, t.trade_id)):
        by_asset[row.asset].append(
            EurTrade(
                ref=str(row.trade_id),
                side=row.side,
                qty=row.qty,
                executed_at=row.executed_at,
                price_eur=row.price_eur,
                fee_eur=row.fee_eur,
            )
        )
    disposals: list[Disposal] = []
    pending_years: set[int] = set()
    projections: dict[Currency, FifoEurProjection] = {}
    for asset, asset_trades in by_asset.items():
        replayed = replay_fifo_eur(asset, asset_trades)
        if isinstance(replayed, Failure):
            return _pending(f"ledger replay failed for {asset.value}")
        projection = replayed.unwrap()
        projections[asset] = projection
        sales: dict[str, list[ConsumptionEurValue]] = defaultdict(list)
        for piece in projection.consumptions:
            sales[piece.sell_ref].append(piece)
        for ref, pieces in sales.items():
            gains = [piece.gain_eur for piece in pieces]
            sold_at = pieces[0].consumed_at
            if any(gain is None for gain in gains):
                pending_years.add(tax_year_of(sold_at))
                continue
            disposals.append(
                Disposal(
                    asset=asset.value,
                    ref=ref,
                    at=sold_at,
                    gain=exact_sum(gain for gain in gains if gain is not None),
                    consumed_refs=frozenset(piece.buy_ref for piece in pieces),
                )
            )
    buys = [row for row in trades if row.side == OrderSide.BUY]
    return _Replay(
        disposals=tuple(disposals),
        pending_disposal_years=frozenset(pending_years),
        projections=projections,
        buy_qty={str(row.trade_id): row.qty for row in buys},
        acquisitions=tuple(
            Acquisition(asset=row.asset.value, ref=str(row.trade_id), at=row.executed_at)
            for row in buys
        ),
    )


def estimate_year(
    trades: Sequence[EurTradeRow],
    *,
    year: int,
    scales: Mapping[int, SavingsScale],
    taxpayer: TaxpayerYear | None,
    default_personal_minimum: Decimal,
    prices: Mapping[str, Decimal],
    as_of: datetime,
    is_shadow: bool,
    general_scales: GeneralScales | TotalIrpfUnavailable | None = None,
) -> TaxYearEstimate | TaxPending:
    """Estimate the hermes tax of ``year`` from the ledger's EUR trades.

    Args:
        trades: Every trade the FIFO projection replays (all assets, any year).
        year: Tax year to estimate.
        scales: Savings scale per tax year, from config.
        taxpayer: The operator's figures for ``year``; ``None`` when there is no
            profile (zeros, the default minimum, and a notice).
        default_personal_minimum: Personal minimum used when ``taxpayer`` is ``None``.
        prices: Current EUR price per asset (``"BTC"``), net of selling costs.
        as_of: Now; fixes "today" (Madrid) and whether ``year`` is current.
        is_shadow: Whether the trades are shadow trades.
        general_scales: The year's general-base scales when the total IRPF view
            is on, why it is unavailable, or ``None`` when the view is off.

    Returns:
        The estimate, or :class:`TaxPending` naming why there is none.
    """
    scale = scales.get(year)
    if scale is None:
        return _pending(f"scale missing for {year}")
    unpriced = sum(1 for t in trades if t.price_eur is None and tax_year_of(t.executed_at) == year)
    if unpriced:
        return _pending(f"EUR values pending for {unpriced} trade(s) in {year}")
    replay = _replay(trades)
    if isinstance(replay, TaxPending):
        return replay
    if year in replay.pending_disposal_years:
        return _pending(f"EUR values pending for a lot sold in {year}")

    notices: tuple[str, ...] = ()
    if taxpayer is None:
        taxpayer = TaxpayerYear(personal_minimum=default_personal_minimum)
        notices = (NO_PROFILE_NOTICE,)

    def hermes_tax_through(cut: datetime | None) -> Decimal:
        sold = (d for d in replay.disposals if cut is None or d.at < cut)
        net = YearPosition.of(year, sold).net
        return marginal_tax(scale, taxpayer, net, year=year).hermes_tax

    realised = YearPosition.of(year, replay.disposals).net
    marginal = marginal_tax(scale, taxpayer, realised, year=year)
    netting = marginal.with_hermes.netting
    is_current = tax_year_of(as_of) == year

    today_tax = _ZERO
    if is_current:
        today_start = datetime.combine(as_of.astimezone(_MADRID).date(), time(0), tzinfo=_MADRID)
        tomorrow = today_start.date() + timedelta(days=1)
        today_end = datetime.combine(tomorrow, time(0), tzinfo=_MADRID)
        with exact():
            today_tax = hermes_tax_through(today_end) - hermes_tax_through(today_start)

    if_sold, per_position = _if_sold_now(
        replay,
        scale=scale,
        taxpayer=taxpayer,
        realised=realised,
        year=year,
        prices=prices,
        hermes_tax=marginal.hermes_tax,
        is_current=is_current,
    )
    flags = _flags(
        replay.disposals,
        replay.acquisitions,
        year=year,
        expiring=netting.expiring,
        expired=netting.expired,
    )
    return TaxYearEstimate(
        year=year,
        realised_gain=realised,
        hermes_tax=TaxEstimate(amount=marginal.hermes_tax),
        today_tax=TaxEstimate(amount=today_tax),
        tax_if_sold_now=if_sold,
        per_position_if_sold_now=per_position,
        carry_forward_used=exact_sum(c.amount for c in netting.carried_used),
        carry_forward_left=netting.carried_left,
        band=_band(scale, marginal.with_hermes.taxable),
        flags=flags,
        notices=notices,
        is_shadow=is_shadow,
        total_irpf=(
            total_irpf(general_scales, taxpayer, marginal)
            if isinstance(general_scales, GeneralScales)
            else general_scales
        ),
    )


def _if_sold_now(
    replay: _Replay,
    *,
    scale: SavingsScale,
    taxpayer: TaxpayerYear,
    realised: Decimal,
    year: int,
    prices: Mapping[str, Decimal],
    hermes_tax: Decimal,
    is_current: bool,
) -> tuple[Tax, tuple[PositionTax, ...]]:
    if not is_current:
        return _pending("not the current tax year"), ()
    open_assets: list[tuple[str, Decimal | None]] = []
    reason: str | None = None
    with exact():
        for asset, projection in replay.projections.items():
            lots = [lot for lot in projection.lots if lot.remaining_qty > _ZERO]
            if not lots:
                continue
            price = prices.get(asset.value)
            costs = [
                None
                if lot.acquisition_eur is None
                else lot.acquisition_eur * lot.remaining_qty / replay.buy_qty[lot.buy_ref]
                for lot in lots
            ]
            if price is None:
                reason = reason or f"no price for {asset.value}"
                open_assets.append((asset.value, None))
            elif any(cost is None for cost in costs):
                reason = reason or f"EUR values pending for an open {asset.value} lot"
                open_assets.append((asset.value, None))
            else:
                held = sum((lot.remaining_qty for lot in lots), start=_ZERO)
                cost = exact_sum(c for c in costs if c is not None)
                open_assets.append((asset.value, price * held - cost))
    if not open_assets:
        return TaxEstimate(amount=_ZERO), ()
    if reason is not None:
        pending = _pending(reason)
        return pending, tuple(PositionTax(a, gain, pending) for a, gain in open_assets)

    gains = [gain for _, gain in open_assets if gain is not None]
    unrealised = exact_sum(gains)
    with exact():
        total = (
            marginal_tax(scale, taxpayer, realised + unrealised, year=year).hermes_tax - hermes_tax
        )
    shares = _pro_rata(total, gains, unrealised)
    positions = tuple(
        PositionTax(asset, gain, TaxEstimate(amount=share))
        for (asset, gain), share in zip(open_assets, shares, strict=True)
    )
    return TaxEstimate(amount=total), positions


def _pro_rata(total: Decimal, parts: Sequence[Decimal], whole: Decimal) -> list[Decimal]:
    """Split ``total`` by ``parts / whole``; the last share takes the rest so the sum is exact."""
    if whole == _ZERO:
        return [_ZERO] * len(parts)
    shares: list[Decimal] = []
    with exact():
        for index, part in enumerate(parts):
            if index == len(parts) - 1:
                shares.append(total - exact_sum(shares))
            else:
                shares.append(total * part / whole)
    return shares


def _percent(rate: Decimal) -> Decimal:
    value = rate * _HUNDRED
    return value.quantize(Decimal(1)) if value == value.to_integral_value() else value


def _band(scale: SavingsScale, taxable: Decimal) -> TaxBand:
    position = scale.band_at(taxable)
    return TaxBand(
        rate=_percent(position.rate),
        next_rate=None if position.next_rate is None else _percent(position.next_rate),
        room_to_next=position.room_to_next,
    )


def _cents(amount: Decimal) -> Decimal:
    return amount.quantize(_CENT, rounding=ROUND_HALF_UP)


def _describe(flag: TwoMonthFlag) -> str:
    return (
        f"{flag.asset} sold at a loss of {_cents(flag.loss)} EUR on {flag.sold_on} with a "
        f"{flag.asset} buy on {flag.bought_on}: two-month rule flagged for the gestor, "
        "loss not deferred"
    )


def _flags(
    disposals: Iterable[Disposal],
    acquisitions: Iterable[Acquisition],
    *,
    year: int,
    expiring: Iterable[CarriedLoss],
    expired: Iterable[CarriedLoss],
) -> tuple[str, ...]:
    in_year = [d for d in disposals if d.tax_year == year]
    lines = [_describe(flag) for flag in two_month_flags(in_year, acquisitions)]
    lines.extend(
        f"a carried loss from {loss.origin_year} expires unused at the end of {year}"
        for loss in expiring
    )
    lines.extend(
        f"a carried loss from {loss.origin_year} is older than 4 years and was ignored"
        for loss in expired
    )
    return tuple(lines)


class TaxService:
    """Reads the ledger's EUR trades and estimates a tax year.

    Args:
        source: The trade ledger's tax read port.
    """

    def __init__(self, source: TaxTradeSourcePort) -> None:
        """Bind to the ledger read port."""
        self._source = source

    def estimate(
        self,
        *,
        year: int,
        scales: Mapping[int, SavingsScale],
        taxpayer: TaxpayerYear | None,
        default_personal_minimum: Decimal,
        prices: Mapping[str, Decimal],
        as_of: datetime,
        is_shadow: bool,
        general_scales: GeneralScales | TotalIrpfUnavailable | None = None,
    ) -> TaxYearEstimate | TaxPending:
        """Estimate ``year`` from every trade in the ledger.

        Args:
            year: Tax year to estimate.
            scales: Savings scale per tax year.
            taxpayer: The operator's figures for ``year``, or ``None``.
            default_personal_minimum: Minimum used when ``taxpayer`` is ``None``.
            prices: Current EUR price per asset, net of selling costs.
            as_of: Now.
            is_shadow: Whether the trades are shadow trades.
            general_scales: General-base scales for the total IRPF view, why it
                is unavailable, or ``None`` when off.

        Returns:
            The estimate, or :class:`TaxPending` naming why there is none.

        Raises:
            LedgerError: When the ledger cannot be read.
        """
        return estimate_year(
            self._source.list_eur_trades(),
            year=year,
            scales=scales,
            taxpayer=taxpayer,
            default_personal_minimum=default_personal_minimum,
            prices=prices,
            as_of=as_of,
            is_shadow=is_shadow,
            general_scales=general_scales,
        )
