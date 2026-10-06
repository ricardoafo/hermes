"""Property tests for the two-month repurchase flag (IRPF art. 33.5.f).

The example tests in ``test_two_month.py`` fix single cases. These hold the
rule to its definition over random ledgers: only a loss sale can flag, the
named buy is an eligible buy nearest to the sale, the window ends exactly two
calendar months either side of the Madrid sale date, and the result does not
depend on input order, on unrelated buys, or on the timezone an instant is
written in.

Invariant protected: a loss sale is flagged exactly when a same-asset buy it
did not consume falls on a Madrid date within two calendar months of the
sale's Madrid date, and the flag names the nearest such buy.

Reference: ADR-016 decision 8, change ``tax-estimates`` D-4; issue #692.
"""

from __future__ import annotations

import calendar
import random
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from hypothesis import given
from hypothesis import strategies as st

from hermes.domain.tax.two_month import (
    REPURCHASE_WINDOW_MONTHS,
    Acquisition,
    add_months,
    two_month_flags,
)
from hermes.domain.tax.year_position import Disposal

_MADRID = ZoneInfo("Europe/Madrid")
_ASSETS = ("BTC", "ETH")
_FIRST = datetime(2025, 1, 1, tzinfo=UTC)
_SPAN_MINUTES = 3 * 366 * 24 * 60

_instants = st.integers(0, _SPAN_MINUTES).map(lambda m: _FIRST + timedelta(minutes=m))
_gains = st.decimals(min_value=-5000, max_value=5000, places=2, allow_nan=False)


@st.composite
def _ledgers(draw: st.DrawFn) -> tuple[list[Disposal], list[Acquisition]]:
    buys = [
        Acquisition(asset=draw(st.sampled_from(_ASSETS)), ref=f"b{i}", at=draw(_instants))
        for i in range(draw(st.integers(0, 8)))
    ]
    refs = [b.ref for b in buys]
    sales = [
        Disposal(
            asset=draw(st.sampled_from(_ASSETS)),
            ref=f"s{i}",
            at=draw(_instants),
            gain=draw(_gains),
            consumed_refs=frozenset(draw(st.lists(st.sampled_from(refs), max_size=3)))
            if refs
            else frozenset(),
        )
        for i in range(draw(st.integers(0, 5)))
    ]
    return sales, buys


def _madrid(at: datetime) -> date:
    return at.astimezone(_MADRID).date()


def _eligible(sale: Disposal, buys: list[Acquisition]) -> list[date]:
    sold_on = _madrid(sale.at)
    earliest = add_months(sold_on, -REPURCHASE_WINDOW_MONTHS)
    latest = add_months(sold_on, REPURCHASE_WINDOW_MONTHS)
    return [
        _madrid(b.at)
        for b in buys
        if b.asset == sale.asset
        and b.ref not in sale.consumed_refs
        and earliest <= _madrid(b.at) <= latest
    ]


@given(_ledgers())
def test_a_sale_flags_exactly_when_it_is_a_loss_with_an_eligible_buy(
    ledger: tuple[list[Disposal], list[Acquisition]],
) -> None:
    """The flagged sales are the loss sales that have at least one eligible buy."""
    sales, buys = ledger
    flagged = {f.sale_ref for f in two_month_flags(sales, buys)}
    assert flagged == {s.ref for s in sales if s.gain < 0 and _eligible(s, buys)}


@given(_ledgers())
def test_each_flag_names_the_nearest_eligible_buy_and_the_loss(
    ledger: tuple[list[Disposal], list[Acquisition]],
) -> None:
    """The named buy is eligible, no eligible buy is closer, and the loss is the gain negated."""
    sales, buys = ledger
    by_ref = {s.ref: s for s in sales}
    for flag in two_month_flags(sales, buys):
        sale = by_ref[flag.sale_ref]
        sold_on = _madrid(sale.at)
        distances = [abs((d - sold_on).days) for d in _eligible(sale, buys)]
        assert (
            flag.bought_on in _eligible(sale, buys),
            abs((flag.bought_on - sold_on).days),
            flag.loss,
            flag.sold_on,
            flag.asset,
        ) == (True, min(distances), -sale.gain, sold_on, sale.asset)


@given(_ledgers(), st.randoms(use_true_random=False))
def test_input_order_does_not_change_the_flags(
    ledger: tuple[list[Disposal], list[Acquisition]], rng: random.Random
) -> None:
    """Shuffling the sales and the buys gives the same flags, in sale-time order."""
    sales, buys = ledger
    shuffled_sales, shuffled_buys = list(sales), list(buys)
    rng.shuffle(shuffled_sales)
    rng.shuffle(shuffled_buys)
    flags = two_month_flags(sales, buys)
    assert (
        two_month_flags(shuffled_sales, shuffled_buys),
        [f.sold_on for f in flags] == sorted(f.sold_on for f in flags),
    ) == (flags, True)


@given(_ledgers(), _instants, st.sampled_from(_ASSETS))
def test_one_more_buy_never_clears_a_flag(
    ledger: tuple[list[Disposal], list[Acquisition]], at: datetime, asset: str
) -> None:
    """Adding a buy can only add flags; a sale flagged before stays flagged."""
    sales, buys = ledger
    before = {f.sale_ref for f in two_month_flags(sales, buys)}
    after = {
        f.sale_ref
        for f in two_month_flags(sales, [*buys, Acquisition(asset=asset, ref="extra", at=at)])
    }
    assert before <= after


@given(_instants, st.sampled_from((-1, 1)))
def test_the_window_ends_exactly_two_calendar_months_from_the_sale(
    sold_at: datetime, side: int
) -> None:
    """A buy on the window's last Madrid day flags; one day further out does not."""
    sold_on = _madrid(sold_at)
    edge = add_months(sold_on, side * REPURCHASE_WINDOW_MONTHS)
    sale = Disposal(asset="BTC", ref="s", at=sold_at, gain=Decimal("-1"), consumed_refs=frozenset())

    def flagged(day: date) -> bool:
        noon = datetime(day.year, day.month, day.day, 12, tzinfo=_MADRID)
        return bool(two_month_flags([sale], [Acquisition(asset="BTC", ref="b", at=noon)]))

    assert (flagged(edge), flagged(edge + timedelta(days=side))) == (True, False)


@given(_ledgers(), st.integers(-12, 14))
def test_the_same_instants_in_another_timezone_give_the_same_flags(
    ledger: tuple[list[Disposal], list[Acquisition]], offset_hours: int
) -> None:
    """Dates come from the Madrid calendar, whatever offset the instants are written in."""
    sales, buys = ledger
    zone = timezone(timedelta(hours=offset_hours))
    moved_sales = [
        Disposal(
            asset=s.asset,
            ref=s.ref,
            at=s.at.astimezone(zone),
            gain=s.gain,
            consumed_refs=s.consumed_refs,
        )
        for s in sales
    ]
    moved_buys = [Acquisition(asset=b.asset, ref=b.ref, at=b.at.astimezone(zone)) for b in buys]
    assert two_month_flags(moved_sales, moved_buys) == two_month_flags(sales, buys)


@given(st.dates(date(2020, 1, 1), date(2035, 12, 31)), st.integers(-24, 24))
def test_add_months_moves_whole_months_and_clamps_the_day(day: date, months: int) -> None:
    """The month moves by exactly ``months``; the day is kept, or clamped to the month's end."""
    shifted = add_months(day, months)
    last = calendar.monthrange(shifted.year, shifted.month)[1]
    assert (
        (shifted.year * 12 + shifted.month) - (day.year * 12 + day.month),
        shifted.day,
    ) == (months, min(day.day, last))
