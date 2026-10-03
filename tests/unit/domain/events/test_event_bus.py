"""Tests for the in-process EventBus — property + example based.

Property invariants exercised (Hypothesis):

- **Fan-out cardinality**: publishing to N subscribers dispatches N times.
- **Dispatch order matches registration order**: for the same event type,
  handlers fire in the order they were subscribed.
- **Fan-out is per exact type**: a subscriber to type A never receives
  events of type B, even when A and B share ``DomainEvent`` as a base.
- **Cross-type isolation**: publishing an event whose type has no
  subscribers is a no-op and does not touch subscribers of other types.

Example-based tests cover:

- Failure isolation — a raising handler does not skip later handlers,
  and every exception is aggregated into ``EventDispatchError``.
- Introspection via ``subscriber_count``.
- Publishing before any subscription is a no-op.

Note on invariant I-06 scope: this PR ships the in-process bus in
isolation from the SQLite adapter. The ledger-transaction invariant
"every event → row+event in the same transaction" is deferred to #24
(SQLite adapter). This suite proves the bus invariant "every publish
to N subscribers dispatches N times, sync, in registration order" —
the foundation the ledger integration will build on.

Reference: ADR-003 §00 (I-06), code-conventions.md §10.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import ClassVar

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hermes.domain.events.anomaly_detected import AnomalyDetected, AnomalySeverity
from hermes.domain.events.base import DomainEvent
from hermes.domain.events.event_bus import EventBus, EventDispatchError
from hermes.domain.events.trade_executed import TradeExecuted, TradeSide
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.price import Price
from hermes.domain.value_objects.quantity import Quantity
from hermes.domain.value_objects.strategy_id import StrategyId

# ---------------------------------------------------------------------------
# Fixture helpers — small pure builders shared across tests
# ---------------------------------------------------------------------------


def _trade() -> TradeExecuted:
    """Return a deterministic TradeExecuted event for use across bus tests."""
    pair = Pair(base=Currency.BTC, quote=Currency.USDT)
    return TradeExecuted(
        occurred_at=datetime(2026, 8, 23, 14, 22, 3, tzinfo=UTC),
        correlation_id="01J9Z8T3M5BQR2FVKN4XWY6PH1",
        pair=pair,
        side=TradeSide.BUY,
        qty=Quantity(amount=Decimal("0.001"), asset=Currency.BTC),
        price=Price(amount=Decimal("68000"), pair=pair),
        fee=Money(amount=Decimal("0.06"), currency=Currency.USDT),
        strategy_id=StrategyId("swing"),
    )


def _anomaly() -> AnomalyDetected:
    """Return a deterministic AnomalyDetected event for use across bus tests."""
    return AnomalyDetected(
        occurred_at=datetime(2026, 8, 23, 14, 22, 3, tzinfo=UTC),
        correlation_id="01J9Z8T3M5BQR2FVKN4XWY6PH1",
        source="swing.risk",
        severity=AnomalySeverity.CRITICAL,
        reason="30% drawdown breached",
    )


@dataclass(frozen=True, slots=True)
class _OtherEvent(DomainEvent):
    """Standalone event class used to prove cross-type isolation."""

    __event_schema_version__: ClassVar[int] = 1
    payload: str


# ---------------------------------------------------------------------------
# Property tests
# ---------------------------------------------------------------------------


class TestBusFanOutCardinality:
    """Publishing dispatches to every subscriber — count must match."""

    @given(subscriber_count=st.integers(min_value=0, max_value=25))
    def test_publish_dispatches_once_per_subscriber(self, subscriber_count: int) -> None:
        """Publishing a single event to N subscribers invokes each handler exactly once."""
        bus = EventBus()
        calls: list[int] = []
        # Bind index at subscription time so we can prove per-handler dispatch.
        for i in range(subscriber_count):
            bus.subscribe(TradeExecuted, lambda _e, idx=i: calls.append(idx))
        bus.publish(_trade())
        assert calls == list(range(subscriber_count))


class TestBusRegistrationOrder:
    """Handlers fire in the exact order they were registered."""

    @given(order=st.lists(st.integers(min_value=0, max_value=999), max_size=15, unique=True))
    def test_dispatch_order_equals_registration_order(self, order: list[int]) -> None:
        """The dispatched sequence of handler tags matches the sequence at subscribe time."""
        bus = EventBus()
        calls: list[int] = []
        for tag in order:
            bus.subscribe(TradeExecuted, lambda _e, t=tag: calls.append(t))
        bus.publish(_trade())
        # Exact list equality — same tags in the same order the subscribe() calls happened.
        assert len(calls) == len(order)
        assert calls == list(order)


class TestBusPerTypeIsolation:
    """Subscribers see only events of their exact registered type."""

    @given(
        trade_subscribers=st.integers(min_value=0, max_value=10),
        anomaly_subscribers=st.integers(min_value=0, max_value=10),
    )
    def test_type_specific_subscribers_receive_only_their_events(
        self, trade_subscribers: int, anomaly_subscribers: int
    ) -> None:
        """A publish routes to exact-type subscribers only; other types stay untouched."""
        bus = EventBus()
        trade_calls: list[int] = []
        anomaly_calls: list[int] = []
        for i in range(trade_subscribers):
            bus.subscribe(TradeExecuted, lambda _e, idx=i: trade_calls.append(idx))
        for i in range(anomaly_subscribers):
            bus.subscribe(AnomalyDetected, lambda _e, idx=i: anomaly_calls.append(idx))
        bus.publish(_trade())
        # Only trade subscribers ran, and every one of them exactly once.
        assert trade_calls == list(range(trade_subscribers))
        assert anomaly_calls == []


# ---------------------------------------------------------------------------
# Example-based tests
# ---------------------------------------------------------------------------


class TestBusFailureIsolation:
    """A raising handler does not skip later handlers; every failure is reported."""

    def test_second_handler_still_runs_after_first_raises(self) -> None:
        """When handler #1 raises, handler #2 still executes before the aggregate is raised."""
        bus = EventBus()
        seen: list[str] = []

        def bad(_event: TradeExecuted) -> None:
            seen.append("bad")
            raise RuntimeError("boom")

        def good(_event: TradeExecuted) -> None:
            seen.append("good")

        bus.subscribe(TradeExecuted, bad)
        bus.subscribe(TradeExecuted, good)

        with pytest.raises(EventDispatchError) as exc_info:
            bus.publish(_trade())

        # Order is preserved — "bad" ran first, then "good".
        assert seen == ["bad", "good"]
        # The aggregate carries the single RuntimeError raised by "bad".
        aggregate = exc_info.value
        assert len(aggregate.failures) == 1
        assert isinstance(aggregate.failures[0], RuntimeError)
        assert aggregate.event_type_name == "TradeExecuted"

    def test_multiple_raising_handlers_are_all_reported(self) -> None:
        """Every raising handler contributes an entry to failures in dispatch order."""
        bus = EventBus()

        def bad_one(_event: TradeExecuted) -> None:
            raise ValueError("first")

        def bad_two(_event: TradeExecuted) -> None:
            raise KeyError("second")

        bus.subscribe(TradeExecuted, bad_one)
        bus.subscribe(TradeExecuted, bad_two)

        with pytest.raises(EventDispatchError) as exc_info:
            bus.publish(_trade())

        failures = exc_info.value.failures
        assert len(failures) == 2
        assert isinstance(failures[0], ValueError)
        assert isinstance(failures[1], KeyError)

    def test_error_message_names_the_event_type(self) -> None:
        """EventDispatchError.__str__ mentions the qualified event class name."""
        bus = EventBus()

        def bad(_event: TradeExecuted) -> None:
            raise RuntimeError("nope")

        bus.subscribe(TradeExecuted, bad)
        with pytest.raises(EventDispatchError) as exc_info:
            bus.publish(_trade())
        assert "TradeExecuted" in str(exc_info.value)


