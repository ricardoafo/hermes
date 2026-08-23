"""StrategyPaused domain event — emitted when a strategy is halted.

A strategy pause is a deliberate suspension of signal generation for a
specific strategy. The pause may originate from an automated circuit
breaker (drawdown breach, recharacterization guard) or from an operator
kill switch. Emission always precedes the state change so subscribers
capture the reason before the pause takes effect.

Reference: ADR-003 §02 (circuit breakers pair with anomaly emission),
    code-conventions.md §10 (event schema versioning) and §3 (frozen).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar

from hermes.domain.events.anomaly_detected import AnomalySeverity
from hermes.domain.events.base import DomainEvent

__all__ = ["StrategyPaused"]


@dataclass(frozen=True, slots=True)
class StrategyPaused(DomainEvent):
    """A trading strategy has been halted from generating new signals.

    Emitted before the pause takes effect — subscribers must not assume
    the strategy has already stopped when they receive the event; the
    engine performs the state change immediately after publish returns.

    Fields:
        strategy_id: Stable identifier of the paused strategy (matches
            ``StrategyConfig.name`` from the loaded configuration).
        reason: Human-readable one-line explanation. Never sensitive data.
        paused_at: UTC-aware datetime when the pause was requested.
        severity: Urgency level; drives notifier routing (critical pauses
            fan out to Telegram + Slack, warnings only to Telegram).

    Reference: ADR-003 §02 — pause pairs with anomaly emission.
    """

    __event_schema_version__: ClassVar[int] = 1

    strategy_id: str
    reason: str
    paused_at: datetime
    severity: AnomalySeverity
