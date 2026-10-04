"""The portfolio view from plain ledger facts (#546, change live-home-screen D-1 to D-3).

Invariants:

- ranges start at local midnight (today), 7 and 30 days back, and 1 January
  local time (the tax year);
- equity in force at an instant is each strategy's newest point at or before it;
- the total, cash, invested, change and sparkline are the quote figures times
  the newest EUR rate, and the change is the last sample minus the first;
- a closed piece splits into gross before every fee and the fees, so gross
  minus fees is its realised gain; one pending piece makes its column pending;
- without a rate the summary is EUR pending and carries no EUR figure;
- the start capital is the seed's EUR value, or pending with the USDT amount.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hermes.application.console.portfolio_figures import (
    ClosedPiece,
    EquityPoint,
    PortfolioFacts,
    SeedFacts,
    StrategyBook,
    build_portfolio,
    equity_at,
    range_start,
)
from hermes.application.console.views import PortfolioRange, TaxPending

_MADRID = ZoneInfo("Europe/Madrid")
_AS_OF = datetime(2026, 10, 1, 17, tzinfo=UTC)
_RATE = Decimal("0.8")


def _book(*points: tuple[datetime, str], cash: str = "0", invested: str = "0") -> StrategyBook:
    return StrategyBook(
        points=tuple(EquityPoint(at=at, equity=Decimal(v)) for at, v in points),
        cash=Decimal(cash),
        invested=Decimal(invested),
    )


def _facts(
    books: tuple[StrategyBook, ...],
    *,
    closed: tuple[ClosedPiece, ...] = (),
    open_net: Decimal | None = Decimal(0),
    rate: Decimal | None = _RATE,
    seeds: tuple[SeedFacts, ...] = (),
) -> PortfolioFacts:
    return PortfolioFacts(
        as_of=_AS_OF,
        tz=_MADRID,
        books=books,
        closed=closed,
        open_net=open_net,
        eur_per_quote=rate,
        seeds=seeds,
    )


def _piece(at: datetime, *, pending: bool = False) -> ClosedPiece:
    return ClosedPiece(
        at=at,
        proceeds=None if pending else Decimal(440),
        cost=Decimal("400.4"),
        buy_fee=Decimal("0.4"),
        sell_fee=Decimal("0.44"),
    )


def test_ranges_start_where_the_labels_say() -> None:
    """Today is Madrid midnight, week and month are 7 and 30 days back, year is 1 January."""
    starts = [range_start(r, _AS_OF, _MADRID) for r in PortfolioRange]
    assert starts == [
        datetime(2026, 9, 30, 22, tzinfo=UTC),
        _AS_OF - timedelta(days=7),
        _AS_OF - timedelta(days=30),
        datetime(2025, 12, 31, 23, tzinfo=UTC),
    ]


def test_equity_in_force_is_the_newest_point_per_strategy() -> None:
    """Each strategy counts its newest point at or before the instant; later ones do not."""
    t = datetime(2026, 10, 1, 8, tzinfo=UTC)
    books = (_book((t, "100"), (t + timedelta(hours=4), "110")), _book((t, "50")))
    assert [equity_at(books, t + timedelta(hours=h)) for h in (-1, 0, 4)] == [
        Decimal(0),
        Decimal(150),
        Decimal(160),
    ]


def test_figures_are_quote_values_at_the_newest_rate() -> None:
    """Total, cash, invested and the sparkline are USDT times 0.8; change is last minus first."""
    seed_at = datetime(2026, 10, 1, 7, tzinfo=UTC)
    book = _book(
        (seed_at, "5000"),
        (datetime(2026, 10, 1, 8, tzinfo=UTC), "4999"),
        (datetime(2026, 10, 1, 16, tzinfo=UTC), "5098.45"),
        cash="4548.45",
        invested="550",
    )
    summary = build_portfolio(_facts((book,)), PortfolioRange.TODAY)
    assert (
        summary.total_value,
        summary.cash,
        summary.invested,
        summary.sparkline,
        summary.change,
        summary.change_pct,
        summary.net,
        summary.total_value_quote,
    ) == (
        Decimal("4078.76"),
        Decimal("3638.76"),
        Decimal(440),
        (Decimal(4000), Decimal("3999.2"), Decimal("4078.76")),
        Decimal("78.76"),
        Decimal("1.969"),
        Decimal("78.76"),
        Decimal("5098.45"),
    )


def test_change_starts_from_the_equity_in_force_at_the_range_start() -> None:
    """A point before the range start is the baseline; it is not repeated inside the range."""
    book = _book(
        (datetime(2026, 9, 1, tzinfo=UTC), "1000"),
        (datetime(2026, 10, 1, 8, tzinfo=UTC), "1100"),
    )
    summary = build_portfolio(_facts((book,), rate=Decimal(1)), PortfolioRange.TODAY)
    assert (summary.sparkline, summary.change, summary.change_pct) == (
        (Decimal(1000), Decimal(1100)),
        Decimal(100),
        Decimal(10),
    )


def test_closed_piece_waterfall_nets_to_the_realised_gain() -> None:
    """Gross 40, fees 0.84, net 39.16 today and this year; tax stays pending."""
    piece = _piece(datetime(2026, 10, 1, 12, tzinfo=UTC))
    summary = build_portfolio(_facts((_book(),), closed=(piece,)), PortfolioRange.TODAY)
    columns = [(c.gross, c.fees, c.net, c.eur_pending) for c in (summary.today, summary.year)]
    assert columns == [(Decimal(40), Decimal("0.84"), Decimal("39.16"), False)] * 2
    assert isinstance(summary.today.tax, TaxPending)


def test_an_earlier_day_counts_for_the_year_only() -> None:
    """A sale on another local day is in This year, not in Today."""
    piece = _piece(datetime(2026, 9, 30, 21, tzinfo=UTC))
    summary = build_portfolio(_facts((_book(),), closed=(piece,)), PortfolioRange.TODAY)
    assert (summary.today.net, summary.year.net) == (Decimal(0), Decimal("39.16"))


def test_one_pending_piece_makes_its_column_pending() -> None:
    """A sale without EUR values never yields a partial sum: the column is EUR pending."""
    pieces = (
        _piece(datetime(2026, 10, 1, 12, tzinfo=UTC)),
        _piece(datetime(2026, 10, 1, 13, tzinfo=UTC), pending=True),
    )
    summary = build_portfolio(_facts((_book(),), closed=pieces), PortfolioRange.TODAY)
    assert (summary.today.eur_pending, summary.today.gross, summary.today.fees) == (
        True,
        Decimal(0),
        Decimal(0),
    )


def test_without_a_rate_the_summary_is_eur_pending() -> None:
    """No rate: no EUR figure at all, only the USDT total; open and columns pending too."""
    book = _book((datetime(2026, 10, 1, 8, tzinfo=UTC), "5000"), cash="5000")
    summary = build_portfolio(_facts((book,), rate=None), PortfolioRange.WEEK)
    assert (
        summary.eur_pending,
        summary.total_value,
        summary.sparkline,
        summary.total_value_quote,
        summary.open_pnl_pending,
        summary.today.eur_pending,
        summary.year.eur_pending,
    ) == (True, Decimal(0), (), Decimal(5000), True, True, True)


def test_open_net_pending_is_flagged_not_zeroed_silently() -> None:
    """An EUR-pending open position marks open P&L pending; a known one is passed through."""
    pending = build_portfolio(_facts((_book(),), open_net=None), PortfolioRange.TODAY)
    known = build_portfolio(_facts((_book(),), open_net=Decimal("39.16")), PortfolioRange.TODAY)
    assert (pending.open_pnl_pending, known.open_pnl_pending, known.open_pnl) == (
        True,
        False,
        Decimal("39.16"),
    )


def test_start_capital_is_the_seed_eur_value_or_pending() -> None:
    """A priced seed gives its EUR value; an unpriced one keeps only the USDT amount."""
    seeded_at = datetime(2026, 9, 30, 22, 30, tzinfo=UTC)
    priced = SeedFacts(amount=Decimal(5000), seeded_at=seeded_at, value_eur=Decimal(4000))
    pending = SeedFacts(amount=Decimal(5000), seeded_at=seeded_at, value_eur=None)
    lines = [
        (s.starting_capital, s.starting_capital_quote, s.started_on)
        for s in (
            build_portfolio(_facts((_book(),), seeds=(seed,)), PortfolioRange.TODAY)
            for seed in (priced, pending)
        )
    ]
    assert lines == [
        (Decimal(4000), Decimal(5000), date(2026, 10, 1)),
        (None, Decimal(5000), date(2026, 10, 1)),
    ]


@pytest.mark.parametrize("rate", [_RATE, None], ids=["priced", "pending"])
def test_summary_carries_the_ledger_quote_currency(rate: Decimal | None) -> None:
    """The start line's currency is the ledger's quote currency, priced or pending (#650)."""
    facts = replace(_facts((_book(),), rate=rate), quote="USDC")
    assert build_portfolio(facts, PortfolioRange.TODAY).quote_currency == "USDC"


def test_no_seed_leaves_the_start_unknown() -> None:
    """Without a seed no start line is offered."""
    summary = build_portfolio(_facts((_book(),)), PortfolioRange.TODAY)
    assert (summary.starting_capital, summary.starting_capital_quote, summary.started_on) == (
        None,
        None,
        None,
    )


_AMOUNTS = st.decimals(min_value=Decimal(1), max_value=Decimal(10**7), places=2)


@given(
    values=st.lists(_AMOUNTS, min_size=1, max_size=12),
    rate=st.decimals(min_value=Decimal("0.5"), max_value=Decimal(2), places=4),
    range_=st.sampled_from(list(PortfolioRange)),
)
def test_change_is_last_minus_first_sample_at_any_rate(
    values: list[Decimal], rate: Decimal, range_: PortfolioRange
) -> None:
    """For any equity path and rate, change = last - first sample and the total is the last."""
    start = datetime(2026, 9, 1, tzinfo=UTC)
    points = tuple((start + timedelta(hours=6 * i), str(v)) for i, v in enumerate(values))
    summary = build_portfolio(_facts((_book(*points),), rate=rate), range_)
    assert summary.change == summary.sparkline[-1] - summary.sparkline[0]
    assert summary.total_value == summary.sparkline[-1] == values[-1] * rate
