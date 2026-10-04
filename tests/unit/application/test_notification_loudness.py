"""Tests for the loud-versus-silent policy and the event-to-route mapping.

Invariants protected here:

- a push rings the phone only for down, P0 and rejected orders; every other
  class is silent (ADR-015 D-06, D-07; templates v2 rule 4);
- a pause or an anomaly rings only at CRITICAL severity;
- every concrete notifiable event maps to a routing class, and events that are
  never notified map to none and stay silent;
- the rebalance heads-up and the resume are routed to Telegram and Slack.

Reference: openspec/changes/144-notification-system/change.md (R-3).
"""

from __future__ import annotations

import pytest

from hermes.application.notification_router import (
    EventClass,
    channels_for,
    event_class_for,
    is_event_loud,
    is_loud,
)
from hermes.domain.events.anomaly_detected import AnomalySeverity
from hermes.domain.events.bar_processed import BarProcessed
from hermes.domain.market_data.kline import KlineInterval
from tests.support.notification_samples import (
    BTC_USDT,
    CORRELATION_ID,
    NOON_SATURDAY_UTC,
    samples,
)

_LOUD = {
    EventClass.SERVICE_CRASH,
    EventClass.HEALTH_CHECK_FAIL,
    EventClass.ROLLBACK_FIRED,
    EventClass.BALANCE_LOW,
    EventClass.TRADE_REJECTED_ESCALATED,
}
_SEVERITY_GATED = {EventClass.STRATEGY_PAUSED, EventClass.ANOMALY_DETECTED}


@pytest.mark.parametrize("event_class", sorted(_LOUD))
def test_down_p0_and_rejected_classes_always_ring(event_class: EventClass) -> None:
    """The classes for down, P0 and rejected orders ring whatever the severity."""
    assert is_loud(event_class) is True


@pytest.mark.parametrize(
    "event_class", sorted(set(EventClass) - _LOUD - _SEVERITY_GATED, key=lambda c: c.value)
)
def test_every_other_class_is_silent(event_class: EventClass) -> None:
    """Signals, fills, reports, resumes and the rest never ring the phone."""
    assert is_loud(event_class, AnomalySeverity.CRITICAL) is False


@pytest.mark.parametrize("event_class", sorted(_SEVERITY_GATED))
@pytest.mark.parametrize(
    ("severity", "loud"),
    [
        (AnomalySeverity.CRITICAL, True),
        (AnomalySeverity.WARNING, False),
        (AnomalySeverity.INFO, False),
        (None, False),
    ],
)
def test_pause_and_anomaly_ring_only_when_critical(
    event_class: EventClass, severity: AnomalySeverity | None, loud: bool
) -> None:
    """A pause or anomaly rings at CRITICAL severity and is silent below it."""
    assert is_loud(event_class, severity) is loud


@pytest.mark.parametrize(
    ("name", "event_class", "loud"),
    [
        ("signal-entry", EventClass.SIGNAL_EMITTED, False),
        ("signal-exit", EventClass.SIGNAL_EMITTED, False),
        ("position-large", EventClass.POSITION_LARGE, False),
        ("trade-rejected", EventClass.TRADE_REJECTED_ESCALATED, True),
        ("strategy-paused-critical", EventClass.STRATEGY_PAUSED, True),
        ("strategy-paused-warning", EventClass.STRATEGY_PAUSED, False),
        ("strategy-resumed", EventClass.STRATEGY_RESUMED, False),
        ("anomaly-critical", EventClass.ANOMALY_DETECTED, True),
        ("anomaly-info", EventClass.ANOMALY_DETECTED, False),
        ("balance-low", EventClass.BALANCE_LOW, True),
        ("rebalance-triggered", EventClass.REBALANCE_TRIGGERED, False),
        ("daily-digest", EventClass.DAILY_TRADE_DIGEST, False),
    ],
)
def test_each_sample_event_maps_to_its_class_and_loudness(
    name: str, event_class: EventClass, loud: bool
) -> None:
    """Every notifiable sample event has its routing class and the expected loudness."""
    event = samples()[name]

    assert (event_class_for(event), is_event_loud(event)) == (event_class, loud)


def test_every_sample_event_is_routed_to_at_least_one_channel() -> None:
    """No notifiable event maps to a class that fires on no channel."""
    unrouted = sorted(
        name
        for name, event in samples().items()
        if (cls := event_class_for(event)) is None or channels_for(cls) == (False, False)
    )

    assert unrouted == []


def test_event_without_a_class_is_silent() -> None:
    """An event that is never notified maps to no class and would not ring."""
    event = BarProcessed(
        occurred_at=NOON_SATURDAY_UTC,
        correlation_id=CORRELATION_ID,
        pair=BTC_USDT,
        interval=KlineInterval.H4,
    )

    assert (event_class_for(event), is_event_loud(event)) == (None, False)
