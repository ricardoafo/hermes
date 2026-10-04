"""Tests for the total IRPF view (change total-irpf-view R-2, R-3, R-5).

Worked examples use Región de Murcia 2025/2026 and the state scale, with the
state personal minimum 5,550 (Ley 35/2006 arts. 57.1, 63.1 and 74.1; Decreto
Legislativo 1/2010 de la Región de Murcia art. 2). Salaries are synthetic.

Scale applied to the minimum, both halves (the first band covers 5,550):
5,550 * 0.095 = 527.25.

Reference: ADR-016 item 11.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hermes.domain.tax.marginal import marginal_tax
from hermes.domain.tax.netting import TaxpayerYear
from hermes.domain.tax.total import (
    GeneralScales,
    TotalIrpf,
    TotalIrpfUnavailable,
    general_tax,
    total_irpf,
)
from tests.unit.domain.tax.scales import MURCIA_GENERAL_2026, SCALE_2026, STATE_GENERAL_2026

_D = Decimal
_MURCIA = GeneralScales(state=STATE_GENERAL_2026, regional=MURCIA_GENERAL_2026)
_MINIMUM = _D("5550")
_bases = st.decimals(min_value=0, max_value=1_000_000, places=2, allow_nan=False)


def test_general_tax_at_20000() -> None:
    """Base 20,000 -> 3,062.60.

    State: 1,182.75 + (20,000 - 12,450) * 0.12 = 1,182.75 + 906.00 = 2,088.75;
    less 527.25 = 1,561.50.
    Murcia: 1,182.75 + 7,550 * 0.112 = 1,182.75 + 845.60 = 2,028.35; less 527.25 = 1,501.10.
    Total 1,561.50 + 1,501.10 = 3,062.60.
    """
    assert general_tax(_MURCIA, _D("20000"), _MINIMUM) == _D("3062.60")


def test_general_tax_at_35000() -> None:
    """Base 35,000 -> 7,343.40.

    State: 2,112.75 + (35,000 - 20,200) * 0.15 = 2,112.75 + 2,220.00 = 4,332.75;
    less 527.25 = 3,805.50.
    Murcia: 3,886.15 + (35,000 - 34,000) * 0.179 = 3,886.15 + 179.00 = 4,065.15;
    less 527.25 = 3,537.90.
    Total 3,805.50 + 3,537.90 = 7,343.40.
    """
    assert general_tax(_MURCIA, _D("35000"), _MINIMUM) == _D("7343.40")


def test_general_tax_at_60000() -> None:
    """Base 60,000 -> 16,436.40.

    State: cumulative quota at 60,000 is 8,950.75; less 527.25 = 8,423.50.
    Murcia: cumulative quota at 60,000 is 8,540.15; less 527.25 = 8,012.90.
    Total 8,423.50 + 8,012.90 = 16,436.40.
    """
    assert general_tax(_MURCIA, _D("60000"), _MINIMUM) == _D("16436.40")


def test_general_tax_at_100000() -> None:
    """Base 100,000 -> 34,436.40.

    State: 8,950.75 + 40,000 * 0.225 = 17,950.75; less 527.25 = 17,423.50.
    Murcia: 8,540.15 + 40,000 * 0.225 = 17,540.15; less 527.25 = 17,012.90.
    Total 17,423.50 + 17,012.90 = 34,436.40.
    """
    assert general_tax(_MURCIA, _D("100000"), _MINIMUM) == _D("34436.40")


def test_general_tax_is_zero_below_the_minimum() -> None:
    """Base 3,000 < 5,550: the minimum covers the whole base, so each half is 0."""
    assert general_tax(_MURCIA, _D("3000"), _MINIMUM) == _D(0)


def test_minimum_shelters_the_bottom_band_not_the_top() -> None:
    """The minimum is relieved at the scale's bottom rates (art. 63.1.2), not removed from the base.

    Base 70,000, minimum 10,000; the scale on the minimum is 10,000 * 0.095 = 950.00.
    State: 8,950.75 + 10,000 * 0.225 = 11,200.75; less 950.00 = 10,250.75.
    Murcia: 8,540.15 + 10,000 * 0.225 = 10,790.15; less 950.00 = 9,840.15.
    Total 20,090.90. Taxing 70,000 - 10,000 = 60,000 instead would give
    8,950.75 + 8,540.15 = 17,490.90, which is not the law.
    """
    assert general_tax(_MURCIA, _D("70000"), _D("10000")) == _D("20090.90")


@given(low=_bases, high=_bases, minimum=_bases)
def test_general_tax_never_falls_as_the_base_grows(
    low: Decimal, high: Decimal, minimum: Decimal
) -> None:
    """Progressive scales: a higher base never owes less, and no base owes below zero."""
    low, high = sorted((low, high))
    assert _D(0) <= general_tax(_MURCIA, low, minimum) <= general_tax(_MURCIA, high, minimum)


@given(base=_bases, minimum=_bases)
def test_general_tax_is_zero_up_to_the_minimum(base: Decimal, minimum: Decimal) -> None:
    """While the minimum covers the base (art. 56.2) the general tax is exactly zero."""
    if base <= minimum:
        assert general_tax(_MURCIA, base, minimum) == _D(0)


def test_total_adds_the_full_savings_tax_and_reports_the_hermes_share() -> None:
    """Base 35,000, hermes gain 10,000, no other savings.

    General 7,343.40 (worked above). The salary absorbs the minimum, so savings
    tax = 6,000 * 0.19 + 4,000 * 0.21 = 1,140 + 840 = 1,980.00, all of it hermes.
    Total 7,343.40 + 1,980.00 = 9,323.40.
    """
    taxpayer = TaxpayerYear(general_income=_D("35000"), personal_minimum=_MINIMUM)
    marginal = marginal_tax(SCALE_2026, taxpayer, _D("10000"), year=2026)
    view = total_irpf(_MURCIA, taxpayer, marginal)
    assert (view.general_tax, view.savings_tax, view.hermes_share, view.total) == (
        _D("7343.40"),
        _D("1980.00"),
        _D("1980.00"),
        _D("9323.40"),
    )


def test_minimum_left_over_by_a_low_salary_shelters_savings() -> None:
    """Base 3,000, hermes gain 5,000: 2,550 of the minimum moves to savings (art. 56.2).

    General 0. Savings: 5,000 * 0.19 - 2,550 * 0.19 = 950.00 - 484.50 = 465.50
    (the whole savings base sits in the 19% band). Total 465.50.
    """
    taxpayer = TaxpayerYear(general_income=_D("3000"), personal_minimum=_MINIMUM)
    marginal = marginal_tax(SCALE_2026, taxpayer, _D("5000"), year=2026)
    view = total_irpf(_MURCIA, taxpayer, marginal)
    assert (view.general_tax, view.savings_tax, view.total) == (
        _D(0),
        _D("465.50"),
        _D("465.50"),
    )


def test_deductions_lower_the_general_base() -> None:
    """Income 37,000 less 2,000 of deductions taxes a 35,000 base: 7,343.40."""
    taxpayer = TaxpayerYear(
        general_income=_D("37000"), general_deductions=_D("2000"), personal_minimum=_MINIMUM
    )
    marginal = marginal_tax(SCALE_2026, taxpayer, _D(0), year=2026)
    assert total_irpf(_MURCIA, taxpayer, marginal).general_tax == _D("7343.40")


@given(income=_bases, other=_bases, hermes=_bases)
def test_total_is_general_plus_savings_and_share_is_the_hermes_tax(
    income: Decimal, other: Decimal, hermes: Decimal
) -> None:
    """The view only adds: total = general + savings, hermes share = marginal hermes tax."""
    taxpayer = TaxpayerYear(
        general_income=income, personal_minimum=_MINIMUM, other_savings_income=other
    )
    marginal = marginal_tax(SCALE_2026, taxpayer, hermes, year=2026)
    view = total_irpf(_MURCIA, taxpayer, marginal)
    assert view.total == view.general_tax + view.savings_tax
    assert view.hermes_share == marginal.hermes_tax


def test_repr_hides_profile_derived_figures() -> None:
    """Every figure derives from the private profile (ADR-016 item 12)."""
    view = TotalIrpf(
        general_tax=_D("7343.40"),
        savings_tax=_D("1980.00"),
        hermes_share=_D("1980.00"),
        total=_D("9323.40"),
    )
    assert "7343" not in repr(view)
    assert "9323" not in repr(view)


@pytest.mark.parametrize(
    "reason", ["no scale for murcia 2027", "no region in the taxpayer profile"]
)
def test_unavailable_label_names_the_reason(reason: str) -> None:
    """The fail-closed line is fixed text plus the reason, never a number."""
    assert TotalIrpfUnavailable(reason).label == f"total IRPF unavailable: {reason}"
