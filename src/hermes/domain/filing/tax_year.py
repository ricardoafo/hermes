"""The tax year: the calendar year in Europe/Madrid time (ADR-016 item 2).

Defined once in :mod:`hermes.domain.tax.tax_calendar` (issue #1171); the filing
package re-exports it so its callers keep one import path.
"""

from hermes.domain.tax.tax_calendar import tax_year_bounds, tax_year_of

__all__ = ["tax_year_bounds", "tax_year_of"]
