"""Tests for the yearly tax estimate (change tax-estimates R-1..R-6).

Reference: ADR-016 decisions 2, 3, 6, 7, 8, 10 and 12.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from hermes.application.console.views import TaxBand, TaxEstimate, TaxPending
from hermes.application.tax.estimate import (
    NO_PROFILE_NOTICE,
    PositionTax,
    TaxYearEstimate,
    estimate_year,
)
from hermes.domain.lots.eur_fifo import exact_sum
from hermes.domain.tax.netting import CarriedLoss, TaxpayerYear
from hermes.domain.trading.order import OrderSide
from hermes.domain.value_objects.currency import Currency
from hermes.infrastructure.config.tax import NO_PROFILE_NOTICE as CONFIG_NO_PROFILE_NOTICE
from hermes.ports.ledger import EurTradeRow
from tests.unit.domain.tax.scales import SCALE_2026

_D = Decimal
_SCALES = {2026: SCALE_2026}
_NOW = datetime(2026, 6, 30, 12, tzinfo=UTC)
_ZERO_PROFILE = TaxpayerYear()


class _Rows:
    """Builds ledger rows with increasing ids."""

    def __init__(self) -> None:
        self.rows: list[EurTradeRow] = []

    def add(
        self,
        side: OrderSide,
        at: datetime,
        price: str | None,
        *,
        qty: str = "1",
        fee: str = "0",
        asset: Currency = Currency.BTC,
    ) -> _Rows:
        self.rows.append(
            EurTradeRow(
                trade_id=len(self.rows) + 1,
                asset=asset,
                side=side,
                qty=_D(qty),
                executed_at=at,
                price_eur=None if price is None else _D(price),
                fee_eur=None if price is None else _D(fee),
            )
        )
        return self

    def buy(
        self,
        at: datetime,
        price: str | None,
        *,
        qty: str = "1",
        fee: str = "0",
        asset: Currency = Currency.BTC,
    ) -> _Rows:
        return self.add(OrderSide.BUY, at, price, qty=qty, fee=fee, asset=asset)

    def sell(self, at: datetime, price: str | None, *, qty: str = "1", fee: str = "0") -> _Rows:
        return self.add(OrderSide.SELL, at, price, qty=qty, fee=fee)


def _estimate(
    rows: Sequence[EurTradeRow],
    *,
    year: int = 2026,
    taxpayer: TaxpayerYear | None = _ZERO_PROFILE,
    prices: dict[str, Decimal] | None = None,
    as_of: datetime = _NOW,
    is_shadow: bool = True,
    default_minimum: str = "0",
) -> TaxYearEstimate | TaxPending:
    return estimate_year(
        rows,
        year=year,
        scales=_SCALES,
        taxpayer=taxpayer,
        default_personal_minimum=_D(default_minimum),
        prices=prices or {},
        as_of=as_of,
        is_shadow=is_shadow,
    )


def _ok(result: TaxYearEstimate | TaxPending) -> TaxYearEstimate:
    assert isinstance(result, TaxYearEstimate), result
    return result


def _at(month: int, day: int, year: int = 2026) -> datetime:
    return datetime(year, month, day, 12, tzinfo=UTC)


def test_round_trip_gain_is_taxed_on_the_scale_with_fees_included() -> None:
    """Buy 1 at 100 + fee 1, sell at 6,102 - fee 1: gain 6,000, tax 1,140."""
    rows = _Rows().buy(_at(1, 5), "100", fee="1").sell(_at(3, 5), "6102", fee="1").rows
    estimate = _ok(_estimate(rows))
    assert (estimate.realised_gain, estimate.hermes_tax) == (_D("6000"), TaxEstimate(_D("1140")))


def test_band_footnote_reports_the_band_of_the_next_euro() -> None:
    """After 6,000 of gains the next euro is at 21%, with 44,000 to the 23% band."""
    rows = _Rows().buy(_at(1, 5), "0").sell(_at(3, 5), "6000").rows
    assert _ok(_estimate(rows)).band == TaxBand(
        rate=_D("21"), next_rate=_D("23"), room_to_next=_D("44000")
    )


def test_missing_scale_is_pending_never_a_guess() -> None:
    """A year with no configured scale fails closed."""
    assert _estimate([], year=2027, as_of=_at(6, 1, 2027)) == TaxPending(
        label="tax pending: scale missing for 2027"
    )


def test_any_eur_pending_trade_in_the_year_makes_the_estimate_pending() -> None:
    """One unpriced trade in the year means no partial figure."""
    rows = _Rows().buy(_at(1, 5), "100").sell(_at(3, 5), "200").buy(_at(4, 1), None).rows
    assert _estimate(rows) == TaxPending(
        label="tax pending: EUR values pending for 1 trade(s) in 2026"
    )


def test_selling_a_lot_bought_while_pending_makes_the_year_pending() -> None:
    """A 2025 buy without EUR values feeds the 2026 sale's gain, so 2026 is pending."""
    rows = _Rows().buy(_at(12, 1, 2025), None).sell(_at(3, 5), "200").rows
    assert _estimate(rows) == TaxPending(
        label="tax pending: EUR values pending for a lot sold in 2026"
    )


