"""Modelo 721: virtual currencies held abroad on 31 December.

Builds the year-end holding per coin and decides whether the declaration is
due (Orden HFP/887/2023, RGAT art. 42 quater, as AEAT applies them to the
aggregate of a taxpayer's virtual currencies):

- No earlier 721 on file: due when the 31 December total exceeds the
  threshold (€50,000).
- An earlier 721 on file: due when the total rose more than the increase
  (€20,000) over the last declared total, or when a declared coin is no
  longer held (its line is declared as extinguished).

Both amounts come from ``config/filing.yaml`` through :class:`Modelo721Rules`.
Year-end units come from the trades; the date shown per coin is the opening
time of the oldest lot still open, found by the existing FIFO replay.

Reference: openspec/archive/36-modelo-100-721/change.md D-7, D-8.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Final

from returns.result import Failure, Result, Success

from hermes.domain.errors import DomainError
from hermes.domain.filing.tax_year import tax_year_bounds
from hermes.domain.lots.eur_fifo import EurTrade, exact_sum, replay_fifo_eur
from hermes.domain.trading.order import OrderSide
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.precision import exact

__all__ = [
    "HoldingLine",
    "Modelo721Origin",
    "Modelo721Report",
    "Modelo721Rules",
    "Obligation",
    "PreviousDeclaration",
    "UnitTrade",
    "YearEndPosition",
    "build_modelo721",
    "year_end_positions",
]

_ZERO: Final[Decimal] = Decimal("0")


class Modelo721Origin(StrEnum):
    """The origin key of a 721 line."""

    FIRST = "A"
    """Declared for the first time."""
    MODIFIED = "M"
    """Declared before and still held."""
    EXTINGUISHED = "C"
    """Declared before and no longer held."""


class Obligation(StrEnum):
    """Whether the 721 is due for the year."""

    REQUIRED = "required"
    NOT_REQUIRED = "not required"
    UNDETERMINED = "undetermined (year-end price missing)"


@dataclass(frozen=True, slots=True)
class Modelo721Rules:
    """The amounts that make the 721 due.

    Attributes:
        threshold_eur: First-time threshold on the 31 December total.
        increase_eur: Increase over the last declared total that makes a
            new declaration due.
    """

    threshold_eur: Decimal
    increase_eur: Decimal

    def __post_init__(self) -> None:
        """Reject negative amounts.

        Raises:
            ValueError: When an amount is negative.
        """
        if self.threshold_eur < _ZERO or self.increase_eur < _ZERO:
            raise ValueError("Modelo721Rules amounts must be >= 0")


@dataclass(frozen=True, slots=True)
class UnitTrade:
    """A trade reduced to what year-end units need.

    Attributes:
        ref: Ledger id.
        asset: Base coin of the pair.
        side: BUY adds units, SELL removes them.
        qty: Base quantity, strictly positive.
        executed_at: Fill time.
    """

    ref: str
    asset: Currency
    side: OrderSide
    qty: Decimal
    executed_at: datetime


@dataclass(frozen=True, slots=True)
class YearEndPosition:
    """Units of one coin held at the end of the tax year.

    Attributes:
        asset: Coin.
        units: Units held, strictly positive.
        oldest_lot_opened_at: Opening time of the oldest lot still open.
    """

    asset: Currency
    units: Decimal
    oldest_lot_opened_at: datetime


@dataclass(frozen=True, slots=True)
class PreviousDeclaration:
    """The last Modelo 721 the operator filed.

    Attributes:
        year: Tax year it declared.
        values_eur: Declared 31 December value per coin.
    """

    year: int
    values_eur: Mapping[Currency, Decimal]

    @property
    def total_eur(self) -> Decimal:
        """Return the declared total."""
        return exact_sum(self.values_eur.values())


@dataclass(frozen=True, slots=True)
class HoldingLine:
    """One 721 line.

    Attributes:
        asset: Coin.
        origin: Origin key.
        units: Units held on 31 December (zero for an extinguished coin).
        price_eur: EUR value of one unit on 31 December; ``None`` when missing.
        value_eur: ``units * price_eur``; ``None`` when the price is missing.
        oldest_lot_opened_at: Opening time of the oldest open lot; ``None``
            for an extinguished coin.
    """

    asset: Currency
    origin: Modelo721Origin
    units: Decimal
    price_eur: Decimal | None
    value_eur: Decimal | None
    oldest_lot_opened_at: datetime | None


@dataclass(frozen=True, slots=True)
class Modelo721Report:
    """The year's 721 figures and whether the declaration is due.

    Attributes:
        year: Tax year.
        lines: One line per coin held or extinguished, by coin.
        total_eur: Sum of the known values.
        missing_prices: Coins held with no year-end price.
        obligation: Whether the 721 is due.
        reason: One sentence saying why.
        previous: The earlier declaration the decision used, if any.
        rules: The thresholds the decision used.
    """

    year: int
    lines: tuple[HoldingLine, ...]
    total_eur: Decimal
    missing_prices: tuple[Currency, ...]
    obligation: Obligation
    reason: str
    previous: PreviousDeclaration | None
    rules: Modelo721Rules

    @property
    def complete(self) -> bool:
        """Return whether every coin held has a year-end price."""
        return not self.missing_prices


def year_end_positions(
    year: int, trades: Iterable[UnitTrade]
) -> Result[tuple[YearEndPosition, ...], DomainError]:
    """Return the coins held at the end of ``year`` and their oldest open lot.

    Args:
        year: Tax year; trades at or after its end are ignored.
        trades: Every trade of every coin, in any order; ties on time keep the
            given order.

    Returns:
        ``Success`` with the positions holding units, by coin, or
        ``Failure`` with the error of a SELL beyond the units held.
    """
    _, end = tax_year_bounds(year)
    by_asset: dict[Currency, list[UnitTrade]] = defaultdict(list)
    for trade in sorted(trades, key=lambda t: t.executed_at):
        if trade.executed_at < end:
            by_asset[trade.asset].append(trade)

    positions: list[YearEndPosition] = []
    for asset in sorted(by_asset):
        asset_trades = by_asset[asset]
        opened_at = {t.ref: t.executed_at for t in asset_trades if t.side == OrderSide.BUY}
        replay = replay_fifo_eur(
            asset,
            [
                EurTrade(
                    ref=t.ref,
                    side=t.side,
                    qty=t.qty,
                    executed_at=t.executed_at,
                    price_eur=None,
                    fee_eur=None,
                )
                for t in asset_trades
            ],
        )
        if isinstance(replay, Failure):
            return Failure(replay.failure())
        open_lots = [lot for lot in replay.unwrap().lots if lot.remaining_qty > _ZERO]
        if open_lots:
            positions.append(
                YearEndPosition(
                    asset=asset,
                    units=exact_sum(lot.remaining_qty for lot in open_lots),
                    oldest_lot_opened_at=opened_at[open_lots[0].buy_ref],
                )
            )
    return Success(tuple(positions))


def build_modelo721(
    year: int,
    positions: Iterable[YearEndPosition],
    prices_eur: Mapping[Currency, Decimal],
    rules: Modelo721Rules,
    previous: PreviousDeclaration | None = None,
) -> Modelo721Report:
    """Build the 721 lines of ``year`` and decide whether it is due.

    Args:
        year: Tax year.
        positions: Coins held on 31 December.
        prices_eur: EUR value of one unit per coin on 31 December.
        rules: Threshold and increase.
        previous: The last 721 filed for an earlier year, or ``None``.

    Returns:
        The report.

    Raises:
        ValueError: When ``previous`` is not for an earlier year.
    """
    if previous is not None and previous.year >= year:
        raise ValueError(f"previous 721 is for {previous.year}, not before {year}")
    declared = set(previous.values_eur) if previous is not None else set()

    lines: list[HoldingLine] = []
    held: set[Currency] = set()
    for position in positions:
        held.add(position.asset)
        price = prices_eur.get(position.asset)
        value = None
        if price is not None:
            with exact():
                value = position.units * price
        lines.append(
            HoldingLine(
                asset=position.asset,
                origin=(
                    Modelo721Origin.MODIFIED
                    if position.asset in declared
                    else Modelo721Origin.FIRST
                ),
                units=position.units,
                price_eur=price,
                value_eur=value,
                oldest_lot_opened_at=position.oldest_lot_opened_at,
            )
        )
    extinguished = sorted(declared - held)
    lines.extend(
        HoldingLine(
            asset=asset,
            origin=Modelo721Origin.EXTINGUISHED,
            units=_ZERO,
            price_eur=None,
            value_eur=_ZERO,
            oldest_lot_opened_at=None,
        )
        for asset in extinguished
    )
    lines.sort(key=lambda line: line.asset)

    total = exact_sum(line.value_eur for line in lines if line.value_eur is not None)
    missing = tuple(line.asset for line in lines if line.value_eur is None)
    obligation, reason = _decide(total, missing, tuple(extinguished), rules, previous)
    return Modelo721Report(
        year=year,
        lines=tuple(lines),
        total_eur=total,
        missing_prices=missing,
        obligation=obligation,
        reason=reason,
        previous=previous,
        rules=rules,
    )


def _decide(
    total: Decimal,
    missing: tuple[Currency, ...],
    extinguished: tuple[Currency, ...],
    rules: Modelo721Rules,
    previous: PreviousDeclaration | None,
) -> tuple[Obligation, str]:
    if previous is None:
        return _decide_first(total, missing, rules)
    if extinguished:
        names = ", ".join(asset.value for asset in extinguished)
        return Obligation.REQUIRED, f"coins declared in {previous.year} are no longer held: {names}"
    increase = f"{rules.increase_eur} EUR over the {previous.year} declaration"
    if total - previous.total_eur > rules.increase_eur:
        return Obligation.REQUIRED, f"the total rose more than {increase}"
    if missing:
        return Obligation.UNDETERMINED, "a year-end price is missing"
    return Obligation.NOT_REQUIRED, f"the total did not rise more than {increase}"


def _decide_first(
    total: Decimal, missing: tuple[Currency, ...], rules: Modelo721Rules
) -> tuple[Obligation, str]:
    threshold = f"{rules.threshold_eur} EUR"
    if total > rules.threshold_eur:
        return Obligation.REQUIRED, (
            f"no earlier 721 on file and the 31 December total exceeds {threshold}"
        )
    if missing:
        return Obligation.UNDETERMINED, "a year-end price is missing"
    return Obligation.NOT_REQUIRED, (
        f"no earlier 721 on file and the 31 December total does not exceed {threshold}"
    )