class TestBusIntrospection:
    """subscriber_count reports how many handlers a given type has."""

    def test_zero_before_subscribing(self) -> None:
        """subscriber_count returns 0 when no handler is registered for the type."""
        bus = EventBus()
        assert bus.subscriber_count(TradeExecuted) == 0

    def test_increments_per_subscribe_call(self) -> None:
        """Every subscribe() adds exactly one slot to subscriber_count for that type."""
        bus = EventBus()
        bus.subscribe(TradeExecuted, lambda _e: None)
        bus.subscribe(TradeExecuted, lambda _e: None)
        bus.subscribe(TradeExecuted, lambda _e: None)
        assert bus.subscriber_count(TradeExecuted) == 3

    def test_scoped_to_the_requested_type(self) -> None:
        """subscriber_count for one type is unaffected by subscribers to a different type."""
        bus = EventBus()
        bus.subscribe(TradeExecuted, lambda _e: None)
        assert bus.subscriber_count(AnomalyDetected) == 0
        assert bus.subscriber_count(TradeExecuted) == 1


class TestBusEmptyDispatch:
    """Publishing before any subscription is a no-op — no error, no side effects."""

    def test_publish_with_no_subscribers_returns_without_error(self) -> None:
        """publish() on an empty bus completes normally and touches nothing."""
        bus = EventBus()
        # This must not raise — a fresh bus with no subscribers is valid.
        bus.publish(_trade())
        # Sentinel: state still empty.
        assert bus.subscriber_count(TradeExecuted) == 0


class TestBusExactTypeDispatch:
    """Fan-out uses exact type match — base-class fan-in requires explicit opt-in."""

    def test_subscribing_to_base_class_receives_no_concrete_events(self) -> None:
        """A DomainEvent subscription does NOT catch concrete-subclass publishes."""
        bus = EventBus()
        base_calls: list[DomainEvent] = []
        bus.subscribe(DomainEvent, base_calls.append)
        bus.publish(_trade())
        bus.publish(_anomaly())
        # Exact-type dispatch means neither the trade nor the anomaly reached the base handler.
        assert base_calls == []

    def test_third_event_type_with_no_subscribers_is_ignored(self) -> None:
        """Publishing an event whose class has zero subscribers is a silent no-op."""
        bus = EventBus()
        seen: list[str] = []
        bus.subscribe(TradeExecuted, lambda _e: seen.append("trade"))
        # _OtherEvent has never been subscribed to.
        bus.publish(
            _OtherEvent(
                occurred_at=datetime(2026, 8, 23, 14, 22, 3, tzinfo=UTC),
                correlation_id="01J9Z8T3M5BQR2FVKN4XWY6PH1",
                payload="ignored",
            )
        )
        assert seen == []
