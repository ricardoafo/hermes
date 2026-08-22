"""Domain events package.

Re-exports the base class, schema-version type alias, every concrete
event, and the in-process ``EventBus``.

Concrete events are pure Python ``@dataclass(frozen=True, slots=True)``
with a ``ClassVar[int]`` schema version — no pydantic, no I/O, no async.
The bus lives here (not in ``application/``) because it is pure Python
and every producer/consumer references it — putting it in the domain
keeps the dependency arrow pointing inward per invariant I-04.

Reference: code-conventions.md §10 — Event schema versioning + upcaster chain.
    code-conventions.md §1 — Ports & Adapters (bus stays pure).
"""

from hermes.domain.events.anomaly_detected import AnomalyDetected, AnomalySeverity
from hermes.domain.events.base import DomainEvent, SchemaVersion
from hermes.domain.events.event_bus import EventBus, EventDispatchError, Handler
from hermes.domain.events.lot_closed import LotClosed
from hermes.domain.events.rebalance_triggered import RebalanceTriggered
from hermes.domain.events.trade_executed import TradeExecuted, TradeSide

__all__ = [
    "AnomalyDetected",
    "AnomalySeverity",
    "DomainEvent",
    "EventBus",
    "EventDispatchError",
    "Handler",
    "LotClosed",
    "RebalanceTriggered",
    "SchemaVersion",
    "TradeExecuted",
    "TradeSide",
]