def test_sell_beyond_open_lots_is_pending_not_a_crash() -> None:
    """An inconsistent ledger (a SELL with no lot) yields a pending notice."""
    rows = _Rows().sell(_at(3, 5), "200").rows
    assert _estimate(rows) == TaxPending(label="tax pending: ledger replay failed for BTC")


def test_pending_trades_of_other_years_do_not_block_the_year() -> None:
    """A pending 2027 buy leaves the 2026 estimate intact."""
    rows = _Rows().buy(_at(1, 5), "0").sell(_at(3, 5), "1000").buy(_at(1, 5, 2027), None).rows
    assert _ok(_estimate(rows)).hermes_tax == TaxEstimate(_D("190"))


def test_no_profile_assumes_zero_income_with_the_default_minimum_and_says_so() -> None:
    """No profile: the 5,550 minimum shelters part of a 10,000 gain (845.50) and a notice shows."""
    rows = _Rows().buy(_at(1, 5), "0").sell(_at(3, 5), "10000").rows
    estimate = _ok(_estimate(rows, taxpayer=None, default_minimum="5550"))
    assert estimate.hermes_tax == TaxEstimate(_D("845.50"))
    assert estimate.notices == (NO_PROFILE_NOTICE,)


def test_no_profile_notice_matches_the_config_wording() -> None:
    """The service and the profile loader show the same notice."""
    assert (NO_PROFILE_NOTICE, CONFIG_NO_PROFILE_NOTICE) == (
        "no taxpayer profile, assuming no other income",
        "no taxpayer profile, assuming no other income",
    )


def test_loss_year_carries_the_loss_forward() -> None:
    """A 2026 loss of 500 owes nothing and is left to carry with origin 2026."""
    rows = _Rows().buy(_at(1, 5), "1000").sell(_at(3, 5), "500").rows
    estimate = _ok(_estimate(rows))
    assert (estimate.realised_gain, estimate.hermes_tax) == (_D("-500"), TaxEstimate(_D("0")))
    assert estimate.carry_forward_left == (CarriedLoss(2026, _D("500")),)


def test_carried_loss_used_and_expiring_loss_flagged() -> None:
    """A 2022 loss partly used by a 300 gain expires; the flag names only the year."""
    taxpayer = TaxpayerYear(carried_losses=(CarriedLoss(2022, _D("1000")),))
    rows = _Rows().buy(_at(1, 5), "0").sell(_at(3, 5), "300").rows
    estimate = _ok(_estimate(rows, taxpayer=taxpayer))
    assert estimate.carry_forward_used == _D("300")
    assert estimate.flags == ("a carried loss from 2022 expires unused at the end of 2026",)


def test_expired_carried_loss_is_flagged_as_ignored() -> None:
    """A loss older than four years is reported as ignored."""
    taxpayer = TaxpayerYear(carried_losses=(CarriedLoss(2020, _D("1000")),))
    estimate = _ok(_estimate([], taxpayer=taxpayer))
    assert estimate.flags == ("a carried loss from 2020 is older than 4 years and was ignored",)


