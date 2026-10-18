"""Build the Modelo 100 and Modelo 721 figures of one tax year.

Reads the ledger through :class:`~hermes.ports.filing.FilingLedgerReaderPort`
and values year-end holdings (D-8 of the change):

1. an operator override (``--price COIN=EUR``) wins;
2. else the last stored bar close in USDT at or before the year's end,
   divided by the ECB EUR/USD rate for 31 December (USDT read as USD,
   ADR-016 item 5), through the same :class:`EurPricer` the ledger uses;
3. else the price is missing and the 721 may be undetermined.

Reference: openspec/archive/36-modelo-100-721/change.md.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, localcontext
from typing import Final

from returns.result import Failure, Result, Success

from hermes.application.ledger.eur_pricer import EurPricer
from hermes.domain.errors import DomainError
from hermes.domain.filing.modelo100 import Modelo100Report, add_months, build_modelo100
from hermes.domain.filing.modelo721 import (
    Modelo721Report,
    Modelo721Rules,
    PreviousDeclaration,
    YearEndPosition,
    build_modelo721,
    year_end_positions,
)
from hermes.domain.filing.tax_year import tax_year_bounds
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair
from hermes.ports.filing import FilingLedgerReaderPort

__all__ = ["Custodian", "FilingPack", "YearEndPrice", "build_filing_pack"]

_PRECISION: Final[int] = 100
_VALUATION_QUOTE: Final[Currency] = Currency.USDT
_REPURCHASE_MARGIN_MONTHS: Final[int] = 2


@dataclass(frozen=True, slots=True)
class Custodian:
    """Who holds the coins, as the 721 asks.

    Attributes:
        name: Entity name.
        country: ISO country code, or ``None`` until the gestor confirms it.
    """

    name: str
    country: str | None


@dataclass(frozen=True, slots=True)
class YearEndPrice:
    """How one coin was valued on 31 December.

    Attributes:
        asset: Coin.
        price_eur: EUR value of one unit, or ``None`` when missing.
        source: Where the value came from, in words.
    """

    asset: Currency
    price_eur: Decimal | None
    source: str


@dataclass(frozen=True, slots=True)
class FilingPack:
    """Everything the report shows for one tax year.

    Attributes:
        year: Tax year.
        modelo100: Transmissions report.
        modelo721: Holdings report.
        prices: Year-end valuation per coin held.
        custodian: Who holds the coins.
        shadow: True when the ledger holds simulated fills.
    """

    year: int
    modelo100: Modelo100Report
    modelo721: Modelo721Report
    prices: tuple[YearEndPrice, ...]
    custodian: Custodian
    shadow: bool

    @property
    def complete(self) -> bool:
        """Return whether no sale is pending and every coin held has a price."""
        return self.modelo100.complete and self.modelo721.complete


def build_filing_pack(
    year: int,
    reader: FilingLedgerReaderPort,
    pricer: EurPricer,
    *,
    rules: Modelo721Rules,
    custodian: Custodian,
    previous: PreviousDeclaration | None,
    overrides: Mapping[Currency, Decimal],
    today: date,
    shadow: bool,
) -> Result[FilingPack, DomainError]:
    """Build the filing figures of ``year``.

    Args:
        year: Tax year.
        reader: Ledger queries.
        pricer: EUR rate lookups (ECB through the FX port).
        rules: Modelo 721 threshold and increase.
        custodian: Who holds the coins.
        previous: The last 721 filed before ``year``, or ``None``.
        overrides: Operator-supplied EUR value of one unit per coin.
        today: Madrid date of the run; a 31 December rate is final only after it.
        shadow: True when the ledger holds simulated fills.

    Returns:
        ``Success(FilingPack)``, or ``Failure`` when the stored trades sell
        more than was bought.
    """
    start, end = tax_year_bounds(year)
    modelo100 = build_modelo100(
        year,
        reader.disposal_pieces(start, end),
        reader.purchases(
            add_months(start, -_REPURCHASE_MARGIN_MONTHS),
            add_months(end, _REPURCHASE_MARGIN_MONTHS),
        ),
    )
    positions = year_end_positions(year, reader.unit_trades(end))
    if isinstance(positions, Failure):
        return Failure(positions.failure())
    held = positions.unwrap()
    prices = tuple(
        _value(position, reader, pricer, year=year, overrides=overrides, today=today)
        for position in held
    )
    modelo721 = build_modelo721(
        year,
        held,
        {p.asset: p.price_eur for p in prices if p.price_eur is not None},
        rules,
        previous,
    )
    return Success(
        FilingPack(
            year=year,
            modelo100=modelo100,
            modelo721=modelo721,
            prices=prices,
            custodian=custodian,
            shadow=shadow,
        )
    )


def _value(
    position: YearEndPosition,
    reader: FilingLedgerReaderPort,
    pricer: EurPricer,
    *,
    year: int,
    overrides: Mapping[Currency, Decimal],
    today: date,
) -> YearEndPrice:
    asset = position.asset
    override = overrides.get(asset)
    if override is not None:
        return YearEndPrice(asset=asset, price_eur=override, source="operator override")
    pricing = pricer.price(_VALUATION_QUOTE, date(year, 12, 31), today=today)
    if pricing is None:
        return YearEndPrice(asset=asset, price_eur=None, source="no final ECB rate for 31 December")
    rate_note = f"ECB rate {pricing.rate} USD per EUR of {pricing.rate_date.isoformat()}"
    if asset == _VALUATION_QUOTE:
        native = Decimal("1")
        source = f"1 USDT at the {rate_note}"
    else:
        _, end = tax_year_bounds(year)
        close = reader.last_close(Pair(base=asset, quote=_VALUATION_QUOTE), end)
        if close is None:
            return YearEndPrice(asset=asset, price_eur=None, source="no stored bar before year end")
        native = close
        source = f"last bar close {close} USDT at the {rate_note}"
    with localcontext() as ctx:
        ctx.prec = _PRECISION
        return YearEndPrice(asset=asset, price_eur=native / pricing.rate, source=source)
