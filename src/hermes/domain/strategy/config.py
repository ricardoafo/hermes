"""StrategyConfig — configuration dataclass for strategy adapters.

Carries the capital budget, OMS routing type, and lifecycle status for a
strategy adapter instance.  Immutable (frozen dataclass) so it can be
passed safely across layers without mutation risk.

Reference: code-conventions.md §1 (Value Object).
spec-5-swing-strategy-adapter change.md SA-6.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from hermes.domain.value_objects.money import Money

__all__ = ["OmsType", "StrategyConfig", "StrategyStatus"]


class StrategyStatus(StrEnum):
    """Lifecycle status of a strategy instance.

    Reference: SA-6 strategy lifecycle.
    """

    ACTIVE = "active"
    PAUSED = "paused"
    DISABLED = "disabled"


class OmsType(StrEnum):
    """Order management system routing target.

    Reference: SA-6 OMS selection.
    """

    PAPER = "paper"
    LIVE = "live"


@dataclass(frozen=True, slots=True)
class StrategyConfig:
    """Immutable configuration for a strategy adapter.

    Args:
        capital_budget: Maximum capital the strategy may deploy at any
            one time.  The OrderRouter uses this to cap position notional.
            Maps to pre-reg §5.4 (hard cap $250 per position / 5% of $5000
            experiment capital).
        oms_type: Where orders are sent — ``paper`` for simulation or
            ``live`` for real execution.
        status: Lifecycle gate.  Only ``active`` strategies emit signals
            that the router will act on.

    Reference: spec-5-swing-strategy-adapter change.md SA-6.
    """

    capital_budget: Money
    oms_type: OmsType | str
    status: StrategyStatus | str
