"""Tests for savings-base netting and carry-forward (change tax-estimates R-2).

Reference: ADR-016 decision 6, IRPF art. 49.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hermes.domain.tax.netting import (
    CARRY_FORWARD_YEARS,
    CarriedLoss,
    TaxpayerYear,
    net_savings_base,
)

_D = Decimal
_amounts = st.decimals(min_value=0, max_value=100_000, places=2, allow_nan=False)
_signed = st.decimals(min_value=-100_000, max_value=100_000, places=2, allow_nan=False)


def test_gains_and_losses_net_into_one_balance() -> None:
    """Hermes net and other gains add up before anything else."""
    result = net_savings_base(2026, _D("1000"), TaxpayerYear(other_capital_gains=_D("-400")))
    assert result.savings_base == _D("600")


def test_net_loss_offsets_at_most_25_percent_of_savings_income() -> None:
    """A 10,000 loss against 2,000 of interest absorbs 500; 9,500 carries forward."""
    result = net_savings_base(2026, _D("-10000"), TaxpayerYear(other_savings_income=_D("2000")))
    assert result.savings_base == _D("1500")
    assert result.carried_left == (CarriedLoss(2026, _D("9500")),)


def test_small_net_loss_is_fully_absorbed_by_savings_income() -> None:
    """A loss under the 25% room leaves nothing to carry."""
    result = net_savings_base(2026, _D("-300"), TaxpayerYear(other_savings_income=_D("2000")))
    assert (result.savings_base, result.carried_left) == (_D("1700"), ())


def test_carried_losses_offset_gains_oldest_first() -> None:
    """The 2022 loss is used before the 2024 loss."""
    taxpayer = TaxpayerYear(
        carried_losses=(CarriedLoss(2024, _D("500")), CarriedLoss(2022, _D("800")))
    )
    result = net_savings_base(2026, _D("1000"), taxpayer)
    assert result.savings_base == _D("0")
    assert result.carried_used == (CarriedLoss(2022, _D("800")), CarriedLoss(2024, _D("200")))
    assert result.carried_left == (CarriedLoss(2024, _D("300")),)


def test_carried_loss_beyond_gains_uses_the_25_percent_room_of_savings_income() -> None:
    """After gains are exhausted a carried loss eats into interest, capped at 25%."""
    taxpayer = TaxpayerYear(
        other_savings_income=_D("1000"), carried_losses=(CarriedLoss(2025, _D("2000")),)
    )
    result = net_savings_base(2026, _D("100"), taxpayer)
    assert result.savings_base == _D("750")
    assert result.carried_used == (CarriedLoss(2025, _D("350")),)
    assert result.carried_left == (CarriedLoss(2025, _D("1650")),)


def test_the_25_percent_room_is_shared_by_current_and_carried_losses() -> None:
    """A current loss that used 200 of the 250 room leaves 50 for carried losses."""
    taxpayer = TaxpayerYear(
        other_savings_income=_D("1000"), carried_losses=(CarriedLoss(2025, _D("500")),)
    )
    result = net_savings_base(2026, _D("-200"), taxpayer)
    assert result.savings_base == _D("750")
    assert result.carried_used == (CarriedLoss(2025, _D("50")),)


def test_unused_loss_from_four_years_ago_expires_this_year() -> None:
    """A 2022 loss is usable for the last time in 2026; what is left expires."""
    taxpayer = TaxpayerYear(carried_losses=(CarriedLoss(2022, _D("1000")),))
    result = net_savings_base(2026, _D("300"), taxpayer)
    assert result.carried_used == (CarriedLoss(2022, _D("300")),)
    assert result.expiring == (CarriedLoss(2022, _D("700")),)
    assert result.carried_left == ()


def test_loss_older_than_four_years_is_ignored() -> None:
    """A 2021 loss cannot offset 2026 gains."""
    taxpayer = TaxpayerYear(carried_losses=(CarriedLoss(2021, _D("1000")),))
    result = net_savings_base(2026, _D("300"), taxpayer)
    assert result.savings_base == _D("300")
    assert result.expired == (CarriedLoss(2021, _D("1000")),)


def test_carried_loss_of_the_same_year_is_not_applied() -> None:
    """Only losses from earlier years carry into the year."""
    taxpayer = TaxpayerYear(carried_losses=(CarriedLoss(2026, _D("1000")),))
    assert net_savings_base(2026, _D("300"), taxpayer).savings_base == _D("300")


@given(
    year=st.integers(min_value=2010, max_value=2090),
    hermes=_signed,
    other=_signed,
    income=_amounts,
    carried=st.lists(st.tuples(st.integers(min_value=-8, max_value=0), _amounts), max_size=6),
)
def test_carry_forward_never_exceeds_four_years(
    year: int,
    hermes: Decimal,
    other: Decimal,
    income: Decimal,
    carried: list[tuple[int, Decimal]],
) -> None:
    """Losses used are at most 4 years old; losses left are still usable next year."""
    taxpayer = TaxpayerYear(
        other_savings_income=income,
        other_capital_gains=other,
        carried_losses=tuple(CarriedLoss(year + delta, amount) for delta, amount in carried),
    )
    result = net_savings_base(year, hermes, taxpayer)
    assert all(year - c.origin_year <= CARRY_FORWARD_YEARS for c in result.carried_used)
    assert all(year + 1 - c.origin_year <= CARRY_FORWARD_YEARS for c in result.carried_left)


def test_repr_hides_every_profile_value() -> None:
    """Profile figures never reach logs through repr (ADR-016 decision 12)."""
    taxpayer = TaxpayerYear(
        general_income=_D("31337"),
        personal_minimum=_D("5550"),
        other_savings_income=_D("4242"),
        other_capital_gains=_D("-777"),
        carried_losses=(CarriedLoss(2024, _D("999")),),
    )
    result = net_savings_base(2026, _D("1"), taxpayer)
    text = repr(taxpayer) + repr(taxpayer.carried_losses) + repr(result)
    for secret in ("31337", "5550", "4242", "777", "999", "2024"):
        assert secret not in text


@pytest.mark.parametrize("field", ["general_income", "personal_minimum", "other_savings_income"])
def test_taxpayer_rejects_negative_amounts_without_echoing_them(field: str) -> None:
    """Only other gains may be negative; the error names the field, not the value."""
    with pytest.raises(ValueError, match=field) as raised:
        TaxpayerYear(**{field: _D("-123.45")})
    assert "123.45" not in str(raised.value)


def test_carried_loss_rejects_a_negative_amount() -> None:
    """A carried loss is a magnitude."""
    with pytest.raises(ValueError, match="non-negative"):
        CarriedLoss(2024, _D("-1"))
