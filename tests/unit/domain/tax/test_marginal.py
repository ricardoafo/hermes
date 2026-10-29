"""Tests for the marginal hermes tax (change tax-estimates R-1, R-2).

Reference: ADR-016 decision 7, IRPF arts. 56.2 and 66.1.
"""

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hermes.domain.tax.marginal import marginal_tax, minimum_remainder, savings_tax
from hermes.domain.tax.netting import CarriedLoss, TaxpayerYear
from tests.unit.domain.tax.scales import SCALE_2026

_D = Decimal
_amounts = st.decimals(min_value=0, max_value=400_000, places=2, allow_nan=False)


@pytest.mark.parametrize(
    ("gain", "tax"),
    [
        ("6000", "1140.00"),
        ("50000", "10380.00"),
        ("200000", "44880.00"),
        ("300000", "71880.00"),
    ],
)
def test_hermes_alone_pays_the_scale_at_each_band_edge(gain: str, tax: str) -> None:
    """With no other income and no minimum, hermes tax is the scale itself."""
    result = marginal_tax(SCALE_2026, TaxpayerYear(), _D(gain), year=2026)
    assert result.hermes_tax == _D(tax)


def test_other_savings_income_pushes_hermes_into_a_higher_band() -> None:
    """With 6,000 of interest the first hermes euro is taxed at 21%, not 19%."""
    taxpayer = TaxpayerYear(other_savings_income=_D("6000"))
    assert marginal_tax(SCALE_2026, taxpayer, _D("1000"), year=2026).hermes_tax == _D("210.00")


def test_personal_minimum_remainder_is_relieved_at_the_bottom_rates() -> None:
    """General income 0, minimum 5,550, gain 10,000 -> 925.50 (art. 66.1.2).

    Scale on the whole base: 6,000 * 0.19 + 4,000 * 0.21 = 1,140 + 840 = 1,980.00.
    Scale on the minimum's part: 5,550 * 0.19 = 1,054.50.
    Tax 1,980.00 - 1,054.50 = 925.50. (Taxing 10,000 - 5,550 = 4,450 instead gives
    845.50, which understates: it relieves the minimum at the top rate.)
    """
    taxpayer = TaxpayerYear(personal_minimum=_D("5550"))
    assert marginal_tax(SCALE_2026, taxpayer, _D("10000"), year=2026).hermes_tax == _D("925.50")


def test_personal_minimum_absorbed_by_general_income_leaves_savings_fully_taxed() -> None:
    """A salary above the minimum leaves no remainder for the savings base."""
    taxpayer = TaxpayerYear(general_income=_D("20000"), personal_minimum=_D("5550"))
    assert marginal_tax(SCALE_2026, taxpayer, _D("10000"), year=2026).hermes_tax == _D("1980.00")


def test_minimum_remainder_is_what_the_general_income_cannot_absorb() -> None:
    """General income 3,000 absorbs part of a 5,550 minimum; 2,550 goes to savings (art. 56.2)."""
    taxpayer = TaxpayerYear(general_income=_D("3000"), personal_minimum=_D("5550"))
    assert minimum_remainder(taxpayer) == _D("2550")


def test_partly_absorbed_minimum_relieves_savings_at_the_bottom_rate() -> None:
    """General income 3,000, minimum 5,550, gain 10,000 -> 1,495.50.

    Remainder 2,550. Scale on 10,000 = 1,980.00; scale on 2,550 = 2,550 * 0.19 = 484.50.
    Tax 1,980.00 - 484.50 = 1,495.50.
    """
    taxpayer = TaxpayerYear(general_income=_D("3000"), personal_minimum=_D("5550"))
    assert marginal_tax(SCALE_2026, taxpayer, _D("10000"), year=2026).hermes_tax == _D("1495.50")


def test_loss_year_with_other_gains_is_a_credit() -> None:
    """A 2,000 hermes loss against 10,000 of other gains lowers the tax by 420."""
    taxpayer = TaxpayerYear(other_capital_gains=_D("10000"))
    result = marginal_tax(SCALE_2026, taxpayer, _D("-2000"), year=2026)
    assert result.hermes_tax == _D("-420.00")


def test_loss_year_with_no_other_income_is_zero_and_carries_the_loss() -> None:
    """A loss with nothing to offset owes nothing and carries forward in full."""
    result = marginal_tax(SCALE_2026, TaxpayerYear(), _D("-2000"), year=2026)
    assert result.hermes_tax == _D("0")
    assert result.with_hermes.netting.carried_left == (CarriedLoss(2026, _D("2000")),)


