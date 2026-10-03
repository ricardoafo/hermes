"""Spanish IRPF savings-tax estimates for hermes gains (pure).

- :class:`SavingsScale` — the year's savings bands from config; ``tax(base)``.
- :class:`YearPosition` — hermes gains and losses by Madrid tax year.
- :func:`net_savings_base` — netting, the 25% cross-offset and 4-year carry-forward.
- :func:`marginal_tax` — hermes tax as tax with hermes minus tax without.
- :func:`two_month_flags` — loss sales with a nearby repurchase, flagged only.

Measurement only: strategy, sizing, risk, fee and kill-rule code never imports
this package (import-linter contract ``tax-is-measurement-only``).

Reference: ADR-016, change ``tax-estimates``.
"""

from hermes.domain.tax.marginal import (
    MarginalTax,
    SavingsTax,
    marginal_tax,
    savings_tax,
    taxable_savings_base,
)
from hermes.domain.tax.netting import (
    CARRY_FORWARD_YEARS,
    CROSS_OFFSET_SHARE,
    CarriedLoss,
    NettingResult,
    TaxpayerYear,
    net_savings_base,
)
from hermes.domain.tax.scale import BandPosition, SavingsBand, SavingsScale
from hermes.domain.tax.two_month import Acquisition, TwoMonthFlag, add_months, two_month_flags
from hermes.domain.tax.year_position import Disposal, YearPosition, tax_year_of

__all__ = [
    "CARRY_FORWARD_YEARS",
    "CROSS_OFFSET_SHARE",
    "Acquisition",
    "BandPosition",
    "CarriedLoss",
    "Disposal",
    "MarginalTax",
    "NettingResult",
    "SavingsBand",
    "SavingsScale",
    "SavingsTax",
    "TaxpayerYear",
    "TwoMonthFlag",
    "YearPosition",
    "add_months",
    "marginal_tax",
    "net_savings_base",
    "savings_tax",
    "tax_year_of",
    "taxable_savings_base",
    "two_month_flags",
]
