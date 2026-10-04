"""Optional total IRPF view: general-base tax plus savings tax (pure).

The general base is taxed twice, once on the state scale and once on the
regional scale (Ley 35/2006 arts. 63 and 74). For each scale the tax is the
scale applied to the whole general liquidable base, less the same scale applied
to the part of that base the personal minimum covers (art. 63.1.2 and 74.1.2).
The minimum goes to the general base first; what the general base cannot absorb
shelters savings (art. 56.2), which the savings computation of #395 already does.

The view is built from the :class:`MarginalTax` the hermes estimate computed,
so it can never change the hermes figure. Its values derive from the private
profile and are excluded from ``repr``.

Reference: ADR-016 item 11, change ``total-irpf-view``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Final

from hermes.domain.tax.marginal import MarginalTax
from hermes.domain.tax.netting import TaxpayerYear
from hermes.domain.tax.precision import exact
from hermes.domain.tax.scale import SavingsScale

__all__ = [
    "UNAVAILABLE_PREFIX",
    "GeneralScales",
    "TotalIrpf",
    "TotalIrpfUnavailable",
    "general_tax",
    "total_irpf",
]

UNAVAILABLE_PREFIX: Final[str] = "total IRPF unavailable"
_ZERO: Final[Decimal] = Decimal(0)


@dataclass(frozen=True, slots=True)
class GeneralScales:
    """The state and regional general-base scales of one year.

    Both use the band arithmetic of :class:`SavingsScale`: cumulative limits,
    ascending, open top band.

    Attributes:
        state: State scale (art. 63).
        regional: The region's scale (art. 74).
    """

    state: SavingsScale
    regional: SavingsScale


def general_tax(scales: GeneralScales, general_base: Decimal, personal_minimum: Decimal) -> Decimal:
    """Return the state plus regional tax on the general base.

    Args:
        scales: The year's state and regional scales.
        general_base: General liquidable base; below zero counts as zero.
        personal_minimum: Personal and family minimum, the same for both halves.

    Returns:
        ``sum(scale.tax(base) - scale.tax(min(minimum, base)))`` over both scales.
    """
    base = max(general_base, _ZERO)
    sheltered = min(max(personal_minimum, _ZERO), base)
    with exact():
        return sum(
            (scale.tax(base) - scale.tax(sheltered) for scale in (scales.state, scales.regional)),
            start=_ZERO,
        )


@dataclass(frozen=True, slots=True)
class TotalIrpf:
    """The year's IRPF quota (before quota deductions), with the hermes share.

    Attributes:
        general_tax: State plus regional tax on the general base.
        savings_tax: Full savings tax of the year, hermes included.
        hermes_share: The hermes tax: savings tax with hermes minus without.
        total: ``general_tax + savings_tax``.
    """

    general_tax: Decimal = field(repr=False)
    savings_tax: Decimal = field(repr=False)
    hermes_share: Decimal = field(repr=False)
    total: Decimal = field(repr=False)


@dataclass(frozen=True, slots=True)
class TotalIrpfUnavailable:
    """The view is on but cannot be computed; never replaced by a guess.

    Attributes:
        reason: Why, e.g. ``no scale for murcia 2027``.
    """

    reason: str

    @property
    def label(self) -> str:
        """Return the line outputs print: ``total IRPF unavailable: <reason>``."""
        return f"{UNAVAILABLE_PREFIX}: {self.reason}"


def total_irpf(scales: GeneralScales, taxpayer: TaxpayerYear, marginal: MarginalTax) -> TotalIrpf:
    """Return the total IRPF of the year from the hermes marginal computation.

    Args:
        scales: The year's state and regional general-base scales.
        taxpayer: The operator's figures for the year (general base and minimum).
        marginal: The hermes marginal tax already computed for the year.

    Returns:
        General tax, savings tax with hermes, the hermes share and the total.
    """
    general = general_tax(scales, taxpayer.general_base, taxpayer.personal_minimum)
    savings = marginal.with_hermes.tax
    with exact():
        total = general + savings
    return TotalIrpf(
        general_tax=general,
        savings_tax=savings,
        hermes_share=marginal.hermes_tax,
        total=total,
    )
