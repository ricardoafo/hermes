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
from hermes.domain.events.balance_low import BalanceLow
from hermes.domain.events.bar_processed import BarProcessed
from hermes.domain.events.base import DomainEvent, SchemaVersion
from hermes.domain.events.daily_trade_digest import DailyTradeDigest, TradeSummary
from hermes.domain.events.event_bus import EventBus, EventDispatchError, Handler
from hermes.domain.events.lot_closed import LotClosed
from hermes.domain.events.rebalance_triggered import RebalanceTriggered
from hermes.domain.events.shadow_capital_seeded import ShadowCapitalSeeded
from hermes.domain.events.signal_emitted import SignalEmitted
from hermes.domain.events.strategy_paused import StrategyPaused
from hermes.domain.events.strategy_resumed import StrategyResumed
from hermes.domain.events.trade_executed import TradeExecuted, TradeSide
from hermes.domain.events.trade_rejected import TradeRejected

__all__ = [
    "AnomalyDetected",
    "AnomalySeverity",
    "BalanceLow",
    "BarProcessed",
    "DailyTradeDigest",
    "DomainEvent",
    "EventBus",
    "EventDispatchError",
    "Handler",
    "LotClosed",
    "RebalanceTriggered",
    "SchemaVersion",
    "ShadowCapitalSeeded",
    "SignalEmitted",
    "StrategyPaused",
    "StrategyResumed",
    "TradeExecuted",
    "TradeRejected",
    "TradeSide",
    "TradeSummary",
]
