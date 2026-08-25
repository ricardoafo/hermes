"""Risk decision types — action enum and decision dataclass.

Lives in the domain layer so rule modules can import them without
crossing the domain→ports boundary (which is forbidden by the
``domain-is-pure`` import-linter contract).

``RiskOverlayPort`` (the Protocol) lives in :mod:`hermes.ports.risk_overlay`
and imports these types from the domain — the dependency flows outward,
not inward.

Reference: spec-8-risk-overlay change.md § Design; import-linter contract
``domain-is-pure``.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

__all__ = [
    "RiskAction",
    "RiskDecision",
]


class RiskAction(enum.Enum):
    """Possible outcomes of a risk rule evaluation.

    Severity order (ascending): OK < WARN < HALT.
    Composition always takes the highest-severity action across all rules.
    """

    OK = "ok"
    WARN = "warn"
    HALT = "halt"

    def is_worse_than(self, other: RiskAction) -> bool:
        """Return ``True`` when this action has higher severity than *other*.

        Args:
            other: The action to compare against.

        Returns:
            ``True`` if ``self`` is more severe than ``other``.
        """
        _severity: dict[RiskAction, int] = {
            RiskAction.OK: 0,
            RiskAction.WARN: 1,
            RiskAction.HALT: 2,
        }
        return _severity[self] > _severity[other]


@dataclass(frozen=True, slots=True)
class RiskDecision:
    """Outcome of a single risk rule evaluation.

    Args:
        action: The risk action decided by the rule — :attr:`RiskAction.OK`,
            :attr:`RiskAction.WARN`, or :attr:`RiskAction.HALT`.
        reason: Human-readable description of why the rule fired (or
            confirmed OK). Always non-empty — even OK decisions include
            the rule identifier for auditability.
    """

    action: RiskAction
    reason: str