def test_loss_sale_with_repurchase_within_two_months_is_flagged_not_deferred() -> None:
    """Selling BTC at a loss and buying BTC a month later flags the sale; the loss counts."""
    rows = _Rows().buy(_at(1, 5), "1000").sell(_at(3, 5), "600").buy(_at(4, 2), "650").rows
    estimate = _ok(_estimate(rows, prices={"BTC": _D("650")}))
    assert estimate.realised_gain == _D("-400")
    assert estimate.flags == (
        "BTC sold at a loss of 400.00 EUR on 2026-03-05 with a BTC buy on 2026-04-02: "
        "two-month rule flagged for the gestor, loss not deferred",
    )


def test_today_tax_counts_only_sales_made_today_in_madrid() -> None:
    """With sales yesterday and today, today's tax is the year tax minus yesterday's."""
    rows = (
        _Rows()
        .buy(_at(1, 5), "0", qty="2")
        .sell(datetime(2026, 6, 29, 21, 59, tzinfo=UTC), "6000")
        .sell(datetime(2026, 6, 29, 22, 30, tzinfo=UTC), "1000")
        .rows
    )
    estimate = _ok(_estimate(rows, prices={"BTC": _D("0")}))
    assert estimate.hermes_tax == TaxEstimate(_D("1350"))
    assert estimate.today_tax == TaxEstimate(_D("210"))


def test_today_tax_is_a_credit_when_today_sale_is_a_loss() -> None:
    """A loss today against earlier gains lowers the year: a negative figure."""
    rows = (
        _Rows()
        .buy(_at(1, 5), "0")
        .buy(_at(1, 6), "1000")
        .sell(_at(2, 1), "1000")
        .sell(_NOW, "800")
        .rows
    )
    assert _ok(_estimate(rows)).today_tax == TaxEstimate(_D("-38"))


def test_past_year_has_no_today_tax_and_no_if_sold_now() -> None:
    """For a closed year, today adds nothing and selling now belongs to another year."""
    scales = {2025: SCALE_2026, 2026: SCALE_2026}
    rows = _Rows().buy(_at(1, 5, 2025), "0").sell(_at(3, 5, 2025), "100").rows
    estimate = _ok(
        estimate_year(
            rows,
            year=2025,
            scales=scales,
            taxpayer=_ZERO_PROFILE,
            default_personal_minimum=_D(0),
            prices={},
            as_of=_NOW,
            is_shadow=True,
        )
    )
    assert estimate.today_tax == TaxEstimate(_D("0"))
    assert estimate.tax_if_sold_now == TaxPending(label="tax pending: not the current tax year")


def test_tax_if_sold_now_is_marginal_and_split_pro_rata() -> None:
    """Unrealised 3,000 BTC + 2,000 ETH: total 950, split 570 / 380."""
    rows = (
        _Rows()
        .buy(_at(1, 5), "100", fee="1")
        .buy(_at(1, 6), "50", qty="2", asset=Currency.ETH)
        .rows
    )
    estimate = _ok(_estimate(rows, prices={"BTC": _D("3101"), "ETH": _D("1050")}))
    assert estimate.tax_if_sold_now == TaxEstimate(_D("950"))
    assert estimate.per_position_if_sold_now == (
        PositionTax("BTC", _D("3000"), TaxEstimate(_D("570"))),
        PositionTax("ETH", _D("2000"), TaxEstimate(_D("380"))),
    )


def test_tax_if_sold_now_stacks_on_the_realised_year() -> None:
    """With 6,000 already realised, a further 1,000 unrealised is taxed at 21%."""
    rows = _Rows().buy(_at(1, 5), "0", qty="2").sell(_at(2, 5), "6000").rows
    estimate = _ok(_estimate(rows, prices={"BTC": _D("1000")}))
    assert estimate.tax_if_sold_now == TaxEstimate(_D("210"))


def test_partly_sold_lot_counts_only_its_remaining_cost() -> None:
    """Half of a 2-unit lot bought for 200 + fee 2 remains: cost 101, price 151 -> gain 50."""
    rows = _Rows().buy(_at(1, 5), "100", qty="2", fee="2").sell(_at(2, 5), "101").rows
    estimate = _ok(_estimate(rows, prices={"BTC": _D("151")}))
    assert estimate.per_position_if_sold_now[0].unrealised_gain == _D("50")


