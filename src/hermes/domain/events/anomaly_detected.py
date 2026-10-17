"""AnomalyDetected domain event — emitted when a circuit-breaker or invariant fires.

Anomalies are the "fail loud, not silent" signal: something is outside the
expected envelope (drawdown breach, reconciliation mismatch, unexpected
exchange response). Emission ALWAYS precedes any automated pause action so
subscribers can capture the reason before state changes.

Reference: ADR-003 §02 (circuit breakers), code-conventions.md §10 and §12
(engine pause pairs with anomaly emission).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar, Final

from hermes.domain.events.base import DomainEvent

__all__ = ["CLOCK_SKEW_SOURCE", "AnomalyDetected", "AnomalySeverity"]

CLOCK_SKEW_SOURCE: Final = "serve.clock_skew"
"""Source of the anomaly serve records when its clock leaves, or comes back to,
exchange time (#859); ``hermes status`` reads the latest one."""


class AnomalySeverity(StrEnum):
    """Severity classification of an anomaly.

    Mirrors the SEV levels of the AMG severity rubric so notifier routing
    (Telegram for critical, Slack for degraded) can dispatch on this field.

    Reference: ADR-003 §02 circuit-breaker semantics.
    """

    CRITICAL = "CRITICAL"
    """Immediate action required. Recorded and notified; nothing pauses on it yet (#136)."""

    WARNING = "WARNING"
    """Degraded state — human review requested, no auto-action."""

    INFO = "INFO"
    """Notable but expected (e.g. weight-limit backoff)."""


@dataclass(frozen=True, slots=True)
class AnomalyDetected(DomainEvent):
    """A circuit-breaker or invariant violation surfaced by the engine.

    The ``source`` field identifies the subsystem that detected the anomaly
    (e.g. ``"swing.risk"``, ``"reconciler"``). Downstream subscribers filter
    on ``source`` + ``severity`` — the router is not part of the event.

    Fields:
        source: Dot-notated identifier of the emitting subsystem.
        severity: How urgent the anomaly is; drives notifier routing.
        reason: Human-readable one-line explanation. Never sensitive data.

    Reference: ADR-003 §02 — drawdown breach, position-limit hit,
        reconciliation mismatch.
    """

    __event_schema_version__: ClassVar[int] = 1

    source: str
    severity: AnomalySeverity
    reason: str
