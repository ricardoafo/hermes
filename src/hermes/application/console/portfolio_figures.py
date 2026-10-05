"""The portfolio view computed from ledger facts, with no I/O (#546, home slice).

The ledger read model gathers plain facts (each strategy's equity over time from
its portfolio marks and capital seed, the closed-trade pieces with their EUR
values, the open positions' net, the newest EUR rate, the capital seeds) and
this module turns them into a :class:`PortfolioSummary`.

EUR rule (change ``live-home-screen`` D-1): equity is held in the quote
currency (USDT) and converted at the newest rate the ledger holds, the same
rate the positions use for "now". Closed trades keep the EUR values their fills
were priced at (#435). The start capital is the seed's value at the seed-date
rate. Whatever has no EUR value yet is flagged pending, never guessed.

Reference: openspec/changes/live-home-screen/change.md (D-1 to D-3).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, tzinfo
from decimal import Decimal
from typing import Final

from hermes.application.console.views import (
    PortfolioRange,
    PortfolioSummary,
    TaxPending,
    WaterfallColumn,
)

__all__ = [
    "ClosedPiece",
    "EquityPoint",
    "PortfolioFacts",
    "SeedFacts",
    "StrategyBook",
    "build_portfolio",
    "equity_at",
    "range_start",
]

_ZERO: Final = Decimal(0)
_HUNDRED: Final = Decimal(100)
_WEEK: Final = timedelta(days=7)
_MONTH: Final = timedelta(days=30)
_DEFAULT_QUOTE: Final = "USDT"


@dataclass(frozen=True, slots=True)
class EquityPoint:
    """A strategy's equity (cash plus open positions) from a point in time on.

    Attributes:
        at: When this value took effect (a bar close, or the seed time).
        equity: Equity in the quote currency.
    """

    at: datetime
    equity: Decimal


@dataclass(frozen=True, slots=True)
class StrategyBook:
    """One strategy's equity history and its newest cash split.

    Attributes:
        points: Equity values, oldest first; each holds until the next one.
        cash: Newest uninvested quote currency.
        invested: Newest market value of the open positions, quote currency.
    """

    points: tuple[EquityPoint, ...]
    cash: Decimal
    invested: Decimal


@dataclass(frozen=True, slots=True)
class ClosedPiece:
    """One lot consumption of a coin sale, with its EUR parts (``None`` while pending).

    Attributes:
        at: When the sale filled.
        proceeds: EUR share of the sale's proceeds.
        cost: EUR acquisition value of the consumed part (buy fee included).
        buy_fee: EUR share of the buy fee inside ``cost``.
        sell_fee: EUR share of the sale's fee.
    """

    at: datetime
    proceeds: Decimal | None
    cost: Decimal | None
    buy_fee: Decimal | None
    sell_fee: Decimal | None

    def gross_and_fees(self) -> tuple[Decimal, Decimal] | None:
        """Return ``(gross, fees)`` in EUR, or ``None`` while any part is pending.

        Gross is before every fee (``proceeds - cost + buy_fee``); fees are the
        buy fee share plus the sell fee share, so ``gross - fees`` is the
        realised gain of the consumption.
        """
        if (
            self.proceeds is None
            or self.cost is None
            or self.buy_fee is None
            or self.sell_fee is None
        ):
            return None
        gross = self.proceeds - self.cost + self.buy_fee
        return gross, self.buy_fee + self.sell_fee


@dataclass(frozen=True, slots=True)
class SeedFacts:
    """A strategy's start capital.

    Attributes:
        amount: The seed in its quote currency (the 5,000 USDT shadow capital).
        seeded_at: When the run started.
        value_eur: Its EUR value at the seed-date rate; ``None`` while pending.
        rate_date: Date of the ECB rate behind ``value_eur``, when known.
    """

    amount: Decimal
    seeded_at: datetime
    value_eur: Decimal | None
    rate_date: date | None = None


@dataclass(frozen=True, slots=True)
class PortfolioFacts:
    """Everything the ledger knows about the portfolio at read time.

    Attributes:
        as_of: Read time.
        tz: Timezone whose calendar days "today" and "this year" follow.
        books: One equity history per strategy.
        closed: Coin sales, as lot consumptions.
        open_net: Net P&L of the open positions in EUR; ``None`` when any of
            them is EUR pending.
        eur_per_quote: EUR for one quote unit at the newest recorded rate.
        seeds: The capital seeds, one per strategy that has one.
        quote: The quote currency every book is counted in, when known.
        eur_rate_date: Date of the ECB rate behind ``eur_per_quote``, when known.
    """

    as_of: datetime
    tz: tzinfo
    books: tuple[StrategyBook, ...]
    closed: tuple[ClosedPiece, ...]
    open_net: Decimal | None
    eur_per_quote: Decimal | None
    seeds: tuple[SeedFacts, ...]
    quote: str | None = None
    eur_rate_date: date | None = None


def range_start(range_: PortfolioRange, as_of: datetime, tz: tzinfo) -> datetime:
    """Return when ``range_`` starts.

    Today is since local midnight, the week the last 7 days, the month the last
    30 days (the TUI's ``M`` key) and the year since 1 January, local time (the
    tax year).

    Args:
        range_: The range.
        as_of: Read time, timezone-aware.
        tz: Display timezone.

    Returns:
        The aware start instant.
    """
    local = as_of.astimezone(tz)
    match range_:
        case PortfolioRange.TODAY:
            return datetime.combine(local.date(), time(), tzinfo=tz)
        case PortfolioRange.WEEK:
            return as_of - _WEEK
        case PortfolioRange.MONTH:
            return as_of - _MONTH
        case PortfolioRange.YEAR:
            return datetime(local.year, 1, 1, tzinfo=tz)


def equity_at(books: Sequence[StrategyBook], at: datetime) -> Decimal:
    """Return the summed equity in force at ``at`` (a strategy not started yet counts 0).

    Args:
        books: The strategies' equity histories.
        at: The instant.

    Returns:
        The total in the quote currency.
    """
    total = _ZERO
    for book in books:
        current = [p.equity for p in book.points if p.at <= at]
        if current:
            total += current[-1]
    return total


def build_portfolio(facts: PortfolioFacts, range_: PortfolioRange) -> PortfolioSummary:
    """Return the portfolio summary for ``range_``.

    Args:
        facts: The ledger facts.
        range_: The period of the change line, net and sparkline.

    Returns:
        The summary; with no EUR rate it is EUR pending and carries the quote
        total only.
    """
    start = range_start(range_, facts.as_of, facts.tz)
    total_quote = equity_at(facts.books, facts.as_of)
    seed = _seed_line(facts)
    rate = facts.eur_per_quote
    if rate is None:
        empty = WaterfallColumn(gross=_ZERO, fees=_ZERO, tax=TaxPending(), eur_pending=True)
        return PortfolioSummary(
            range=range_,
            as_of=facts.as_of,
            total_value=_ZERO,
            change=_ZERO,
            change_pct=_ZERO,
            net=_ZERO,
            sparkline=(),
            cash=_ZERO,
            invested=_ZERO,
            today=empty,
            year=empty,
            open_pnl=_ZERO,
            open_tax_if_sold=TaxPending(),
            tax_band=None,
            eur_pending=True,
            total_value_quote=total_quote,
            open_pnl_pending=True,
            starting_capital=seed.value_eur,
            started_on=seed.started_on,
            starting_capital_quote=seed.amount,
            quote_currency=facts.quote or _DEFAULT_QUOTE,
            starting_capital_rate_date=seed.rate_date,
        )
    samples = _samples(facts, start)
    change = samples[-1] - samples[0]
    change_pct = change * _HUNDRED / samples[0] if samples[0] != _ZERO else _ZERO
    local_today = facts.as_of.astimezone(facts.tz).date()
    year_start = range_start(PortfolioRange.YEAR, facts.as_of, facts.tz)
    return PortfolioSummary(
        range=range_,
        as_of=facts.as_of,
        total_value=total_quote * rate,
        change=change * rate,
        change_pct=change_pct,
        net=change * rate,
        sparkline=tuple(value * rate for value in samples),
        cash=sum((b.cash for b in facts.books), start=_ZERO) * rate,
        invested=sum((b.invested for b in facts.books), start=_ZERO) * rate,
        today=_column(p for p in facts.closed if _local_day(p.at, facts.tz) == local_today),
        year=_column(p for p in facts.closed if year_start <= p.at <= facts.as_of),
        open_pnl=facts.open_net if facts.open_net is not None else _ZERO,
        open_tax_if_sold=TaxPending(),
        tax_band=None,
        total_value_quote=total_quote,
        open_pnl_pending=facts.open_net is None,
        starting_capital=seed.value_eur,
        started_on=seed.started_on,
        starting_capital_quote=seed.amount,
        quote_currency=facts.quote or _DEFAULT_QUOTE,
        eur_rate_date=facts.eur_rate_date,
        starting_capital_rate_date=seed.rate_date,
    )


def _samples(facts: PortfolioFacts, start: datetime) -> list[Decimal]:
    """Return the equity at the range start, then at every change inside the range."""
    times = sorted({p.at for book in facts.books for p in book.points})
    inside = [t for t in times if start < t <= facts.as_of]
    if any(t <= start for t in times) or not inside:
        first = equity_at(facts.books, start)
    else:
        first = equity_at(facts.books, inside.pop(0))
    return [first, *(equity_at(facts.books, t) for t in inside)]


def _column(pieces: Iterable[ClosedPiece]) -> WaterfallColumn:
    gross = _ZERO
    fees = _ZERO
    for piece in pieces:
        parts = piece.gross_and_fees()
        if parts is None:
            return WaterfallColumn(gross=_ZERO, fees=_ZERO, tax=TaxPending(), eur_pending=True)
        gross += parts[0]
        fees += parts[1]
    return WaterfallColumn(gross=gross, fees=fees, tax=TaxPending())


def _local_day(at: datetime, tz: tzinfo) -> date:
    return at.astimezone(tz).date()


@dataclass(frozen=True, slots=True)
class _SeedLine:
    amount: Decimal | None = None
    value_eur: Decimal | None = None
    started_on: date | None = None
    rate_date: date | None = None


def _seed_line(facts: PortfolioFacts) -> _SeedLine:
    if not facts.seeds:
        return _SeedLine()
    values = [s.value_eur for s in facts.seeds if s.value_eur is not None]
    priced = len(values) == len(facts.seeds)
    dates = [s.rate_date for s in facts.seeds if s.rate_date is not None]
    return _SeedLine(
        amount=sum((s.amount for s in facts.seeds), start=_ZERO),
        value_eur=sum(values, start=_ZERO) if priced else None,
        started_on=min(_local_day(s.seeded_at, facts.tz) for s in facts.seeds),
        rate_date=max(dates) if priced and len(dates) == len(facts.seeds) else None,
    )
