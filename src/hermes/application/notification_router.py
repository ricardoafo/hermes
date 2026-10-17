"""Notification router — decides which notifier adapter(s) fire per event class.

Pure function on the operational ``EventClass`` enum defined in this module.
The routing table is the one codified in the 2026-08-23 decisions-journal
entry (``docs/decisions/2026-08-23-slack-alongside-telegram.md`` §
"Rationale") — Telegram for urgent/terse (push-to-phone alerts), Slack for
rich/long-form (formatted reports, threaded postmortems, CSV attachments),
both when a signal needs immediate visibility AND a durable record.

Design invariants (property-tested — see ``tests/unit/application/``):

- **Purity**: ``route`` takes an ``EventClass`` + a ``RouterConfig`` and
  returns a tuple of adapters. No I/O, no state, no exceptions. Calling
  the same input twice returns equal tuples.
- **Adapter injection**: the router never imports ``TelegramAdapter`` /
  ``SlackAdapter`` (those live in ``hermes.adapters.notifier`` and the
  application layer must not import adapters — code-conventions §1).
  ``RouterConfig`` carries whichever adapters composition wires in.
- **Missing-adapter tolerance**: if the routing table asks for Telegram
  but ``config.telegram is None`` (e.g. Slack-only test rig, or an early
  boot before the Telegram credential is loaded), that slot is silently
  dropped from the returned tuple. The router never fabricates adapters.
- **Fan-out ordering**: when both channels fire for a class, Telegram
  appears first in the returned tuple. Deterministic order matters for
  audit trails and for the caller's failure-isolation semantics.
- **Dispatch is the caller's job**: the router returns adapters; it does
  NOT invoke them. Callers (typically an ``EventBus`` subscriber wired
  in ``entrypoints/``) iterate the tuple and choose the notifier method
  (``notify_urgent`` or ``notify_report``) that matches the event shape.
  Failure isolation across adapters MUST mirror ``EventBus``'s
  ``EventDispatchError`` pattern — one raising adapter cannot silence
  the rest.

Reference: ``docs/decisions/2026-08-23-slack-alongside-telegram.md``
    (Rationale table drives the routing map);
    ``src/hermes/ports/notifier.py`` (adapter contract);
    ``docs/guides/code-conventions.md`` §1 (hexagonal — application
    imports ports + domain, never adapters).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from hermes.domain.events.anomaly_detected import AnomalyDetected, AnomalySeverity
from hermes.domain.events.balance_low import BalanceLow
from hermes.domain.events.base import DomainEvent
from hermes.domain.events.daily_trade_digest import DailyTradeDigest
from hermes.domain.events.rebalance_triggered import RebalanceTriggered
from hermes.domain.events.risk_halt_recorded import RiskHaltRecorded
from hermes.domain.events.signal_emitted import SignalEmitted
from hermes.domain.events.strategy_paused import StrategyPaused
from hermes.domain.events.strategy_resumed import StrategyResumed
from hermes.domain.events.trade_executed import TradeExecuted
from hermes.domain.events.trade_rejected import TradeRejected
from hermes.ports.notifier import NotifierPort

__all__ = [
    "EventClass",
    "RouterConfig",
    "channels_for",
    "event_class_for",
    "is_event_loud",
    "is_loud",
    "route",
]


class EventClass(StrEnum):
    """Operational event categories that drive notification routing.

    These are NOT the concrete ``DomainEvent`` subclasses under
    ``hermes.domain.events``. They are the operational categories the
    2026-08-23 decisions-journal entry uses in its Rationale table.
    Callers translate concrete events into an ``EventClass`` before
    invoking ``route`` — that mapping is a composition concern (a bus
    subscriber wired in ``entrypoints/``), not the router's concern.

    Members mirror the table one-to-one so the router stays a literal
    encoding of the decision doc:

    - ``SERVICE_CRASH`` — process died / systemd restart loop.
    - ``HEALTH_CHECK_FAIL`` — dead-man's-switch tripped.
    - ``ROLLBACK_FIRED`` — automated rollback ran (short ping + full postmortem).
    - ``CVE_DETECTED`` — Dependabot / semgrep / pip-audit finding
      (short ping + full analysis thread).
    - ``DEPLOY_STATUS`` — deploy done / failed (one-line summary).
    - ``WEEKLY_REBALANCE_REPORT`` — recurring report (rich formatting).
    - ``MONTHLY_TAX_REPORT`` — recurring report (CSV attachment).
    - ``TRADE_FIRED_SINGLE`` — individual trade fill (log-only; no notify).

    Reference: ``docs/decisions/2026-08-23-slack-alongside-telegram.md``
        § Rationale table.
    """

    SERVICE_CRASH = "SERVICE_CRASH"
    """Process crash — Telegram only (urgent, actionable, phone-push)."""

    HEALTH_CHECK_FAIL = "HEALTH_CHECK_FAIL"
    """Health-check failure — Telegram only (urgent, phone-push)."""

    ROLLBACK_FIRED = "ROLLBACK_FIRED"
    """Automated rollback — Telegram (short ping) + Slack (postmortem thread)."""

    CVE_DETECTED = "CVE_DETECTED"
    """CVE detected — Telegram (short ping) + Slack (analysis + timeline)."""

    DEPLOY_STATUS = "DEPLOY_STATUS"
    """Deploy done / failed — Telegram only (one-line summary)."""

    WEEKLY_REBALANCE_REPORT = "WEEKLY_REBALANCE_REPORT"
    """Weekly rebalance report — Slack only (formatted table)."""

    MONTHLY_TAX_REPORT = "MONTHLY_TAX_REPORT"
    """Monthly tax report — Slack only (CSV attachment)."""

    TRADE_FIRED_SINGLE = "TRADE_FIRED_SINGLE"
    """Individual trade fill — log-only, no channel fires."""

    # ------------------------------------------------------------------
    # MVP1 additions — extend the routing table per the 2026-08-23
    # decisions-journal entry ("Extended MVP1 events" section). Each row
    # below mirrors a routing choice documented in that entry.
    # ------------------------------------------------------------------

    STRATEGY_PAUSED = "STRATEGY_PAUSED"
    """Strategy pause — Telegram (urgent) + Slack (durable record)."""

    STRATEGY_RESUMED = "STRATEGY_RESUMED"
    """Strategy resume — Telegram (silent) + Slack: the recovery mirrors the pause."""

    BALANCE_LOW = "BALANCE_LOW"
    """Wallet balance below threshold — Telegram only (urgent action)."""

    TRADE_REJECTED_ESCALATED = "TRADE_REJECTED_ESCALATED"
    """Repeated trade rejection — Telegram only (urgent).

    Single rejections are logged only (no ``EventClass`` for them). This
    class fires when the retry / dedup logic escalates a pattern of
    rejections into a single actionable ping.
    """

    DAILY_TRADE_DIGEST = "DAILY_TRADE_DIGEST"
    """End-of-day trading summary — Slack only (formatted + CSV)."""

    POSITION_LARGE = "POSITION_LARGE"
    """Large-position notice — Telegram (visibility) + Slack (durable record)."""

    ANOMALY_DETECTED = "ANOMALY_DETECTED"
    """Circuit-breaker / invariant violation — Telegram + Slack (both channels)."""

    SIGNAL_EMITTED = "SIGNAL_EMITTED"
    """Shadow strategy signal — Telegram (push) + Slack (durable record).

    Reference: ``docs/decisions/2026-10-03-signal-notification-routing.md``.
    """

    REBALANCE_TRIGGERED = "REBALANCE_TRIGGERED"
    """Rebalance heads-up — Slack (compact) + Telegram (one-line pointer), silent."""

    RISK_HALT = "RISK_HALT"
    """A risk halt or a §10 kill refused an entry — Telegram + Slack, loud (#749)."""


@dataclass(frozen=True, slots=True)
class RouterConfig:
    """Adapter injection container for the notification router.

    Holds optional handles to the concrete notifier adapters wired at
    composition time. Both fields are ``NotifierPort | None``:

    - Marking them optional lets composition build a partial rig — e.g.
      a Slack-only integration test, or an early-boot phase where the
      Telegram credential has not loaded yet.
    - When ``route`` is asked for a channel that is ``None``, that slot
      is dropped from the returned tuple (silent tolerance). The router
      NEVER raises for a missing adapter — the caller decides whether
      "no adapters returned" is a real problem for a given event class.

    The router does not import concrete adapter classes; both fields are
    typed against the ``NotifierPort`` Protocol. This keeps the
    application layer free of adapter imports per hexagonal invariant
    (code-conventions §1).

    Fields:
        telegram: The Telegram adapter, or ``None`` if not wired.
        slack: The Slack adapter, or ``None`` if not wired.

    Reference: code-conventions.md §1 (Ports & Adapters — application
        depends on ports, not adapters).
    """

    telegram: NotifierPort | None = None
    slack: NotifierPort | None = None


# ---------------------------------------------------------------------------
# Routing table — literal encoding of the 2026-08-23 decisions-journal entry
# Rationale table. Each entry names which channels an EventClass fires on.
# Row order in the enum matches the doc; keys mirror members 1:1.
# ---------------------------------------------------------------------------

_ROUTING_TABLE: dict[EventClass, tuple[bool, bool]] = {
    # Each value is (telegram_fires, slack_fires) — flags mirror the ADR table.
    EventClass.SERVICE_CRASH: (True, False),
    EventClass.HEALTH_CHECK_FAIL: (True, False),
    EventClass.ROLLBACK_FIRED: (True, True),
    EventClass.CVE_DETECTED: (True, True),
    EventClass.DEPLOY_STATUS: (True, False),
    EventClass.WEEKLY_REBALANCE_REPORT: (False, True),
    EventClass.MONTHLY_TAX_REPORT: (False, True),
    EventClass.TRADE_FIRED_SINGLE: (False, False),
    # MVP1 additions — see 2026-08-23-slack-alongside-telegram.md §
    # "Extended MVP1 events" for the rationale of each row.
    EventClass.STRATEGY_PAUSED: (True, True),
    EventClass.STRATEGY_RESUMED: (True, True),
    EventClass.BALANCE_LOW: (True, False),
    EventClass.TRADE_REJECTED_ESCALATED: (True, False),
    EventClass.DAILY_TRADE_DIGEST: (False, True),
    EventClass.POSITION_LARGE: (True, True),
    EventClass.ANOMALY_DETECTED: (True, True),
    # See 2026-10-03-signal-notification-routing.md.
    EventClass.SIGNAL_EMITTED: (True, True),
    # Notification templates v2 (#144): the heads-up reaches both channels.
    EventClass.REBALANCE_TRIGGERED: (True, True),
    # #749: a halt or a kill needs the operator, and a durable record.
    EventClass.RISK_HALT: (True, True),
}
"""Per-class channel fan-out — encodes the ADR Rationale table verbatim."""

_ALWAYS_LOUD: frozenset[EventClass] = frozenset(
    {
        EventClass.SERVICE_CRASH,
        EventClass.HEALTH_CHECK_FAIL,
        EventClass.ROLLBACK_FIRED,
        EventClass.BALANCE_LOW,
        EventClass.TRADE_REJECTED_ESCALATED,
        EventClass.RISK_HALT,
    }
)
"""Classes that ring the phone: down, P0 and rejected orders (ADR-015 D-06, D-07)."""

_LOUD_WHEN_CRITICAL: frozenset[EventClass] = frozenset(
    {EventClass.STRATEGY_PAUSED, EventClass.ANOMALY_DETECTED}
)
"""Classes that ring only at ``CRITICAL`` severity (P0); lower severities are silent."""

_EVENT_CLASSES: dict[type[DomainEvent], EventClass] = {
    SignalEmitted: EventClass.SIGNAL_EMITTED,
    StrategyPaused: EventClass.STRATEGY_PAUSED,
    StrategyResumed: EventClass.STRATEGY_RESUMED,
    AnomalyDetected: EventClass.ANOMALY_DETECTED,
    BalanceLow: EventClass.BALANCE_LOW,
    TradeRejected: EventClass.TRADE_REJECTED_ESCALATED,
    TradeExecuted: EventClass.POSITION_LARGE,
    RebalanceTriggered: EventClass.REBALANCE_TRIGGERED,
    DailyTradeDigest: EventClass.DAILY_TRADE_DIGEST,
    RiskHaltRecorded: EventClass.RISK_HALT,
}
"""Concrete event type to routing class. ``LotClosed`` and ``BarProcessed`` have none."""


def route(event_class: EventClass, config: RouterConfig) -> tuple[NotifierPort, ...]:
    """Return the adapters that should fire for ``event_class``.

    Pure function — no I/O, no state, no exceptions. Two calls with equal
    inputs return equal tuples. The caller iterates the returned tuple
    and invokes the appropriate notifier method (``notify_urgent`` for
    Telegram-style short pings; ``notify_report`` for Slack-style rich
    reports); adapter selection of which method to call is not the
    router's job.

    Behavior:

    - Looks up ``event_class`` in the routing table (the literal encoding
      of the 2026-08-23 decisions-journal Rationale table).
    - For each channel flagged ``True`` in the table AND wired in
      ``config``, appends the adapter to the returned tuple.
    - A channel flagged ``True`` but wired as ``None`` in ``config`` is
      dropped silently — the router never fabricates adapters. Callers
      that require a specific adapter must validate composition
      themselves.
    - Fan-out order is deterministic: Telegram always precedes Slack in
      the returned tuple when both fire.

    Args:
        event_class: The operational category driving the routing decision.
            Callers translate concrete ``DomainEvent`` instances into an
            ``EventClass`` before invoking this function.
        config: The composition-wired adapter handles. Either or both
            fields may be ``None`` for a partial rig.

    Returns:
        A tuple of ``NotifierPort`` adapters, in dispatch order
        (Telegram first when present). Empty tuple when the routing
        table says no channel fires (e.g. ``TRADE_FIRED_SINGLE``) OR
        when every channel the table asks for is unwired.
    """
    telegram_fires, slack_fires = _ROUTING_TABLE[event_class]
    adapters: list[NotifierPort] = []
    if telegram_fires and config.telegram is not None:
        adapters.append(config.telegram)
    if slack_fires and config.slack is not None:
        adapters.append(config.slack)
    return tuple(adapters)


def channels_for(event_class: EventClass) -> tuple[bool, bool]:
    """Return ``(telegram_fires, slack_fires)`` for ``event_class``.

    Introspection helper used by tooling that needs to know WHICH
    channels an event class targets without wiring adapters (e.g. the
    ``hermes preview`` CLI decides whether to render the Telegram
    template based on the routing table, not by trial-and-error).

    The tuple mirrors the internal routing table row-for-row; a router
    change automatically propagates.

    Args:
        event_class: The operational category to look up.

    Returns:
        A ``(telegram, slack)`` pair of bools.
    """
    return _ROUTING_TABLE[event_class]


def event_class_for(event: DomainEvent) -> EventClass | None:
    """Return the routing class of a concrete event.

    Args:
        event: A concrete domain event.

    Returns:
        Its :class:`EventClass`, or ``None`` for an event that is never
        notified (``LotClosed``, ``BarProcessed``).
    """
    return _EVENT_CLASSES.get(type(event))


def is_loud(event_class: EventClass, severity: AnomalySeverity | None = None) -> bool:
    """Return whether a push for ``event_class`` should ring the phone.

    Loud means down, P0 and rejected orders; everything else is sent silent
    (Telegram ``disable_notification``).

    Args:
        event_class: The routing class.
        severity: The event's severity, for the classes that carry one. A
            pause or an anomaly rings only when it is ``CRITICAL``.

    Returns:
        ``True`` to ring, ``False`` to send silently.
    """
    if event_class in _ALWAYS_LOUD:
        return True
    return event_class in _LOUD_WHEN_CRITICAL and severity is AnomalySeverity.CRITICAL


def is_event_loud(event: DomainEvent) -> bool:
    """Return whether the push for ``event`` should ring the phone.

    Args:
        event: A concrete domain event.

    Returns:
        ``False`` for an event with no routing class, else :func:`is_loud`
        on its class and, where it has one, its severity.
    """
    event_class = event_class_for(event)
    if event_class is None:
        return False
    severity = event.severity if isinstance(event, StrategyPaused | AnomalyDetected) else None
    return is_loud(event_class, severity)
