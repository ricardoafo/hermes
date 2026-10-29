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

    @property
    def severity(self) -> int:
        """Return the rank in the severity order: OK 0, WARN 1, HALT 2.

        The order is the declaration order, so a new action ranks by where it
        is declared and no second table can disagree (issue #1179).
        """
        return _ORDER.index(self)

    def is_worse_than(self, other: RiskAction) -> bool:
        """Return ``True`` when this action has higher severity than *other*.

        Args:
            other: The action to compare against.

        Returns:
            ``True`` if ``self`` is more severe than ``other``.
        """
        return self.severity > other.severity


_ORDER: tuple[RiskAction, ...] = tuple(RiskAction)


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
