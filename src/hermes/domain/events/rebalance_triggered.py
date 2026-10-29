"""RebalanceTriggered domain event — emitted when the rebalancer decides to trade.

Carries the fact that the rebalancer cycle detected drift beyond the
allowed band and produced a trade plan. The event captures the pair, the
drift magnitude, and the band threshold so downstream subscribers can
audit or notify without reading the ledger row.

Reference: ADR-003 §02 flow 1 (rebalancer cycle), code-conventions.md §10.
"""

from dataclasses import dataclass
from typing import ClassVar

from hermes.domain.events.base import DomainEvent
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.weight import Weight

__all__ = ["RebalanceTriggered"]


@dataclass(frozen=True, slots=True)
class RebalanceTriggered(DomainEvent):
    """The rebalancer decided the portfolio needs a corrective trade.

    Emitted when observed drift for a pair exceeds ``band``. The event
    precedes any exchange call — subscribers must not assume the trade has
    already executed (a ``TradeExecuted`` event follows on fill).

    Fields:
        pair: The trading pair whose weight breached the band.
        current_weight: Observed portfolio weight for the pair.
        target_weight: Configured target weight for the pair.
        band: Absolute drift threshold that triggered the rebalance
            (e.g. 0.10 for a 10% band). Positive weight in [0, 1].

    Reference: ADR-003 §02 — "drift exceeds band" branch of flow 1.
    """

    __event_schema_version__: ClassVar[int] = 1

    pair: Pair
    current_weight: Weight
    target_weight: Weight
    band: Weight
