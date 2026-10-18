"""Missed-bar budget: ADR-021 §2 Done rule 2 for one shadow (issue #1117).

A shadow is Done only if at most 1% of its bars were missed over the 90
counted days. Each shadow runs on its own ledger (issue #1076), so its budget
is read from its own ``BarMissed`` rows and never from another shadow's. The
budget is a whole number of bars fixed up front: 1% of every bar the shadow's
streams close in 90 days, rounded down, so a run can only stay within it or
cross it, and what is left is known from the first day.

A bar re-handled after a crash can leave two ``BarMissed`` rows; the ledger
counts distinct bars, so it is spent once.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import timedelta
from typing import Final

from hermes.domain.market_data.bar_schedule import interval_duration
from hermes.domain.market_data.kline import KlineInterval

__all__ = [
    "BUDGET_SOURCE",
    "MISSED_BAR_PERCENT",
    "SHADOW_DAYS",
    "MissedBarBudget",
    "budget_crossed_report",
    "missed_bar_allowance",
]

MISSED_BAR_PERCENT: Final = 1
"""ADR-021 §2 Done rule 2: at most 1% of bars missed."""

SHADOW_DAYS: Final = 90
"""ADR-021 §2 Done rule 1: the counted days the budget covers."""

BUDGET_SOURCE: Final = "serve.missed_bar_budget"
"""``AnomalyDetected.source`` of the alert sent when a shadow crosses its budget."""


def missed_bar_allowance(intervals: Iterable[KlineInterval], *, days: int = SHADOW_DAYS) -> int:
    """Return how many bars a shadow may miss over ``days`` and still be Done.

    Args:
        intervals: The interval of every stream the shadow evaluates, one
            entry per pair and interval.
        days: The counted days the budget covers.

    Returns:
        1% of every bar the streams close in ``days``, rounded down.
    """
    span = timedelta(days=days)
    bars = sum(span // interval_duration(interval) for interval in intervals)
    return bars * MISSED_BAR_PERCENT // 100


@dataclass(frozen=True, slots=True)
class MissedBarBudget:
    """How much of one shadow's missed-bar budget is spent.

    Attributes:
        allowed: Bars the shadow may miss and still be Done.
        missed: Distinct bars its ledger records as missed.
    """

    allowed: int
    missed: int

    @property
    def left(self) -> int:
        """Return the bars still allowed, never below 0."""
        return max(self.allowed - self.missed, 0)

    @property
    def crossed(self) -> bool:
        """Return whether the shadow missed more bars than Done allows."""
        return self.missed > self.allowed

    def summary(self) -> str:
        """Return one line naming the bars missed, the budget and what is left."""
        return f"missed-bar budget: {self.missed} of {self.allowed} bars used, {self.left} left"


def budget_crossed_report(budget: MissedBarBudget) -> str:
    """Return the alert text for a shadow that has just crossed its budget.

    Args:
        budget: The crossed budget.

    Returns:
        What was crossed and what it means for the run; never a price or P&L.
    """
    return (
        f"missed-bar budget crossed: {budget.missed} bars missed, "
        f"{budget.allowed} allowed over {SHADOW_DAYS} days (ADR-021 §2 Done rule 2); "
        "this run can no longer be Done"
    )
