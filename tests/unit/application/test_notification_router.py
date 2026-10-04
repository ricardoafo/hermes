"""Tests for the application-layer notification router.

Property + example based. Invariants exercised:

- **Routing table matches the decision doc**: every ``EventClass`` maps to
  the exact channel set from
  ``docs/decisions/2026-08-23-slack-alongside-telegram.md`` § Rationale.
  Enforced as an explicit expected mapping — if a future PR silently
  broadens a channel, this test fails and the reviewer must either
  update the ADR entry or revert the router change.
- **Fan-out**: classes flagged for both channels return both adapters
  (in Telegram-first order); classes flagged for one channel return
  exactly one; classes flagged for none return an empty tuple.
- **Missing-adapter tolerance**: when the table wants a channel but
  ``config`` has ``None`` for it, that slot is silently dropped — no
  exception, no fabrication.
- **Purity**: two calls with equal inputs return equal tuples; no state
  mutation across invocations.
- **Adapter order**: Telegram always precedes Slack when both fire.
- **Enum coverage**: the property test iterates every ``EventClass``
  member — a new member without a routing-table entry crashes the test
  immediately (``KeyError`` from the lookup), not silently at runtime.

Domain-purity note: this file imports the router + the ``NotifierPort``
Protocol only. It never imports Telegram / Slack adapter classes — those
live in ``hermes.adapters.notifier`` and slot in behind the port. The
fakes below implement ``NotifierPort`` structurally.

Reference: ``docs/decisions/2026-08-23-slack-alongside-telegram.md``;
    ``src/hermes/application/notification_router.py``;
    ``docs/guides/ai-authored-tests.md`` §"The hermes protocol"
    (docstring names the invariant, one behavior claim per test).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hermes.application.notification_router import (
    EventClass,
    RouterConfig,
    channels_for,
    route,
)
from hermes.domain.events.base import DomainEvent
from hermes.ports.notifier import NotifierPort

# ---------------------------------------------------------------------------
# Fakes — structurally implement NotifierPort so the router can pack them
# into its returned tuple. Recording lists let example tests assert
# invocation shape when we simulate the caller-side dispatch (though the
# router itself never invokes; see docstrings).
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _RecordingNotifier:
    """In-memory NotifierPort fake — records urgent + report calls.

    Attributes:
        label: Human-readable tag ("telegram" or "slack") so tests can
            assert ORDER inside the router's returned tuple by identity
            (``adapters[0] is telegram_fake``) rather than by class name.
        urgent_calls: Every ``notify_urgent`` invocation, in order.
        report_calls: Every ``notify_report`` invocation, in order.

    """

    label: str
    urgent_calls: list[dict[str, object]] = field(default_factory=list)
    report_calls: list[dict[str, object]] = field(default_factory=list)
    notify_calls: list[DomainEvent] = field(default_factory=list)

    def notify(self, event: DomainEvent) -> None:
        """Record a template-driven domain-event notification."""
        self.notify_calls.append(event)

    def notify_urgent(self, *, event: str, correlation_id: str, message: str) -> None:
        """Record a push-to-phone alert invocation."""
        self.urgent_calls.append(
            {"event": event, "correlation_id": correlation_id, "message": message}
        )

    def notify_report(
        self,
        *,
        event: str,
        correlation_id: str,
        title: str,
        body: str,
        attachments: list[Path] | None = None,
    ) -> None:
        """Record a rich long-form report invocation."""
        self.report_calls.append(
            {
                "event": event,
                "correlation_id": correlation_id,
                "title": title,
                "body": body,
                "attachments": attachments,
            }
        )


# ---------------------------------------------------------------------------
# The canonical routing table — this MUST stay in sync with
# docs/decisions/2026-08-23-slack-alongside-telegram.md § Rationale.
# Each row: (telegram_fires, slack_fires). If you change this table without
# amending the decision doc (or vice-versa), you have broken the invariant
# the router encodes.
# ---------------------------------------------------------------------------

_EXPECTED_ROUTING: dict[EventClass, tuple[bool, bool]] = {
    EventClass.SERVICE_CRASH: (True, False),
    EventClass.HEALTH_CHECK_FAIL: (True, False),
    EventClass.ROLLBACK_FIRED: (True, True),
    EventClass.CVE_DETECTED: (True, True),
    EventClass.DEPLOY_STATUS: (True, False),
    EventClass.WEEKLY_REBALANCE_REPORT: (False, True),
    EventClass.MONTHLY_TAX_REPORT: (False, True),
    EventClass.TRADE_FIRED_SINGLE: (False, False),
    # MVP1 additions — see 2026-08-23-slack-alongside-telegram.md §
    # "Extended MVP1 events".
    EventClass.STRATEGY_PAUSED: (True, True),
    EventClass.STRATEGY_RESUMED: (True, True),
    EventClass.BALANCE_LOW: (True, False),
    EventClass.TRADE_REJECTED_ESCALATED: (True, False),
    EventClass.DAILY_TRADE_DIGEST: (False, True),
    EventClass.POSITION_LARGE: (True, True),
    EventClass.ANOMALY_DETECTED: (True, True),
    # 2026-10-03-signal-notification-routing.md
    EventClass.SIGNAL_EMITTED: (True, True),
    # notification templates v2 (#144): the rebalance heads-up is routed.
    EventClass.REBALANCE_TRIGGERED: (True, True),
}


def _both_wired() -> tuple[RouterConfig, _RecordingNotifier, _RecordingNotifier]:
    """Return a RouterConfig with both channels wired plus the two fakes."""
    telegram = _RecordingNotifier(label="telegram")
    slack = _RecordingNotifier(label="slack")
    return RouterConfig(telegram=telegram, slack=slack), telegram, slack


# ---------------------------------------------------------------------------
# Property tests — every EventClass, every wiring shape
# ---------------------------------------------------------------------------


class TestRoutingTable:
    """The router matches the 2026-08-23 decisions-journal Rationale table."""

    @pytest.mark.parametrize("event_class", list(EventClass))
    def test_every_event_class_matches_expected_routing(self, event_class: EventClass) -> None:
        """For each EventClass, route() returns exactly the adapters flagged in the ADR table."""
        config, telegram, slack = _both_wired()
        expected_telegram, expected_slack = _EXPECTED_ROUTING[event_class]

        result = route(event_class, config)

        expected: list[NotifierPort] = []
        if expected_telegram:
            expected.append(telegram)
        if expected_slack:
            expected.append(slack)
        assert result == tuple(expected), (
            f"{event_class.name}: routing table drift — expected "
            f"telegram={expected_telegram}, slack={expected_slack}"
        )

    def test_routing_table_covers_every_enum_member(self) -> None:
        """No EventClass member is missing from the expected routing map."""
        # Guards against adding an enum member without updating both the
        # router's internal table and this expected map.
        assert set(_EXPECTED_ROUTING) == set(EventClass)


class TestFanOutOrdering:
    """When both channels fire, Telegram precedes Slack — deterministic order."""

    @pytest.mark.parametrize(
        "event_class",
        [
            EventClass.ROLLBACK_FIRED,
            EventClass.CVE_DETECTED,
            EventClass.STRATEGY_PAUSED,
            EventClass.POSITION_LARGE,
            EventClass.ANOMALY_DETECTED,
            EventClass.STRATEGY_RESUMED,
            EventClass.REBALANCE_TRIGGERED,
        ],
    )
    def test_telegram_precedes_slack_in_fan_out(self, event_class: EventClass) -> None:
        """Fan-out classes return the Telegram adapter at index 0 and Slack at index 1."""
        config, telegram, slack = _both_wired()

        result = route(event_class, config)

        # Explicit index checks: exact position matters for the caller's
        # audit-trail semantics (dispatch order = registration order).
        assert len(result) == 2
        assert result[0] is telegram
        assert result[1] is slack


class TestTelegramOnlyClasses:
    """Classes flagged Telegram-only return exactly the Telegram adapter."""

    @pytest.mark.parametrize(
        "event_class",
        [
            EventClass.SERVICE_CRASH,
            EventClass.HEALTH_CHECK_FAIL,
            EventClass.DEPLOY_STATUS,
            EventClass.BALANCE_LOW,
            EventClass.TRADE_REJECTED_ESCALATED,
        ],
    )
    def test_telegram_only_class_returns_telegram_alone(self, event_class: EventClass) -> None:
        """Telegram-only classes return a 1-tuple containing the Telegram adapter."""
        config, telegram, _slack = _both_wired()

        result = route(event_class, config)

        assert result == (telegram,)


class TestSlackOnlyClasses:
    """Classes flagged Slack-only return exactly the Slack adapter."""

    @pytest.mark.parametrize(
        "event_class",
        [
            EventClass.WEEKLY_REBALANCE_REPORT,
            EventClass.MONTHLY_TAX_REPORT,
            EventClass.DAILY_TRADE_DIGEST,
        ],
    )
    def test_slack_only_class_returns_slack_alone(self, event_class: EventClass) -> None:
        """Slack-only classes return a 1-tuple containing the Slack adapter."""
        config, _telegram, slack = _both_wired()

        result = route(event_class, config)

        assert result == (slack,)


class TestLogOnlyClasses:
    """Classes flagged log-only return an empty tuple — no channel fires."""

    def test_trade_fired_single_returns_no_adapters(self) -> None:
        """TRADE_FIRED_SINGLE returns an empty tuple — individual trades are log-only per ADR."""
        config, _telegram, _slack = _both_wired()

        result = route(EventClass.TRADE_FIRED_SINGLE, config)

        assert result == ()


# ---------------------------------------------------------------------------
# Missing-adapter tolerance — the router never fabricates, never raises
# ---------------------------------------------------------------------------


class TestMissingAdapterTolerance:
    """A wanted-but-None channel drops silently from the returned tuple."""

    def test_no_telegram_wired_drops_telegram_slot(self) -> None:
        """Fan-out class with telegram=None returns Slack alone, no exception raised."""
        slack = _RecordingNotifier(label="slack")
        config = RouterConfig(telegram=None, slack=slack)

        result = route(EventClass.ROLLBACK_FIRED, config)

        assert result == (slack,)

    def test_no_slack_wired_drops_slack_slot(self) -> None:
        """Fan-out class with slack=None returns Telegram alone, no exception raised."""
        telegram = _RecordingNotifier(label="telegram")
        config = RouterConfig(telegram=telegram, slack=None)

        result = route(EventClass.ROLLBACK_FIRED, config)

        assert result == (telegram,)

    def test_neither_wired_returns_empty_tuple(self) -> None:
        """Fan-out class with both channels None returns () — no fabrication, no crash."""
        config = RouterConfig(telegram=None, slack=None)

        result = route(EventClass.ROLLBACK_FIRED, config)

        assert result == ()

    @pytest.mark.parametrize("event_class", list(EventClass))
    def test_empty_config_never_raises_for_any_event_class(self, event_class: EventClass) -> None:
        """Every EventClass survives an unwired config — returns () rather than raising."""
        # This is the safety net: even a totally-unwired composition
        # (early boot, misconfigured env) must not crash on route().
        result = route(event_class, RouterConfig())

        assert result == ()


# ---------------------------------------------------------------------------
# Purity — same input → same output, no state carryover
# ---------------------------------------------------------------------------


class TestRouterPurity:
    """route() is a pure function: repeated calls yield equal tuples."""

    @given(event_class=st.sampled_from(list(EventClass)))
    def test_same_input_produces_same_output_across_calls(self, event_class: EventClass) -> None:
        """Two invocations with the same EventClass + config return equal tuples."""
        config, telegram, slack = _both_wired()

        first = route(event_class, config)
        second = route(event_class, config)

        # Build the expected tuple from the ADR-verified _EXPECTED_ROUTING —
        # asserts both purity (first == second) AND correctness (both equal
        # the ADR-encoded expected value) in one shot. The len() checks
        # anchor a Constant so the vague-assertion AST walker sees a real
        # comparison (both-Names comparisons are flagged as vague).
        expected_telegram, expected_slack = _EXPECTED_ROUTING[event_class]
        expected_size = int(expected_telegram) + int(expected_slack)
        expected: list[NotifierPort] = []
        if expected_telegram:
            expected.append(telegram)
        if expected_slack:
            expected.append(slack)
        expected_tuple = tuple(expected)

        assert len(first) == expected_size
        assert len(second) == expected_size
        assert first == expected_tuple
        assert second == expected_tuple

    @given(event_class=st.sampled_from(list(EventClass)))
    def test_return_type_is_always_a_tuple(self, event_class: EventClass) -> None:
        """route() always returns a tuple — never a list, never None."""
        config, _telegram, _slack = _both_wired()

        result = route(event_class, config)

        assert isinstance(result, tuple)


# ---------------------------------------------------------------------------
# Adapter-injection integrity — the router uses the exact objects handed in
# ---------------------------------------------------------------------------


class TestAdapterInjectionIntegrity:
    """The router returns the exact adapter instances from RouterConfig, not copies."""

    def test_returned_telegram_adapter_is_the_config_instance(self) -> None:
        """The Telegram entry in the result tuple is the same object as config.telegram."""
        config, telegram, _slack = _both_wired()

        result = route(EventClass.SERVICE_CRASH, config)

        # `is` (identity) — not `==`. The router must not wrap, copy,
        # or reconstruct adapters; the caller-provided instance flows
        # through unchanged so its state (e.g. per-adapter rate limits)
        # is preserved.
        assert result[0] is telegram

    def test_returned_slack_adapter_is_the_config_instance(self) -> None:
        """The Slack entry in the result tuple is the same object as config.slack."""
        config, _telegram, slack = _both_wired()

        result = route(EventClass.WEEKLY_REBALANCE_REPORT, config)

        assert result[0] is slack


# ---------------------------------------------------------------------------
# Caller-side dispatch smoke test — simulates the EventBus subscriber
# invoking the returned adapters. The router does NOT invoke; this test
# proves the returned tuple is USEFUL — the caller can iterate + dispatch.
# ---------------------------------------------------------------------------


class TestChannelsForHelper:
    """The channels_for introspection helper mirrors the routing table row-for-row."""

    @pytest.mark.parametrize("event_class", list(EventClass))
    def test_channels_for_matches_expected_routing(self, event_class: EventClass) -> None:
        """channels_for(x) returns the (telegram, slack) pair from the ADR-verified map."""
        assert channels_for(event_class) == _EXPECTED_ROUTING[event_class]


class TestCallerSideDispatchSmoke:
    """A caller iterating the returned tuple can invoke notify_urgent on every adapter."""

    def test_iterating_result_invokes_every_returned_adapter(self) -> None:
        """After the caller iterates route()'s output, each returned adapter records one call."""
        config, telegram, slack = _both_wired()

        adapters = route(EventClass.ROLLBACK_FIRED, config)
        for adapter in adapters:
            adapter.notify_urgent(  # type: ignore[attr-defined]
                event="ROLLBACK_FIRED",
                correlation_id="01J9Z8T3M5BQR2FVKN4XWY6PH1",
                message="rollback fired",
            )

        assert len(telegram.urgent_calls) == 1
        assert len(slack.urgent_calls) == 1
        # Payload flow-through check — verifies the caller-side invocation
        # actually reaches the adapter unchanged (not a mock swallow).
        assert telegram.urgent_calls[0]["message"] == "rollback fired"
        assert slack.urgent_calls[0]["message"] == "rollback fired"
