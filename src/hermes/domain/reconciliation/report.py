"""ReconciliationReport — one month's checks, tax view and verdict.

The verdict is what the operator acts on:

- ``CLEAN``: every view agrees with the book and every trade has its EUR
  values. Once the month has ended the month-end lot cut may proceed.
- ``REVIEW``: the views agree but trades are still waiting for an ECB rate,
  so the month's EUR figures are incomplete. Run the backfill, then re-check.
- ``DRIFT``: a view disagrees with the book beyond the tolerance, or the book
  itself cannot be replayed. A human resolves it; nothing is auto-healed
  (ADR-003 §10).

Reference: openspec/changes/35-monthly-reconciliation/change.md D-5.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from hermes.domain.reconciliation.holdings import CheckStatus, HoldingCheck
from hermes.domain.reconciliation.month import ReconciliationMonth
from hermes.domain.reconciliation.tax_view import TaxView

__all__ = ["ReconciliationReport", "Verdict"]


class Verdict(StrEnum):
    """What the operator does next."""

    CLEAN = "clean"
    REVIEW = "review"
    DRIFT = "drift"


@dataclass(frozen=True, slots=True)
class ReconciliationReport:
    """Everything one reconciliation run found.

    Attributes:
        month: The month covered.
        as_of: The instant the views were taken at: the month end for a past
            month, the run time for the current one.
        checks: Every per-asset comparison with the book.
        tax: The month's tax view; ``None`` when the book could not be replayed.
        problems: Findings that are not a per-asset number, such as a book
            that sells more than it holds. Any problem means drift.
        skipped: Views that were not compared, each with the reason.
    """

    month: ReconciliationMonth
    as_of: datetime
    checks: tuple[HoldingCheck, ...]
    tax: TaxView | None
    problems: tuple[str, ...] = ()
    skipped: tuple[str, ...] = ()

    @property
    def drifts(self) -> tuple[HoldingCheck, ...]:
        """The checks whose difference exceeds the tolerance."""
        return tuple(c for c in self.checks if c.status is CheckStatus.DRIFT)

    @property
    def verdict(self) -> Verdict:
        """DRIFT on any drift or problem, REVIEW on pending EUR values, else CLEAN."""
        if self.drifts or self.problems or self.tax is None:
            return Verdict.DRIFT
        if self.tax.pending_trades:
            return Verdict.REVIEW
        return Verdict.CLEAN

    @property
    def month_closed(self) -> bool:
        """True when the views were taken at the month end, not part way through."""
        return self.as_of >= self.month.end

    @property
    def ready_for_cut(self) -> bool:
        """True when the month has ended and the verdict is CLEAN."""
        return self.month_closed and self.verdict is Verdict.CLEAN
