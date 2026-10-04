"""Marginal tax of hermes: tax with hermes minus tax without it.

The band hermes gains land in depends on the operator's other savings income,
other gains and carried losses (ADR-016 decision 7), so the hermes figure is
the difference between two full computations of the savings tax.

Personal minimum (IRPF arts. 56.2 and 66.1): the minimum is applied to the
general base first; whatever the general base cannot absorb forms part of the
savings base. Art. 66.1.2: the scale is applied to the whole savings base, and
the result is reduced by the same scale applied to the part of the base that
corresponds to the minimum. The relief is therefore at the scale's bottom rates,
not a cut from the top of the base.

Reference: ADR-016 decisions 6 and 7, change ``tax-estimates`` D-1.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Final

from hermes.domain.tax.netting import NettingResult, TaxpayerYear, net_savings_base
from hermes.domain.tax.precision import exact
from hermes.domain.tax.scale import SavingsScale

__all__ = ["MarginalTax", "SavingsTax", "marginal_tax", "minimum_remainder", "savings_tax"]

_ZERO: Final[Decimal] = Decimal(0)


def minimum_remainder(taxpayer: TaxpayerYear) -> Decimal:
    """Return the part of the personal minimum the general base cannot absorb.

    Args:
        taxpayer: Supplies the general base and the personal minimum.

    Returns:
        ``max(0, personal_minimum - general_base)`` (IRPF art. 56.2), where
        ``general_base`` is general income less the operator's deductions.
    """
    with exact():
        return max(taxpayer.personal_minimum - taxpayer.general_base, _ZERO)


@dataclass(frozen=True, slots=True)
class SavingsTax:
    """Savings tax of one scenario. Values derive from the private profile.

    Attributes:
        netting: Netting outcome of the scenario.
        sheltered: Part of the savings base that corresponds to the minimum:
            ``min(minimum remainder, savings base)``.
        tax: ``scale(savings base) - scale(sheltered)`` (art. 66.1.2).
    """

    netting: NettingResult = field(repr=False)
    sheltered: Decimal = field(repr=False)
    tax: Decimal = field(repr=False)


def savings_tax(
    scale: SavingsScale, taxpayer: TaxpayerYear, hermes_net: Decimal, *, year: int
) -> SavingsTax:
    """Return the full savings tax of ``year`` with ``hermes_net`` added.

    Args:
        scale: The year's savings scale.
        taxpayer: The operator's other income for the year.
        hermes_net: Hermes net gain of the year.
        year: Tax year.

    Returns:
        The netting, the sheltered part of the base and the tax.
    """
    netting = net_savings_base(year, hermes_net, taxpayer)
    base = netting.savings_base
    sheltered = min(minimum_remainder(taxpayer), base)
    with exact():
        tax = max(scale.tax(base) - scale.tax(sheltered), _ZERO)
    return SavingsTax(netting=netting, sheltered=sheltered, tax=tax)


@dataclass(frozen=True, slots=True)
class MarginalTax:
    """Hermes share of the year's savings tax.

    Attributes:
        hermes_tax: Tax with hermes minus tax without; negative is a credit.
        with_hermes: The scenario including hermes.
        without_hermes: The scenario with hermes net set to zero.
    """

    hermes_tax: Decimal
    with_hermes: SavingsTax = field(repr=False)
    without_hermes: SavingsTax = field(repr=False)


def marginal_tax(
    scale: SavingsScale, taxpayer: TaxpayerYear, hermes_net: Decimal, *, year: int
) -> MarginalTax:
    """Return the tax hermes adds to (or removes from) the operator's savings tax.

    Args:
        scale: The year's savings scale.
        taxpayer: The operator's other income for the year.
        hermes_net: Hermes net gain of the year; negative is a net loss.
        year: Tax year.

    Returns:
        The marginal figure and both scenarios.
    """
    with_hermes = savings_tax(scale, taxpayer, hermes_net, year=year)
    without_hermes = savings_tax(scale, taxpayer, _ZERO, year=year)
    with exact():
        difference = with_hermes.tax - without_hermes.tax
    return MarginalTax(
        hermes_tax=difference, with_hermes=with_hermes, without_hermes=without_hermes
    )