def test_missing_price_makes_tax_if_sold_now_pending() -> None:
    """No price for an open position: no figure for it or the total."""
    rows = _Rows().buy(_at(1, 5), "100").rows
    estimate = _ok(_estimate(rows))
    pending = TaxPending(label="tax pending: no price for BTC")
    assert estimate.tax_if_sold_now == pending
    assert estimate.per_position_if_sold_now == (PositionTax("BTC", None, pending),)


def test_open_lot_without_eur_values_makes_tax_if_sold_now_pending() -> None:
    """A lot bought pending last year has no cost yet, so selling it now has no figure."""
    rows = _Rows().buy(_at(12, 1, 2025), None).rows
    estimate = _ok(_estimate(rows, prices={"BTC": _D("10")}))
    assert estimate.tax_if_sold_now == TaxPending(
        label="tax pending: EUR values pending for an open BTC lot"
    )


def test_no_open_positions_means_nothing_to_sell() -> None:
    """With every lot closed the figure is zero and there are no positions."""
    rows = _Rows().buy(_at(1, 5), "0").sell(_at(2, 5), "10").rows
    estimate = _ok(_estimate(rows))
    assert (estimate.tax_if_sold_now, estimate.per_position_if_sold_now) == (
        TaxEstimate(_D("0")),
        (),
    )


def test_offsetting_positions_split_zero_without_dividing_by_zero() -> None:
    """A +100 and a -100 position: no extra tax, each share zero."""
    rows = _Rows().buy(_at(1, 5), "100").buy(_at(1, 5), "100", asset=Currency.ETH).rows
    estimate = _ok(_estimate(rows, prices={"BTC": _D("200"), "ETH": _D("0")}))
    assert [p.tax_if_sold_now for p in estimate.per_position_if_sold_now] == [
        TaxEstimate(_D("0")),
        TaxEstimate(_D("0")),
    ]


@pytest.mark.parametrize(
    ("is_shadow", "label"),
    [(True, "estimate · shadow (no real tax due)"), (False, "estimate")],
)
def test_every_estimate_is_labelled(is_shadow: bool, label: str) -> None:
    """Figures are always an estimate; shadow figures say no real tax is due."""
    estimate = _ok(_estimate([], is_shadow=is_shadow))
    assert (estimate.is_estimate, estimate.label) == (True, label)


def test_repr_hides_profile_derived_carry_forward() -> None:
    """Carried-loss amounts come from the private profile and stay out of repr."""
    taxpayer = TaxpayerYear(carried_losses=(CarriedLoss(2024, _D("4321.99")),))
    estimate = _ok(_estimate([], taxpayer=taxpayer))
    assert "4321.99" not in repr(estimate)


_sales = st.lists(
    st.tuples(
        st.integers(min_value=0, max_value=364),
        st.decimals(min_value=-5000, max_value=40000, places=2, allow_nan=False),
    ),
    min_size=1,
    max_size=6,
)


@settings(max_examples=15, deadline=None)
@given(
    sales=_sales,
    other_gains=st.decimals(min_value=-20000, max_value=20000, places=2, allow_nan=False),
    income=st.decimals(min_value=0, max_value=20000, places=2, allow_nan=False),
)
def test_today_tax_summed_over_every_day_equals_the_year_tax(
    sales: list[tuple[int, Decimal]], other_gains: Decimal, income: Decimal
) -> None:
    """Today's figures telescope: their sum over the year is the year's hermes tax, exactly."""
    rows = _Rows()
    start = date(2026, 1, 1)
    for day, gain in sales:
        sold_at = datetime.combine(start + timedelta(days=day), datetime.min.time(), tzinfo=UTC)
        sold_at += timedelta(hours=12)
        rows.buy(sold_at - timedelta(minutes=1), "10000")
        rows.sell(sold_at, str(_D("10000") + gain))
    taxpayer = TaxpayerYear(other_capital_gains=other_gains, other_savings_income=income)
    year_tax = _ok(_estimate(rows.rows, taxpayer=taxpayer)).hermes_tax.amount
    daily = [
        _ok(
            _estimate(
                rows.rows,
                taxpayer=taxpayer,
                as_of=datetime.combine(start + timedelta(days=day), datetime.min.time(), tzinfo=UTC)
                + timedelta(hours=12),
            )
        ).today_tax.amount
        for day in range(365)
    ]
    assert exact_sum(daily) == year_tax
