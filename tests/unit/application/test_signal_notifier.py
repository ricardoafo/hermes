"""Tests for ``SignalNotifier`` — fan-out of one signal to the routed channels.

Invariants protected here:

- one failing channel never stops the other from being notified;
- every channel failure arrives in ONE ``EventDispatchError``;
- a non-infra exception (a bug) is isolated like an outage, so it cannot stop
  serve after the fill (#854);
- ``deliver`` names the channels that took the push and the ones that raised,
  and with ``only`` pushes to those channels alone (a retry);
- zero wired channels is not an error;
- Telegram is notified before Slack (router order).

Reference: 309-serve-bar-loop change.md D-3, D-5, R-2; issue #854.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

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


def test_a_non_infra_exception_is_isolated_and_the_fan_out_goes_on() -> None:
    """A missing template (ValueError) is collected like an outage; Slack still gets the push."""
    log: list[str] = []
    bug = ValueError("no template")
    slack = _Channel("slack", log)
    channels = RouterConfig(telegram=_Channel("telegram", log, raises=bug), slack=slack)

    with pytest.raises(EventDispatchError) as caught:
        SignalNotifier(channels).dispatch(_event())

    assert (log, len(slack.seen), caught.value.failures) == (["telegram", "slack"], 1, (bug,))


def test_deliver_names_the_channels_that_took_the_push_and_the_ones_that_raised() -> None:
    """A failing Telegram and a working Slack give delivered=slack, failed=telegram."""
    log: list[str] = []
    bug = ValueError("no template")
    channels = RouterConfig(
        telegram=_Channel("telegram", log, raises=bug), slack=_Channel("slack", log)
    )

    delivery = SignalNotifier(channels).deliver(_event())

    assert (delivery.delivered, delivery.failed, delivery.failures) == (
        ("slack",),
        ("telegram",),
        (("telegram", bug),),
    )


def test_deliver_with_only_pushes_to_those_channels_alone() -> None:
    """A retry of the Telegram push does not push Slack a second time."""
    log: list[str] = []
    telegram, slack = _Channel("telegram", log), _Channel("slack", log)

    delivery = SignalNotifier(RouterConfig(telegram=telegram, slack=slack)).deliver(
        _event(), only=("telegram",)
    )

    assert (log, delivery.delivered, delivery.failed) == (["telegram"], ("telegram",), ())


@given(
    telegram=st.sampled_from(["absent", "ok", "raises"]),
    slack=st.sampled_from(["absent", "ok", "raises"]),
    only=st.none() | st.sets(st.sampled_from(["telegram", "slack"])),
)
def test_deliver_reports_every_attempted_channel_once(
    telegram: str, slack: str, only: set[str] | None
) -> None:
    """Delivered and failed split the wired channels ``only`` allows, with no channel in both."""
    log: list[str] = []

    def wire(name: str, state: str) -> _Channel | None:
        if state == "absent":
            return None
        return _Channel(name, log, raises=NetworkError("x") if state == "raises" else None)

    delivery = SignalNotifier(
        RouterConfig(telegram=wire("telegram", telegram), slack=wire("slack", slack))
    ).deliver(_event(), only=only)

    wired = [n for n, state in (("telegram", telegram), ("slack", slack)) if state != "absent"]
    attempted = [n for n in wired if only is None or n in only]
    assert (sorted(delivery.delivered + delivery.failed), log) == (sorted(attempted), attempted)
    assert not set(delivery.delivered) & set(delivery.failed)


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
