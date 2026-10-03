"""Reporting phase — the aggregate blind every notification consults.

Until the v1 out-of-sample certification (not before 2027-01-01), hermes
runs in shadow and no message may show how the strategy is doing. A
peek at outcomes during the out-of-sample window is unrecoverable
(integrity rule 1.8), and nothing in the experiment needs a human to see
them: shadow certification and the kill rule are computed by the engine.

While the phase is :attr:`ReportingPhase.PRE_CERTIFICATION`, messages
may show:

- service and integration health;
- signal events (entry and exit) with pair, side, price and bar time;
- kill-trigger state as a yes or no;
- calibration metrics M-09 and M-10 as pass or fail only.

Everything in :data:`OUTCOME_FIELD_NAMES` is suppressed, fail-closed:
a template that cannot tell which phase it is in renders blind.

Unblinding is an explicit step at certification: the composition root
passes :attr:`ReportingPhase.CERTIFIED` to the notifier adapters, which
log a warning when they are built unblinded. Nothing defaults to it.

Reference: ADR-012 ruling 1 (Code Owner ruling, 2026-10-02, option B);
    docs/experiments/swing-mvp1/integrity-checker-rules.md rule 1.8.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final

__all__ = [
    "DEFAULT_REPORTING_PHASE",
    "OUTCOME_FIELD_NAMES",
    "OUTCOME_WITHHELD_NOTICE",
    "ReportingPhase",
]


class ReportingPhase(StrEnum):
    """Which outcome figures a notification is allowed to carry.

    Members:
        PRE_CERTIFICATION: Shadow phase. Outcome figures are withheld.
        CERTIFIED: After v1 out-of-sample certification. Outcome figures
            may be shown.
    """

    PRE_CERTIFICATION = "pre_certification"
    CERTIFIED = "certified"

    @property
    def shows_outcomes(self) -> bool:
        """Return ``True`` only when outcome figures may be rendered."""
        return self is ReportingPhase.CERTIFIED


DEFAULT_REPORTING_PHASE: Final[ReportingPhase] = ReportingPhase.PRE_CERTIFICATION

OUTCOME_FIELD_NAMES: Final[frozenset[str]] = frozenset(
    {
        "pnl",
        "p&l",
        "return",
        "returns",
        "equity",
        "sharpe",
        "drawdown",
        "win rate",
        "win_rate",
        "winners",
        "losers",
    }
)

OUTCOME_WITHHELD_NOTICE: Final[str] = "Outcome figures are withheld until OOS certification."
