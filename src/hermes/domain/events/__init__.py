"""Domain events package.

Re-exports the base class and schema-version type alias.
Concrete events (TradeExecuted, RebalanceTriggered, …) are added in #99.

Reference: code-conventions.md §10 — Event schema versioning + upcaster chain.
"""

from hermes.domain.events.base import DomainEvent, SchemaVersion

__all__ = ["DomainEvent", "SchemaVersion"]
