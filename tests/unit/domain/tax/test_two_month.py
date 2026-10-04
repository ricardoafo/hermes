"""Tests for the two-month repurchase flag (change tax-estimates D-4).

Reference: ADR-016 decision 8, IRPF art. 33.5.f.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from hermes.domain.tax.two_month import Acquisition, TwoMonthFlag, add_months, two_month_flags
from hermes.domain.tax.year_position import Disposal

_SOLD_AT = datetime(2026, 5, 15, 12, tzinfo=UTC)


def _loss(consumed: frozenset[str] = frozenset({"b0"})) -> Disposal:
    return Disposal(
        asset="BTC", ref="s1", at=_SOLD_AT, gain=Decimal("-300"), consumed_refs=consumed
    )


def _buy(ref: str, day: date, asset: str = "BTC") -> Acquisition:
    return Acquisition(
        asset=asset, ref=ref, at=datetime(day.year, day.month, day.day, 12, tzinfo=UTC)
    )


def test_loss_with_a_repurchase_inside_two_months_is_flagged() -> None:
    """A BTC buy one month after a BTC loss sale flags the sale; the loss is kept."""
    flags = two_month_flags(
        [_loss()], [_buy("b0", date(2026, 1, 2)), _buy("b1", date(2026, 6, 15))]
    )
    assert flags == (
        TwoMonthFlag(
            asset="BTC",
            sale_ref="s1",
            sold_on=date(2026, 5, 15),
            loss=Decimal("300"),
            bought_on=date(2026, 6, 15),
        ),
    )


def test_buy_on_the_window_edge_is_flagged() -> None:
    """Exactly two calendar months before the sale is inside the window."""
    flags = two_month_flags([_loss()], [_buy("b1", date(2026, 3, 15))])
    assert [f.bought_on for f in flags] == [date(2026, 3, 15)]


def test_buy_outside_the_window_is_not_flagged() -> None:
    """Two months and one day after the sale is outside."""
    assert two_month_flags([_loss()], [_buy("b1", date(2026, 7, 16))]) == ()


def test_the_lot_the_sale_consumed_is_not_a_repurchase() -> None:
    """Buying on 1 May and selling at a loss on 15 May flags nothing by itself."""
    assert two_month_flags([_loss(frozenset({"b0"}))], [_buy("b0", date(2026, 5, 1))]) == ()


def test_other_assets_and_gain_sales_are_not_flagged() -> None:
    """An ETH buy does not flag a BTC sale, and a gain sale never flags."""
    gain = Disposal(
        asset="BTC", ref="s2", at=_SOLD_AT, gain=Decimal("10"), consumed_refs=frozenset()
    )
    assert two_month_flags([_loss(), gain], [_buy("e1", date(2026, 5, 20), "ETH")]) == ()


def test_the_nearest_buy_is_named() -> None:
    """With buys before and after, the flag names the closest one."""
    flags = two_month_flags(
        [_loss()], [_buy("b1", date(2026, 4, 1)), _buy("b2", date(2026, 5, 20))]
    )
    assert [f.bought_on for f in flags] == [date(2026, 5, 20)]


@pytest.mark.parametrize(
    ("day", "months", "expected"),
    [
        (date(2026, 3, 31), -1, date(2026, 2, 28)),
        (date(2024, 3, 31), -1, date(2024, 2, 29)),
        (date(2026, 11, 30), 2, date(2027, 1, 30)),
        (date(2026, 1, 15), -2, date(2025, 11, 15)),
    ],
)
def test_add_months_clamps_to_the_last_day_of_the_month(
    day: date, months: int, expected: date
) -> None:
    """Calendar months, not 30-day blocks; short months clamp."""
    assert add_months(day, months) == expected
