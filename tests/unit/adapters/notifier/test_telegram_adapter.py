"""Contract tests for :class:`hermes.adapters.notifier.TelegramAdapter`.

Every test protects one invariant of the Telegram adapter contract. Wire
calls are mocked via ``respx`` (declared in dev deps per ADR-007). No real
socket ever opens; no real credentials are ever transmitted.

Reference: docs/decisions/2026-08-23-slack-alongside-telegram.md.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest
import respx
import structlog
from structlog.testing import capture_logs

from hermes.adapters.notifier import (
    TelegramAdapter,
    TelegramConfig,
    TelegramNotifierError,
)
from hermes.domain.errors import HermesError, InfraError
from hermes.domain.events.anomaly_detected import AnomalyDetected, AnomalySeverity
from hermes.domain.events.balance_low import BalanceLow
from hermes.domain.events.base import DomainEvent
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.money import Money
from hermes.ports.notifier import NotifierPort
from tests.support.notification_samples import samples

_TEST_TOKEN = "123456:ABC-DEF"  # noqa: S105 - test-only bot token, never real
_TEST_CHAT = "-1001234567890"
_TEST_CORRELATION_ID = "01HKQMZXYZ0123456789ABCDEF"  # 26-char pseudo-ULID
_TEST_CORRELATION_TAIL = "ABCDEF"  # last 6 chars
_API_URL = f"https://api.telegram.org/bot{_TEST_TOKEN}/sendMessage"
_GET_ME_URL = f"https://api.telegram.org/bot{_TEST_TOKEN}/getMe"


@pytest.fixture
def config() -> TelegramConfig:
    """Return a fixed :class:`TelegramConfig` for adapter construction."""
    return TelegramConfig(bot_token=_TEST_TOKEN, chat_id=_TEST_CHAT)


@pytest.fixture
def adapter(config: TelegramConfig) -> TelegramAdapter:
    """Return a :class:`TelegramAdapter` wired with a plain httpx.Client.

    ``respx`` patches transport globally in the ``respx_mock`` context
    manager per test, so the client here does NOT need MockTransport.
    """
    client = httpx.Client(base_url="https://api.telegram.org", timeout=5.0)
    return TelegramAdapter(config=config, client=client)


class TestTelegramConfig:
    """Verify credential-loading contract for :class:`TelegramConfig`."""

    def test_from_env_loads_both_variables(self) -> None:
        """from_env returns config populated from the two HERMES_TELEGRAM__* env vars."""
        env = {
            "HERMES_TELEGRAM__BOT_TOKEN": "token-abc",
            "HERMES_TELEGRAM__CHAT_ID": "chat-xyz",
        }
        cfg = TelegramConfig.from_env(env)
        assert cfg.bot_token == "token-abc"  # noqa: S105 - test literal, not a real secret
        assert cfg.chat_id == "chat-xyz"

    def test_from_env_raises_when_bot_token_missing(self) -> None:
        """Missing HERMES_TELEGRAM__BOT_TOKEN raises TelegramNotifierError at load time."""
        env = {"HERMES_TELEGRAM__CHAT_ID": "chat-xyz"}
        with pytest.raises(TelegramNotifierError, match="BOT_TOKEN"):
            TelegramConfig.from_env(env)

    def test_from_env_raises_when_chat_id_missing(self) -> None:
        """Missing HERMES_TELEGRAM__CHAT_ID raises TelegramNotifierError at load time."""
        env = {"HERMES_TELEGRAM__BOT_TOKEN": "token-abc"}
        with pytest.raises(TelegramNotifierError, match="CHAT_ID"):
            TelegramConfig.from_env(env)

    def test_from_env_treats_whitespace_as_missing(self) -> None:
        """A whitespace-only env var is treated as missing (fail-fast, no silent success)."""
        env = {
            "HERMES_TELEGRAM__BOT_TOKEN": "   ",
            "HERMES_TELEGRAM__CHAT_ID": "chat-xyz",
        }
        with pytest.raises(TelegramNotifierError, match="BOT_TOKEN"):
            TelegramConfig.from_env(env)

    def test_repr_hides_the_bot_token(self, adapter: TelegramAdapter) -> None:
        """Rendering the config or the adapter never prints the token (#506).

        Logs, tracebacks and error reports render objects with ``repr``.
        """
        rendered = repr(adapter.config) + repr(adapter)
        assert _TEST_TOKEN not in rendered
        assert _TEST_CHAT in rendered


class TestNotifyUrgentHappyPath:
    """Verify successful message-send behaviour of :meth:`notify_urgent`."""

    def test_notify_urgent_posts_to_send_message_endpoint(self, adapter: TelegramAdapter) -> None:
        """notify_urgent hits POST /bot<TOKEN>/sendMessage on api.telegram.org."""
        with respx.mock(assert_all_called=True) as router:
            route = router.post(_API_URL).mock(
                return_value=httpx.Response(200, json={"ok": True, "result": {}})
            )
            adapter.notify_urgent(
                event="rollback.fired",
                correlation_id=_TEST_CORRELATION_ID,
                message="Rollback executed on api node 3.",
            )
            assert route.called
            assert route.call_count == 1

    def test_notify_urgent_payload_includes_chat_id_and_markdown_v2(
        self, adapter: TelegramAdapter
    ) -> None:
        """Payload carries chat_id, MarkdownV2 parse_mode, and disable_web_page_preview=True."""
        with respx.mock() as router:
            route = router.post(_API_URL).mock(
                return_value=httpx.Response(200, json={"ok": True, "result": {}})
            )
            adapter.notify_urgent(
                event="health.check.fail",
                correlation_id=_TEST_CORRELATION_ID,
                message="Simple message",
            )
            request = route.calls[0].request
            payload = json.loads(request.content)
            assert payload["chat_id"] == _TEST_CHAT
            assert payload["parse_mode"] == "MarkdownV2"
            assert payload["disable_web_page_preview"] is True

    def test_notify_urgent_includes_correlation_tail_in_message_text(
        self, adapter: TelegramAdapter
    ) -> None:
        """The message text contains the last 6 chars of the correlation ID per ADR-008."""
        with respx.mock() as router:
            route = router.post(_API_URL).mock(
                return_value=httpx.Response(200, json={"ok": True, "result": {}})
            )
            adapter.notify_urgent(
                event="service.crash",
                correlation_id=_TEST_CORRELATION_ID,
                message="node down",
            )
            payload = json.loads(route.calls[0].request.content)
            # correlation tail escaped inside brackets
            assert _TEST_CORRELATION_TAIL in payload["text"]

    def test_notify_urgent_escapes_markdown_v2_reserved_chars(
        self, adapter: TelegramAdapter
    ) -> None:
        """Reserved MarkdownV2 chars in event/message are backslash-escaped for literal render."""
        with respx.mock() as router:
            route = router.post(_API_URL).mock(
                return_value=httpx.Response(200, json={"ok": True, "result": {}})
            )
            # event has dots (reserved); message has parens + underscore + dot
            adapter.notify_urgent(
                event="rebalance.cycle.decided",
                correlation_id=_TEST_CORRELATION_ID,
                message="pair=BTC_USDT (delta=+0.5%)",
            )
            text = json.loads(route.calls[0].request.content)["text"]
            # Each reserved char in the event must appear escaped
            assert "rebalance\\.cycle\\.decided" in text
            # Message chars: _ ( ) . % + - are reserved
            assert "BTC\\_USDT" in text
            assert "\\(delta" in text
            assert "0\\.5" in text

    def test_notify_urgent_short_correlation_id_used_verbatim(
        self, adapter: TelegramAdapter
    ) -> None:
        """A correlation ID shorter than 6 chars is used in full (no exception)."""
        with respx.mock() as router:
            route = router.post(_API_URL).mock(
                return_value=httpx.Response(200, json={"ok": True, "result": {}})
            )
            adapter.notify_urgent(
                event="deploy.ok",
                correlation_id="abc",
                message="ok",
            )
            text = json.loads(route.calls[0].request.content)["text"]
            assert "abc" in text


class TestNotifyUrgentErrorPaths:
    """Verify :meth:`notify_urgent` raises on every documented failure mode."""

    def test_rate_limit_429_raises_notifier_error(self, adapter: TelegramAdapter) -> None:
        """HTTP 429 (rate limit) raises TelegramNotifierError instead of silent retry."""
        with respx.mock() as router:
            router.post(_API_URL).mock(
                return_value=httpx.Response(
                    429,
                    json={
                        "ok": False,
                        "error_code": 429,
                        "description": "Too Many Requests: retry after 30",
                        "parameters": {"retry_after": 30},
                    },
                )
            )
            with pytest.raises(TelegramNotifierError, match="429"):
                adapter.notify_urgent(
                    event="deploy.done",
                    correlation_id=_TEST_CORRELATION_ID,
                    message="deploy 1.2.3 shipped",
                )

    def test_auth_failure_401_raises_notifier_error(self, adapter: TelegramAdapter) -> None:
        """HTTP 401 (bad bot token) raises TelegramNotifierError with the status in the message."""
        with respx.mock() as router:
            router.post(_API_URL).mock(
                return_value=httpx.Response(
                    401,
                    json={"ok": False, "error_code": 401, "description": "Unauthorized"},
                )
            )
            with pytest.raises(TelegramNotifierError, match="401"):
                adapter.notify_urgent(
                    event="cve.detected",
                    correlation_id=_TEST_CORRELATION_ID,
                    message="CVE-2026-9999 in urllib3",
                )

    def test_forbidden_403_raises_notifier_error(self, adapter: TelegramAdapter) -> None:
        """HTTP 403 (bot kicked from chat) raises TelegramNotifierError with the status."""
        with respx.mock() as router:
            router.post(_API_URL).mock(
                return_value=httpx.Response(
                    403,
                    json={"ok": False, "error_code": 403, "description": "Forbidden"},
                )
            )
            with pytest.raises(TelegramNotifierError, match="403"):
                adapter.notify_urgent(
                    event="rollback.fired",
                    correlation_id=_TEST_CORRELATION_ID,
                    message="rollback fired",
                )

    def test_network_error_wraps_httpx_exception(self, adapter: TelegramAdapter) -> None:
        """A transport-level failure (httpx.ConnectError) is wrapped as TelegramNotifierError."""
        with respx.mock() as router:
            router.post(_API_URL).mock(side_effect=httpx.ConnectError("DNS failed"))
            with pytest.raises(TelegramNotifierError, match="transport failure"):
                adapter.notify_urgent(
                    event="health.check.fail",
                    correlation_id=_TEST_CORRELATION_ID,
                    message="db unreachable",
                )

    def test_ok_false_response_raises_notifier_error(self, adapter: TelegramAdapter) -> None:
        """A 200 response with ok=false in the body still raises — Bot API rejected us."""
        with respx.mock() as router:
            router.post(_API_URL).mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "ok": False,
                        "error_code": 400,
                        "description": "Bad Request: chat not found",
                    },
                )
            )
            with pytest.raises(TelegramNotifierError, match="rejected"):
                adapter.notify_urgent(
                    event="deploy.failed",
                    correlation_id=_TEST_CORRELATION_ID,
                    message="deploy 1.2.3 rolled back",
                )

    def test_non_json_response_raises_notifier_error(self, adapter: TelegramAdapter) -> None:
        """A 2xx response that is not JSON raises TelegramNotifierError, not ValueError."""
        with respx.mock() as router:
            router.post(_API_URL).mock(
                return_value=httpx.Response(
                    200,
                    content=b"<html>gateway proxy error</html>",
                    headers={"content-type": "text/html"},
                )
            )
            with pytest.raises(TelegramNotifierError, match="non-JSON"):
                adapter.notify_urgent(
                    event="service.crash",
                    correlation_id=_TEST_CORRELATION_ID,
                    message="oops",
                )

    def test_notifier_error_inherits_from_infra_error(self) -> None:
        """TelegramNotifierError is an InfraError so 'except InfraError' catches it."""
        err = TelegramNotifierError("something")
        assert isinstance(err, InfraError)
        assert isinstance(err, HermesError)


class TestNotifyReportNoOp:
    """Verify :meth:`notify_report` is a no-op with a warning log per decisions journal."""

    def test_notify_report_does_not_call_telegram_api(self, adapter: TelegramAdapter) -> None:
        """notify_report makes ZERO HTTP calls (Telegram is urgent-only channel)."""
        with respx.mock(assert_all_called=False) as router:
            route = router.post(_API_URL).mock(return_value=httpx.Response(200, json={"ok": True}))
            adapter.notify_report(
                event="weekly.rebalance.report",
                correlation_id=_TEST_CORRELATION_ID,
                title="Weekly rebalance",
                body="Some long body",
                attachments=None,
            )
            assert not route.called

    def test_notify_report_logs_warning_with_correlation_tail(
        self, adapter: TelegramAdapter
    ) -> None:
        """notify_report emits a WARNING log with event name + correlation tail for triage."""
        structlog.configure(
            processors=[structlog.processors.KeyValueRenderer()],
            wrapper_class=structlog.make_filtering_bound_logger(0),
            cache_logger_on_first_use=False,
        )
        with capture_logs() as logs:
            adapter.notify_report(
                event="monthly.tax.report",
                correlation_id=_TEST_CORRELATION_ID,
                title="Q1",
                body="body",
                attachments=None,
            )
        assert any(
            entry.get("log_level") == "warning"
            and entry.get("event") == "telegram.notify_report.noop"
            and entry.get("event_name") == "monthly.tax.report"
            and entry.get("correlation_tail") == _TEST_CORRELATION_TAIL
            for entry in logs
        ), f"expected a warning noop entry, got: {logs}"


class TestPortConformance:
    """Verify :class:`TelegramAdapter` satisfies the :class:`NotifierPort` Protocol."""

    def test_adapter_is_notifier_port_instance(self, adapter: TelegramAdapter) -> None:
        """The isinstance check against the runtime_checkable Protocol returns True."""
        assert isinstance(adapter, NotifierPort)


class TestCreateAndClose:
    """Verify the :meth:`TelegramAdapter.create` factory and :meth:`close` lifecycle."""

    def test_create_returns_adapter_with_default_client(self, config: TelegramConfig) -> None:
        """create() builds an adapter whose client targets api.telegram.org."""
        adapter = TelegramAdapter.create(config)
        try:
            assert isinstance(adapter.client, httpx.Client)
            assert str(adapter.client.base_url) == "https://api.telegram.org"
        finally:
            adapter.close()

    def test_close_is_idempotent(self, config: TelegramConfig) -> None:
        """close() called twice does not raise (second call is a no-op)."""
        adapter = TelegramAdapter.create(config)
        adapter.close()
        adapter.close()  # must not raise


class TestNotifyEvent:
    """Verify :meth:`notify` (event-based) delegates to the template registry."""

    def test_notify_posts_rendered_template_to_send_message(self, adapter: TelegramAdapter) -> None:
        """notify(event) hits sendMessage with the text produced by telegram_template."""
        event = AnomalyDetected(
            occurred_at=datetime(2026, 8, 23, 14, 22, 3, tzinfo=UTC),
            correlation_id=_TEST_CORRELATION_ID,
            source="swing.risk",
            severity=AnomalySeverity.CRITICAL,
            reason="30% drawdown breached",
        )
        with respx.mock() as router:
            route = router.post(_API_URL).mock(
                return_value=httpx.Response(200, json={"ok": True, "result": {}})
            )
            adapter.notify(event)
            assert route.call_count == 1
            payload = json.loads(route.calls[0].request.content)
            assert payload["chat_id"] == _TEST_CHAT
            assert payload["parse_mode"] == "MarkdownV2"
            assert payload["disable_web_page_preview"] is True
            # The anomaly template renders the source (dot escaped for MarkdownV2).
            assert "Anomaly \u00b7 swing\\.risk" in payload["text"]
            assert _TEST_CORRELATION_TAIL in payload["text"]

    def test_notify_uses_template_for_different_event_types(self, adapter: TelegramAdapter) -> None:
        """Notify dispatches on event type: BalanceLow renders as a low-balance message."""
        usdt = Currency.USDT
        event = BalanceLow(
            occurred_at=datetime(2026, 8, 23, 14, 22, 3, tzinfo=UTC),
            correlation_id=_TEST_CORRELATION_ID,
            asset=usdt,
            current=Money(amount=Decimal("50"), currency=usdt),
            threshold=Money(amount=Decimal("100"), currency=usdt),
            checked_at=datetime(2026, 8, 23, 14, 22, 3, tzinfo=UTC),
        )
        with respx.mock() as router:
            route = router.post(_API_URL).mock(
                return_value=httpx.Response(200, json={"ok": True, "result": {}})
            )
            adapter.notify(event)
            payload = json.loads(route.calls[0].request.content)
            assert "Balance low \u00b7 USDT" in payload["text"]

    def test_critical_event_rings_the_phone(self, adapter: TelegramAdapter) -> None:
        """A CRITICAL anomaly (P0) is sent loud: disable_notification is false."""
        event = AnomalyDetected(
            occurred_at=datetime(2026, 8, 23, 14, 22, 3, tzinfo=UTC),
            correlation_id=_TEST_CORRELATION_ID,
            source="swing.risk",
            severity=AnomalySeverity.CRITICAL,
            reason="kill switch",
        )
        with respx.mock() as router:
            route = router.post(_API_URL).mock(
                return_value=httpx.Response(200, json={"ok": True, "result": {}})
            )
            adapter.notify(event)
            payload = json.loads(route.calls[0].request.content)
            assert payload["disable_notification"] is False

    def test_non_critical_event_is_sent_silently(self, adapter: TelegramAdapter) -> None:
        """A WARNING anomaly is sent silent: disable_notification is true."""
        event = AnomalyDetected(
            occurred_at=datetime(2026, 8, 23, 14, 22, 3, tzinfo=UTC),
            correlation_id=_TEST_CORRELATION_ID,
            source="reconciler",
            severity=AnomalySeverity.WARNING,
            reason="drift detected",
        )
        with respx.mock() as router:
            route = router.post(_API_URL).mock(
                return_value=httpx.Response(200, json={"ok": True, "result": {}})
            )
            adapter.notify(event)
            payload = json.loads(route.calls[0].request.content)
            assert payload["disable_notification"] is True

    def test_notify_urgent_is_loud(self, adapter: TelegramAdapter) -> None:
        """The legacy urgent wrapper wraps a CRITICAL anomaly, so it still rings the phone."""
        with respx.mock() as router:
            route = router.post(_API_URL).mock(
                return_value=httpx.Response(200, json={"ok": True, "result": {}})
            )
            adapter.notify_urgent(
                event="health.check.fail",
                correlation_id=_TEST_CORRELATION_ID,
                message="db unreachable",
            )
            payload = json.loads(route.calls[0].request.content)
            assert payload["disable_notification"] is False

    def test_notify_wire_failure_raises_notifier_error(self, adapter: TelegramAdapter) -> None:
        """A network error inside notify() surfaces as TelegramNotifierError."""
        event = AnomalyDetected(
            occurred_at=datetime(2026, 8, 23, 14, 22, 3, tzinfo=UTC),
            correlation_id=_TEST_CORRELATION_ID,
            source="reconciler",
            severity=AnomalySeverity.WARNING,
            reason="drift detected",
        )
        with respx.mock() as router:
            router.post(_API_URL).mock(side_effect=httpx.ConnectError("DNS failed"))
            with pytest.raises(TelegramNotifierError, match="transport failure"):
                adapter.notify(event)


class TestGetMe:
    """Verify :meth:`get_me` — the read-only health probe used by `hermes health`."""

    def test_get_me_returns_result_object_on_success(self, adapter: TelegramAdapter) -> None:
        """get_me returns the 'result' object from a successful Bot API response."""
        with respx.mock() as router:
            router.get(_GET_ME_URL).mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "ok": True,
                        "result": {
                            "id": 42,
                            "is_bot": True,
                            "username": "hermes_bot",
                            "first_name": "Hermes",
                        },
                    },
                )
            )
            result = adapter.get_me()
        assert result["username"] == "hermes_bot"
        assert result["is_bot"] is True

    def test_get_me_401_raises_notifier_error(self, adapter: TelegramAdapter) -> None:
        """HTTP 401 from getMe raises TelegramNotifierError with the status in the msg."""
        with respx.mock() as router:
            router.get(_GET_ME_URL).mock(
                return_value=httpx.Response(
                    401,
                    json={"ok": False, "error_code": 401, "description": "Unauthorized"},
                )
            )
            with pytest.raises(TelegramNotifierError, match="401"):
                adapter.get_me()

    def test_get_me_ok_false_raises_notifier_error(self, adapter: TelegramAdapter) -> None:
        """A 200 response with ok=false still raises — Bot API rejected the probe."""
        with respx.mock() as router:
            router.get(_GET_ME_URL).mock(
                return_value=httpx.Response(
                    200,
                    json={"ok": False, "error_code": 400, "description": "Bad Request"},
                )
            )
            with pytest.raises(TelegramNotifierError, match="rejected"):
                adapter.get_me()

    def test_get_me_transport_error_wraps_httpx_exception(self, adapter: TelegramAdapter) -> None:
        """A transport-level failure during getMe wraps to TelegramNotifierError."""
        with respx.mock() as router:
            router.get(_GET_ME_URL).mock(side_effect=httpx.ConnectError("DNS failed"))
            with pytest.raises(TelegramNotifierError, match="transport failure"):
                adapter.get_me()

    def test_get_me_non_json_response_raises_notifier_error(self, adapter: TelegramAdapter) -> None:
        """A 2xx response that is not JSON raises TelegramNotifierError, not ValueError."""
        with respx.mock() as router:
            router.get(_GET_ME_URL).mock(
                return_value=httpx.Response(
                    200,
                    content=b"<html>gateway error</html>",
                    headers={"content-type": "text/html"},
                )
            )
            with pytest.raises(TelegramNotifierError, match="non-JSON"):
                adapter.get_me()

    def test_get_me_missing_result_object_raises_notifier_error(
        self, adapter: TelegramAdapter
    ) -> None:
        """A 200 ok=true response without a 'result' object raises TelegramNotifierError."""
        with respx.mock() as router:
            router.get(_GET_ME_URL).mock(return_value=httpx.Response(200, json={"ok": True}))
            with pytest.raises(TelegramNotifierError, match="missing 'result'"):
                adapter.get_me()


class TestEurRate:
    """The injected rate lookup decides whether a push shows EUR or USDT (change 668)."""

    @staticmethod
    def _sent_text(adapter: TelegramAdapter) -> str:
        with respx.mock() as router:
            route = router.post(_API_URL).mock(
                return_value=httpx.Response(200, json={"ok": True, "result": {}})
            )
            adapter.notify(samples()["signal-entry"])
            return str(json.loads(route.calls[0].request.content)["text"])

    def test_the_lookup_receives_the_event_and_its_rate_reaches_the_wire(
        self, config: TelegramConfig
    ) -> None:
        """Invariant (668 R-4): the lookup gets the event; its rate prices the push."""
        seen: list[DomainEvent] = []

        def lookup(event: DomainEvent) -> Decimal | None:
            seen.append(event)
            return Decimal("1.25")

        client = httpx.Client(base_url="https://api.telegram.org", timeout=5.0)
        adapter = TelegramAdapter(config=config, client=client, eur_rate=lookup)

        text = self._sent_text(adapter)

        assert (seen, text.splitlines()[0]) == (
            [samples()["signal-entry"]],
            "\u25b2 Entry long BTC \u00b7 \u20ac41,928\\.16",
        )

    def test_a_lookup_with_no_rate_sends_the_usdt_text(self, config: TelegramConfig) -> None:
        """Invariant (668 R-2): a cold cache keeps the USDT text, visibly and never wrong."""
        client = httpx.Client(base_url="https://api.telegram.org", timeout=5.0)
        adapter = TelegramAdapter(config=config, client=client, eur_rate=lambda _event: None)
        assert self._sent_text(adapter).splitlines()[0] == (
            "\u25b2 Entry long BTC \u00b7 52,410\\.20 USDT"
        )

    def test_a_failing_lookup_still_delivers_the_push_in_usdt(self, config: TelegramConfig) -> None:
        """Invariant (668 R-2): a lookup that raises never stops the notification."""

        def boom(_event: DomainEvent) -> Decimal | None:
            raise RuntimeError("cache exploded")

        client = httpx.Client(base_url="https://api.telegram.org", timeout=5.0)
        adapter = TelegramAdapter(config=config, client=client, eur_rate=boom)
        assert "52,410\\.20 USDT" in self._sent_text(adapter)

    def test_create_passes_the_lookup_to_the_adapter(self, config: TelegramConfig) -> None:
        """Invariant (668 D-2): the factory serve uses carries the lookup into the adapter."""

        def lookup(_event: DomainEvent) -> Decimal | None:
            return Decimal("1.25")

        adapter = TelegramAdapter.create(config, eur_rate=lookup)
        try:
            assert adapter.eur_rate is lookup
        finally:
            adapter.close()
