"""StrategyResumed domain event — emitted when a paused strategy restarts.

Pairs with :class:`StrategyPaused`. Carries the pause duration so the
notification message can convey the operational impact without forcing
subscribers to reconstruct it from the ledger.

Reference: ADR-003 §02 (pause / resume lifecycle),
    code-conventions.md §10 (event schema versioning) and §3 (frozen).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar

from hermes.domain.events.base import DomainEvent

__all__ = ["StrategyResumed"]


@dataclass(frozen=True, slots=True)
class StrategyResumed(DomainEvent):
    """A previously paused strategy is back online.

    Emitted after the resume state change succeeds. Subscribers may
    assume the strategy is generating signals again by the time the
    event fans out.

    Fields:
        strategy_id: Stable identifier of the resumed strategy (matches
            ``StrategyConfig.name`` from the loaded configuration).
        resumed_at: UTC-aware datetime when the resume completed.
        pause_duration_seconds: Total seconds the strategy stayed paused,
            measured from the paired :class:`StrategyPaused` event's
            ``paused_at`` up to :attr:`resumed_at`. Non-negative integer.

    Reference: ADR-003 §02 — pause / resume symmetry.
    """

    __event_schema_version__: ClassVar[int] = 1

    strategy_id: str
    resumed_at: datetime
    pause_duration_seconds: int
