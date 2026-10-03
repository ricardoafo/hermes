"""Tests for ``SignalNotifier`` — fan-out of one signal to the routed channels.

Invariants protected here:

- one failing channel never stops the other from being notified;
- every channel failure arrives in ONE ``EventDispatchError``;
- a non-infra exception is a bug and propagates without being collected;
- zero wired channels is not an error;
- Telegram is notified before Slack (router order).

Reference: 309-serve-bar-loop change.md D-3, D-5, R-2.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from hermes.application.notification_router import RouterConfig
from hermes.application.signal_notifier import SignalNotifier
from hermes.domain.errors import ExchangeError, InfraError, NetworkError
from hermes.domain.events.base import DomainEvent
from hermes.domain.events.event_bus import EventDispatchError
from hermes.domain.events.signal_emitted import SignalEmitted
from hermes.domain.strategy.signal import SignalKind, SignalSide, StrategySignal
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.strategy_id import StrategyId


@dataclass
class _Channel:
    """NotifierPort fake that records events and optionally raises."""

    label: str
    log: list[str]
    raises: Exception | None = None
    seen: list[DomainEvent] = field(default_factory=list)

    def notify(self, event: DomainEvent) -> None:
        """Record the call, then raise when configured to."""
        self.log.append(self.label)
        if self.raises is not None:
            raise self.raises
        self.seen.append(event)

    def notify_urgent(self, *, event: str, correlation_id: str, message: str) -> None:
        """Unused by the notifier."""

    def notify_report(
        self,
        *,
        event: str,
        correlation_id: str,
        title: str,
        body: str,
        attachments: list[Path] | None = None,
    ) -> None:
        """Unused by the notifier."""


def _event() -> SignalEmitted:
    """Return a long entry event."""
    return SignalEmitted.from_signal(
        StrategySignal(
            kind=SignalKind.ENTRY,
            side=SignalSide.LONG,
            entry_price=Decimal("50000"),
            stop_price=Decimal("48000"),
            atr_context=Decimal("1200"),
            signal_time=datetime(2026, 10, 2, 16, tzinfo=UTC),
        ),
        strategy_id=StrategyId("swing-donchian-4h"),
        pair=Pair(base=Currency.BTC, quote=Currency.USDT),
        correlation_id="01J9Z8T3M5BQR2FVKN4XWY6PH1",
    )


def test_both_channels_receive_the_event_telegram_first() -> None:
    """A signal reaches Telegram then Slack, each exactly once."""
    log: list[str] = []
    telegram, slack = _Channel("telegram", log), _Channel("slack", log)
    event = _event()

    SignalNotifier(RouterConfig(telegram=telegram, slack=slack)).dispatch(event)

    assert (log, telegram.seen, slack.seen) == (["telegram", "slack"], [event], [event])


def test_a_failing_channel_does_not_stop_the_other() -> None:
    """Telegram failing still lets Slack be notified."""
    log: list[str] = []
    telegram = _Channel("telegram", log, raises=NetworkError("telegram down"))
    slack = _Channel("slack", log)

    with pytest.raises(EventDispatchError):
        SignalNotifier(RouterConfig(telegram=telegram, slack=slack)).dispatch(_event())

    assert (log, len(slack.seen)) == (["telegram", "slack"], 1)


def test_every_failure_arrives_in_one_dispatch_error() -> None:
    """Two failing channels give one error carrying both failures, in order."""
    log: list[str] = []
    first, second = NetworkError("a"), ExchangeError("b")
    channels = RouterConfig(
        telegram=_Channel("telegram", log, raises=first),
        slack=_Channel("slack", log, raises=second),
    )

    with pytest.raises(EventDispatchError) as caught:
        SignalNotifier(channels).dispatch(_event())

    assert (caught.value.event_type_name, caught.value.failures) == (
        "SignalEmitted",
        (first, second),
    )


def test_a_non_infra_exception_propagates_and_stops_the_fan_out() -> None:
    """A missing template (ValueError) is a bug: it propagates, uncollected."""
    log: list[str] = []
    channels = RouterConfig(
        telegram=_Channel("telegram", log, raises=ValueError("no template")),
        slack=_Channel("slack", log),
    )

    with pytest.raises(ValueError, match="no template"):
        SignalNotifier(channels).dispatch(_event())

    assert log == ["telegram"]


def test_zero_wired_channels_is_not_an_error() -> None:
    """With no channel wired, dispatch returns silently."""
    SignalNotifier(RouterConfig()).dispatch(_event())


def test_infra_error_subclasses_are_isolated() -> None:
    """Any InfraError subclass is treated as a channel outage."""
    assert issubclass(NetworkError, InfraError)
    log: list[str] = []
    slack = _Channel("slack", log)
    channels = RouterConfig(
        telegram=_Channel("telegram", log, raises=NetworkError("x")), slack=slack
    )

    with pytest.raises(EventDispatchError):
        SignalNotifier(channels).dispatch(_event())

    assert len(slack.seen) == 1