def test_carried_loss_lowers_the_hermes_tax() -> None:
    """A 2025 loss of 1,000 shelters the first 1,000 of hermes gains."""
    taxpayer = TaxpayerYear(carried_losses=(CarriedLoss(2025, _D("1000")),))
    result = marginal_tax(SCALE_2026, taxpayer, _D("3000"), year=2026)
    assert result.hermes_tax == _D("380.00")
    assert result.with_hermes.netting.carried_used == (CarriedLoss(2025, _D("1000")),)


def test_expiring_carried_loss_is_reported() -> None:
    """A 2022 loss not fully used by a small 2026 gain expires."""
    taxpayer = TaxpayerYear(carried_losses=(CarriedLoss(2022, _D("1000")),))
    result = marginal_tax(SCALE_2026, taxpayer, _D("400"), year=2026)
    assert result.hermes_tax == _D("0")
    assert result.with_hermes.netting.expiring == (CarriedLoss(2022, _D("600")),)


def test_savings_tax_is_the_scale_less_the_scale_on_the_sheltered_part() -> None:
    """Base 1,500 with 100 of minimum: 1,500 * 0.19 - 100 * 0.19 = 285 - 19 = 266.00."""
    taxpayer = TaxpayerYear(other_savings_income=_D("500"), personal_minimum=_D("100"))
    result = savings_tax(SCALE_2026, taxpayer, _D("1000"), year=2026)
    assert (result.sheltered, result.tax) == (_D("100"), _D("266.00"))


_taxpayers = st.builds(
    TaxpayerYear,
    general_income=_amounts,
    personal_minimum=st.decimals(min_value=0, max_value=10_000, places=2, allow_nan=False),
    other_savings_income=_amounts,
    other_capital_gains=st.decimals(
        min_value=-400_000, max_value=400_000, places=2, allow_nan=False
    ),
    carried_losses=st.lists(
        st.builds(
            CarriedLoss,
            origin_year=st.integers(min_value=2020, max_value=2025),
            amount=_amounts,
        ),
        max_size=4,
    ).map(tuple),
)


@given(
    taxpayer=_taxpayers,
    hermes=st.decimals(min_value=0, max_value=400_000, places=2, allow_nan=False),
)
def test_marginal_tax_is_never_negative_when_hermes_net_is_positive(
    taxpayer: TaxpayerYear, hermes: Decimal
) -> None:
    """Gains can only add tax, whatever the operator's other figures are."""
    assert marginal_tax(SCALE_2026, taxpayer, hermes, year=2026).hermes_tax >= _D("0")


@given(taxpayer=_taxpayers, a=_amounts, b=_amounts)
def test_marginal_tax_is_monotonic_in_hermes_net(
    taxpayer: TaxpayerYear, a: Decimal, b: Decimal
) -> None:
    """More hermes gain never means less hermes tax, across gains and losses."""
    low, high = sorted((a - _D("200000"), b - _D("200000")))
    low_tax = marginal_tax(SCALE_2026, taxpayer, low, year=2026).hermes_tax
    high_tax = marginal_tax(SCALE_2026, taxpayer, high, year=2026).hermes_tax
    assert low_tax <= high_tax


@given(taxpayer=_taxpayers, hermes=_amounts)
def test_art_66_method_never_taxes_less_than_subtracting_the_minimum(
    taxpayer: TaxpayerYear, hermes: Decimal
) -> None:
    """Relieving the minimum at the bottom rates is >= cutting it from the top of the base.

    The old reading, ``scale(max(0, base - remainder))``, understates the tax whenever
    the base crosses a band limit; the art. 66.1.2 figure is never below it.
    """
    result = savings_tax(SCALE_2026, taxpayer, hermes, year=2026)
    base = result.netting.savings_base
    subtracted = SCALE_2026.tax(max(base - minimum_remainder(taxpayer), _D("0")))
    assert result.tax >= subtracted


@given(taxpayer=_taxpayers, hermes=_amounts)
def test_savings_tax_is_zero_while_the_minimum_covers_the_base(
    taxpayer: TaxpayerYear, hermes: Decimal
) -> None:
    """A savings base no larger than the minimum remainder owes nothing (art. 56.2, 66.1)."""
    result = savings_tax(SCALE_2026, taxpayer, hermes, year=2026)
    if result.netting.savings_base <= minimum_remainder(taxpayer):
        assert result.tax == _D("0")
