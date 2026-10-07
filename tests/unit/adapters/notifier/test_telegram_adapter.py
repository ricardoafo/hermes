"""Contract tests for :class:`hermes.adapters.notifier.TelegramAdapter`.

Every test protects one invariant of the Telegram adapter contract. Wire
calls are mocked via ``respx`` (declared in dev deps per ADR-007); every
request the adapter makes is pinned by method, URL and JSON body, and every
failure by its exact exception message. No real socket ever opens; no real
credentials are ever transmitted.

The mocked wire is the Bot API seam; its real counterpart is
``tests/contract/test_telegram_getme.py::test_get_me_against_real_telegram_api``.

Reference: docs/decisions/2026-08-23-slack-alongside-telegram.md.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx
import pytest
import respx
from structlog.testing import capture_logs

from hermes.adapters.notifier import (
    TelegramAdapter,
    TelegramConfig,
    TelegramNotifierError,
)
from hermes.domain.errors import InfraError
from hermes.domain.events.anomaly_detected import AnomalyDetected, AnomalySeverity
from hermes.domain.events.balance_low import BalanceLow
from hermes.domain.events.base import DomainEvent
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.money import Money
from tests.support.notification_samples import samples

_TEST_TOKEN = "123456:ABC-DEF"  # noqa: S105 - test-only bot token, never real  # gitleaks:allow
_TEST_CHAT = "-1001234567890"
_TEST_CORRELATION_ID = "01HKQMZXYZ0123456789ABCDEF"  # 26-char pseudo-ULID
_API_URL = f"https://api.telegram.org/bot{_TEST_TOKEN}/sendMessage"
_GET_ME_URL = f"https://api.telegram.org/bot{_TEST_TOKEN}/getMe"
# 14:22:03 UTC on Sunday 2026-08-23 is 16:22 in Madrid (CEST, UTC+2).
_AT = datetime(2026, 8, 23, 14, 22, 3, tzinfo=UTC)


def _ok() -> httpx.Response:
    """Return a fresh successful sendMessage response."""
    return httpx.Response(200, json={"ok": True, "result": {}})


@pytest.fixture
def config() -> TelegramConfig:
    """Return a fixed :class:`TelegramConfig` for adapter construction."""
    return TelegramConfig(bot_token=_TEST_TOKEN, chat_id=_TEST_CHAT)


@pytest.fixture
def adapter(config: TelegramConfig) -> TelegramAdapter:
    """Return a :class:`TelegramAdapter` wired with a plain httpx.Client.

    ``respx`` patches transport globally in the ``respx.mock`` context
    manager per test, so the client here does NOT need MockTransport.
    """
    client = httpx.Client(base_url="https://api.telegram.org", timeout=5.0)
    return TelegramAdapter(config=config, client=client, sleep=lambda _sec: None)


class _FakeTime:
    """A monotonic clock that only moves when the adapter sleeps or a request runs."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _timed_adapter(config: TelegramConfig, fake: _FakeTime) -> TelegramAdapter:
    """Return an adapter whose back-off sleeps and reads ``fake`` instead of real time."""
    client = httpx.Client(base_url="https://api.telegram.org", timeout=10.0)
    return TelegramAdapter(config=config, client=client, sleep=fake.sleep, monotonic=fake.monotonic)


def _too_many(retry_after: int | None = None, **headers: str) -> httpx.Response:
    """Return a Bot API 429, with ``parameters.retry_after`` when given."""
    body: dict[str, Any] = {"ok": False, "error_code": 429, "description": "Too Many Requests"}
    if retry_after is not None:
        body["parameters"] = {"retry_after": retry_after}
    return httpx.Response(429, json=body, headers=headers)


def _anomaly(severity: AnomalySeverity = AnomalySeverity.CRITICAL) -> AnomalyDetected:
    """Return a fixed anomaly event at :data:`_AT`."""
    return AnomalyDetected(
        occurred_at=_AT,
        correlation_id=_TEST_CORRELATION_ID,
        source="swing.risk",
        severity=severity,
        reason="30% drawdown breached",
    )


def _sent_payload(adapter: TelegramAdapter, **urgent: str) -> dict[str, Any]:
    """Run ``notify_urgent`` against a 200 sendMessage and return the JSON body sent."""
    with respx.mock() as router:
        route = router.post(_API_URL).mock(return_value=_ok())
        adapter.notify_urgent(**urgent)
    body: dict[str, Any] = json.loads(route.calls[0].request.content)
    return body


def _urgent_error(adapter: TelegramAdapter, response: httpx.Response) -> str:
    """Return the message of the error ``notify_urgent`` raises for ``response``."""
    with respx.mock() as router:
        router.post(_API_URL).mock(return_value=response)
        with pytest.raises(TelegramNotifierError) as excinfo:
            adapter.notify_urgent(event="deploy.done", correlation_id="c", message="m")
    return str(excinfo.value)


def _get_me_error(adapter: TelegramAdapter, response: httpx.Response) -> str:
    """Return the message of the error ``get_me`` raises for ``response``."""
    with respx.mock() as router:
        router.get(_GET_ME_URL).mock(return_value=response)
        with pytest.raises(TelegramNotifierError) as excinfo:
            adapter.get_me()
    return str(excinfo.value)


class TestTelegramConfig:
    """Verify credential-loading contract for :class:`TelegramConfig`."""

    def test_from_env_loads_both_variables_stripped(self) -> None:
        """from_env takes the two HERMES_TELEGRAM__* values with surrounding blanks removed."""
        env = {
            "HERMES_TELEGRAM__BOT_TOKEN": "  token-abc\n",
            "HERMES_TELEGRAM__CHAT_ID": " chat-xyz ",
        }
        cfg = TelegramConfig.from_env(env)
        assert (cfg.bot_token, cfg.chat_id) == ("token-abc", "chat-xyz")

    def test_from_env_raises_when_bot_token_missing(self) -> None:
        """A missing HERMES_TELEGRAM__BOT_TOKEN fails at load time and names the variable."""
        env = {"HERMES_TELEGRAM__CHAT_ID": "chat-xyz"}
        with pytest.raises(TelegramNotifierError) as excinfo:
            TelegramConfig.from_env(env)
        assert str(excinfo.value) == (
            "Telegram notifier error: HERMES_TELEGRAM__BOT_TOKEN is not set (or empty)"
        )

    def test_from_env_raises_when_chat_id_missing(self) -> None:
        """A missing HERMES_TELEGRAM__CHAT_ID fails at load time and names the variable."""
        env = {"HERMES_TELEGRAM__BOT_TOKEN": "token-abc"}
        with pytest.raises(TelegramNotifierError) as excinfo:
            TelegramConfig.from_env(env)
        assert str(excinfo.value) == (
            "Telegram notifier error: HERMES_TELEGRAM__CHAT_ID is not set (or empty)"
        )

    def test_from_env_treats_whitespace_token_as_missing(self) -> None:
        """A whitespace-only token is missing (fail-fast, no silent success)."""
        env = {
            "HERMES_TELEGRAM__BOT_TOKEN": "   ",
            "HERMES_TELEGRAM__CHAT_ID": "chat-xyz",
        }
        with pytest.raises(TelegramNotifierError, match=r"BOT_TOKEN is not set \(or empty\)$"):
            TelegramConfig.from_env(env)

    def test_from_env_treats_whitespace_chat_id_as_missing(self) -> None:
        """A whitespace-only chat id is missing (fail-fast, no silent success)."""
        env = {
            "HERMES_TELEGRAM__BOT_TOKEN": "token-abc",
            "HERMES_TELEGRAM__CHAT_ID": " \t ",
        }
        with pytest.raises(TelegramNotifierError, match=r"CHAT_ID is not set \(or empty\)$"):
            TelegramConfig.from_env(env)

    def test_repr_hides_the_bot_token(self, adapter: TelegramAdapter) -> None:
        """Rendering the config or the adapter never prints the token (#506).

        Logs, tracebacks and error reports render objects with ``repr``.
        """
        rendered = repr(adapter.config) + repr(adapter)
        assert (_TEST_TOKEN in rendered, _TEST_CHAT in rendered) == (False, True)


class TestNotifierError:
    """The error type operators see in logs and the router catches."""

    def test_error_text_names_the_channel_and_the_cause(self) -> None:
        """str() of the error prefixes the Telegram channel to the wrapped message."""
        assert str(TelegramNotifierError("boom")) == "Telegram notifier error: boom"

    def test_wire_errors_are_caught_as_infra_errors(self, adapter: TelegramAdapter) -> None:
        """A failed send is catchable as InfraError, the layer the router converts to Result."""
        with respx.mock() as router:
            router.post(_API_URL).mock(return_value=httpx.Response(500, text="down"))
            with pytest.raises(InfraError, match="HTTP 500 from sendMessage: down"):
                adapter.notify_urgent(event="e", correlation_id="c", message="m")


class TestNotifyUrgentHappyPath:
    """Verify the request :meth:`notify_urgent` puts on the wire."""

    def test_notify_urgent_sends_one_json_post_to_the_bot_send_message_url(
        self, adapter: TelegramAdapter
    ) -> None:
        """notify_urgent is exactly one JSON POST to /bot<TOKEN>/sendMessage."""
        with respx.mock(assert_all_called=True) as router:
            route = router.post(_API_URL).mock(return_value=_ok())
            adapter.notify_urgent(
                event="rollback.fired",
                correlation_id=_TEST_CORRELATION_ID,
                message="Rollback executed on api node 3.",
            )
        (call,) = route.calls
        request = call.request
        assert (request.method, str(request.url), request.headers["content-type"]) == (
            "POST",
            _API_URL,
            "application/json",
        )

    def test_notify_urgent_payload_fields_besides_text(self, adapter: TelegramAdapter) -> None:
        """The payload is chat_id, MarkdownV2, no link preview and a loud push, nothing else."""
        payload = _sent_payload(
            adapter,
            event="health.check.fail",
            correlation_id=_TEST_CORRELATION_ID,
            message="Simple message",
        )
        payload.pop("text")
        assert payload == {
            "chat_id": _TEST_CHAT,
            "parse_mode": "MarkdownV2",
            "disable_web_page_preview": True,
            "disable_notification": False,
        }

    def test_notify_urgent_ends_with_the_correlation_tail(self, adapter: TelegramAdapter) -> None:
        """The last line of the push is the last 6 chars of the correlation ID (ADR-008)."""
        payload = _sent_payload(
            adapter, event="service.crash", correlation_id=_TEST_CORRELATION_ID, message="node down"
        )
        assert payload["text"].splitlines()[-1] == "…ABCDEF"

    def test_notify_urgent_escapes_markdown_v2_reserved_chars_in_the_message(
        self, adapter: TelegramAdapter
    ) -> None:
        """Reserved MarkdownV2 chars in the message are backslash-escaped for a literal render."""
        payload = _sent_payload(
            adapter,
            event="rebalance.cycle.decided",
            correlation_id=_TEST_CORRELATION_ID,
            message="pair=BTC_USDT (delta=+0.5%)",
        )
        assert payload["text"].splitlines()[1] == r"pair\=BTC\_USDT \(delta\=\+0\.5%\)"

    def test_notify_urgent_heads_the_push_with_the_escaped_event_name(
        self, adapter: TelegramAdapter
    ) -> None:
        """The headline names the event as the anomaly source, dots escaped for MarkdownV2."""
        payload = _sent_payload(
            adapter, event="rebalance.cycle.decided", correlation_id="c", message="m"
        )
        assert payload["text"].startswith("■ Anomaly · rebalance\\.cycle\\.decided · ")

    def test_notify_urgent_short_correlation_id_used_verbatim(
        self, adapter: TelegramAdapter
    ) -> None:
        """A correlation ID shorter than 6 chars is shown in full, not padded or dropped."""
        payload = _sent_payload(adapter, event="deploy.ok", correlation_id="abc", message="ok")
        assert payload["text"].splitlines()[-1] == "…abc"


class TestSendErrorPaths:
    """Every documented sendMessage failure raises with an operator-readable message."""

    def test_rate_limit_429_raises_with_status_and_body(self, adapter: TelegramAdapter) -> None:
        """A 429 that outlasts the retries raises; the message carries status and body."""
        body = '{"ok":false,"error_code":429,"description":"Too Many Requests: retry after 30"}'
        assert _urgent_error(adapter, httpx.Response(429, text=body)) == (
            f"Telegram notifier error: HTTP 429 from sendMessage: {body}"
        )

    def test_auth_failure_401_raises_with_status_and_body(self, adapter: TelegramAdapter) -> None:
        """HTTP 401 (bad bot token) raises with the status and body in the message."""
        assert _urgent_error(adapter, httpx.Response(401, text="Unauthorized")) == (
            "Telegram notifier error: HTTP 401 from sendMessage: Unauthorized"
        )

    def test_status_400_is_the_first_error_status(self, adapter: TelegramAdapter) -> None:
        """HTTP 400 is an error even when the body claims ok=true (threshold at 400)."""
        assert _urgent_error(adapter, httpx.Response(400, text='{"ok":true}')) == (
            'Telegram notifier error: HTTP 400 from sendMessage: {"ok":true}'
        )

    def test_error_body_excerpt_is_capped_at_200_chars(self, adapter: TelegramAdapter) -> None:
        """A huge error body is cut to its first 200 chars in the message."""
        body = "x" * 200 + "TAIL"
        assert _urgent_error(adapter, httpx.Response(502, text=body)) == (
            f"Telegram notifier error: HTTP 502 from sendMessage: {'x' * 200}"
        )

    def test_network_error_wraps_the_httpx_exception(self, adapter: TelegramAdapter) -> None:
        """A transport failure is re-raised as TelegramNotifierError chained to the cause."""
        cause = httpx.ConnectError("DNS failed")
        with respx.mock() as router:
            router.post(_API_URL).mock(side_effect=cause)
            with pytest.raises(TelegramNotifierError) as excinfo:
                adapter.notify_urgent(event="e", correlation_id="c", message="m")
        assert (str(excinfo.value), excinfo.value.__cause__) == (
            "Telegram notifier error: transport failure calling sendMessage: "
            "ConnectError('DNS failed')",
            cause,
        )

    def test_ok_false_on_http_200_raises(self, adapter: TelegramAdapter) -> None:
        """A 200 with ok=false still raises, quoting the parsed body (Bot API rejected us)."""
        response = httpx.Response(
            200, json={"ok": False, "error_code": 400, "description": "Bad Request: chat not found"}
        )
        assert _urgent_error(adapter, response) == (
            "Telegram notifier error: Bot API rejected sendMessage: "
            "{'ok': False, 'error_code': 400, 'description': 'Bad Request: chat not found'}"
        )

    def test_body_without_ok_field_raises(self, adapter: TelegramAdapter) -> None:
        """A 200 JSON body with no ok field counts as a rejection, not a success."""
        assert _urgent_error(adapter, httpx.Response(200, json={"result": {}})) == (
            "Telegram notifier error: Bot API rejected sendMessage: {'result': {}}"
        )

    def test_non_object_json_body_raises(self, adapter: TelegramAdapter) -> None:
        """A 200 JSON body that is not an object counts as a rejection."""
        assert _urgent_error(adapter, httpx.Response(200, json=[True])) == (
            "Telegram notifier error: Bot API rejected sendMessage: [True]"
        )

    def test_rejection_excerpt_is_capped_at_200_chars(self, adapter: TelegramAdapter) -> None:
        """A huge ok=false body is quoted only up to its first 200 chars."""
        response = httpx.Response(200, json={"ok": False, "description": "d" * 300})
        quoted = "{'ok': False, 'description': '" + "d" * 300
        assert _urgent_error(adapter, response) == (
            f"Telegram notifier error: Bot API rejected sendMessage: {quoted[:200]}"
        )

    def test_non_json_response_raises(self, adapter: TelegramAdapter) -> None:
        """A 2xx response that is not JSON raises TelegramNotifierError, not ValueError."""
        response = httpx.Response(
            200, content=b"<html>gateway proxy error</html>", headers={"content-type": "text/html"}
        )
        assert _urgent_error(adapter, response) == (
            "Telegram notifier error: non-JSON response from sendMessage: "
            "<html>gateway proxy error</html>"
        )

    def test_non_json_excerpt_is_capped_at_200_chars(self, adapter: TelegramAdapter) -> None:
        """A huge non-JSON body is cut to its first 200 chars in the message."""
        response = httpx.Response(200, text="<" * 200 + "TAIL")
        assert _urgent_error(adapter, response) == (
            f"Telegram notifier error: non-JSON response from sendMessage: {'<' * 200}"
        )


class TestRateLimitBackOff:
    """A 429 is waited out a bounded number of times, never longer than the cap (#859 item 5)."""

    @staticmethod
    def _send(adapter: TelegramAdapter, *responses: httpx.Response) -> tuple[int, str]:
        """Send one push against ``responses`` in order; return the call count and any error."""
        with respx.mock() as router:
            route = router.post(_API_URL).mock(side_effect=list(responses))
            try:
                adapter.notify(_anomaly())
            except TelegramNotifierError as exc:
                return route.call_count, str(exc)
        return route.call_count, ""

    def test_one_429_then_ok_delivers_the_message_once(self, config: TelegramConfig) -> None:
        """Invariant: a single 429 is waited out and the push goes through on the retry."""
        fake = _FakeTime()
        calls, error = self._send(_timed_adapter(config, fake), _too_many(2), _ok())
        assert (calls, error, fake.sleeps) == (2, "", [2.0])

    def test_retry_after_in_the_body_is_the_wait(self, config: TelegramConfig) -> None:
        """Invariant: the Bot API's ``parameters.retry_after`` sets the back-off, not a default."""
        fake = _FakeTime()
        self._send(_timed_adapter(config, fake), _too_many(7, **{"Retry-After": "3"}), _ok())
        assert fake.sleeps == [7.0]

    def test_retry_after_header_is_the_wait_when_the_body_has_none(
        self, config: TelegramConfig
    ) -> None:
        """Invariant: without ``parameters.retry_after`` the ``Retry-After`` header is honoured."""
        fake = _FakeTime()
        self._send(_timed_adapter(config, fake), _too_many(**{"Retry-After": "3"}), _ok())
        assert fake.sleeps == [3.0]

    @pytest.mark.parametrize(
        "response",
        [
            httpx.Response(429, text="Too Many Requests"),
            _too_many(-5),
            httpx.Response(429, json={"ok": False, "parameters": {"retry_after": True}}),
            _too_many(**{"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}),
        ],
        ids=["plain-text-body", "negative", "boolean", "http-date-header"],
    )
    def test_an_unreadable_wait_falls_back_to_one_second(
        self, config: TelegramConfig, response: httpx.Response
    ) -> None:
        """Invariant: a 429 without a usable wait is retried after 1 s, never at once or never."""
        fake = _FakeTime()
        self._send(_timed_adapter(config, fake), response, _ok())
        assert fake.sleeps == [1.0]

    def test_repeated_429s_give_up_after_three_retries(self, config: TelegramConfig) -> None:
        """Invariant: at most 3 retries, then the same InfraError as any other HTTP failure."""
        fake = _FakeTime()
        calls, error = self._send(_timed_adapter(config, fake), *[_too_many(5)] * 6)
        assert (calls, fake.sleeps, error.startswith("Telegram notifier error: HTTP 429")) == (
            4,
            [5.0, 5.0, 5.0],
            True,
        )

    def test_waits_never_add_up_past_the_cap(self, config: TelegramConfig) -> None:
        """Invariant: a second 20 s wait would pass the 30 s cap, so the adapter gives up."""
        fake = _FakeTime()
        calls, error = self._send(_timed_adapter(config, fake), *[_too_many(20)] * 4)
        assert (calls, fake.sleeps, bool(error)) == (2, [20.0], True)

    def test_a_wait_longer_than_the_cap_is_not_slept(self, config: TelegramConfig) -> None:
        """Invariant: a retry_after beyond the cap fails at once instead of stalling serve."""
        fake = _FakeTime()
        calls, error = self._send(_timed_adapter(config, fake), _too_many(60), _ok())
        assert (calls, fake.sleeps, bool(error)) == (1, [], True)

    def test_slow_attempts_count_towards_the_cap(self, config: TelegramConfig) -> None:
        """Invariant: request time plus waits stay within 30 s even when every call is slow."""
        fake = _FakeTime()

        def slow_429(request: httpx.Request) -> httpx.Response:
            fake.now += request.extensions["timeout"]["read"]
            return _too_many(1)

        adapter = _timed_adapter(config, fake)
        with respx.mock() as router:
            router.post(_API_URL).mock(side_effect=slow_429)
            with pytest.raises(TelegramNotifierError, match="HTTP 429"):
                adapter.notify(_anomaly())
        assert fake.now <= 30.0

    def test_a_429_from_the_production_factory_is_retried(self, config: TelegramConfig) -> None:
        """Invariant: :meth:`TelegramAdapter.create` wires the back-off, not only the tests."""
        adapter = TelegramAdapter.create(config)
        try:
            calls, error = self._send(adapter, _too_many(0), _ok())
        finally:
            adapter.close()
        assert (calls, error) == (2, "")


class TestNotifyReportNoOp:
    """Verify :meth:`notify_report` is a no-op with a warning log per decisions journal."""

    def test_notify_report_only_logs_one_warning_with_the_correlation_tail(
        self, adapter: TelegramAdapter
    ) -> None:
        """notify_report sends nothing and logs one warning naming the event and the ID tail."""
        # No route is registered: any HTTP call would raise inside respx.mock.
        with respx.mock(assert_all_mocked=True), capture_logs() as logs:
            adapter.notify_report(
                event="monthly.tax.report",
                correlation_id=_TEST_CORRELATION_ID,
                title="Q1",
                body="body",
                attachments=None,
            )
        assert logs == [
            {
                "event": "telegram.notify_report.noop",
                "log_level": "warning",
                "event_name": "monthly.tax.report",
                "correlation_tail": "ABCDEF",
                "reason": "telegram channel is urgent-only per decisions journal",
            }
        ]


class TestCreateAndClose:
    """Verify the :meth:`TelegramAdapter.create` factory and :meth:`close` lifecycle."""

    def test_create_targets_the_bot_api_with_the_given_timeout(
        self, config: TelegramConfig
    ) -> None:
        """create() builds a client on api.telegram.org bounded by the requested timeout."""
        adapter = TelegramAdapter.create(config, timeout_sec=7.5)
        try:
            assert (str(adapter.client.base_url), adapter.client.timeout) == (
                "https://api.telegram.org",
                httpx.Timeout(7.5),
            )
        finally:
            adapter.close()

    def test_create_defaults_to_a_ten_second_timeout(self, config: TelegramConfig) -> None:
        """Without a timeout, create() bounds every request at 10 s (module design notes)."""
        adapter = TelegramAdapter.create(config)
        try:
            assert adapter.client.timeout == httpx.Timeout(10.0)
        finally:
            adapter.close()

    def test_close_releases_the_client_and_tolerates_a_second_call(
        self, config: TelegramConfig
    ) -> None:
        """close() closes the HTTP client, and calling it again does not raise."""
        adapter = TelegramAdapter.create(config)
        adapter.close()
        adapter.close()
        assert adapter.client.is_closed is True


class TestNotifyEvent:
    """Verify :meth:`notify` (event-based) sends the rendered template."""

    def test_notify_posts_the_rendered_anomaly(self, adapter: TelegramAdapter) -> None:
        """notify(event) sends the full payload with the anomaly rendered in MarkdownV2."""
        with respx.mock() as router:
            route = router.post(_API_URL).mock(return_value=_ok())
            adapter.notify(_anomaly())
        (call,) = route.calls
        assert json.loads(call.request.content) == {
            "chat_id": _TEST_CHAT,
            "text": "■ Anomaly · swing\\.risk · Sun 16:22\n30% drawdown breached\n…ABCDEF",
            "parse_mode": "MarkdownV2",
            "disable_web_page_preview": True,
            "disable_notification": False,
        }

    def test_notify_dispatches_on_event_type(self, adapter: TelegramAdapter) -> None:
        """A BalanceLow event is rendered by its own template, not the anomaly one."""
        usdt = Currency.USDT
        event = BalanceLow(
            occurred_at=_AT,
            correlation_id=_TEST_CORRELATION_ID,
            asset=usdt,
            current=Money(amount=Decimal(50), currency=usdt),
            threshold=Money(amount=Decimal(100), currency=usdt),
            checked_at=_AT,
        )
        with respx.mock() as router:
            route = router.post(_API_URL).mock(return_value=_ok())
            adapter.notify(event)
        assert json.loads(route.calls[0].request.content)["text"] == (
            "■ Balance low · USDT · Sun 16:22\n"
            "50\\.00 USDT left, threshold 100\\.00 USDT\ntop up · …ABCDEF"
        )

    def test_non_critical_event_is_sent_silently(self, adapter: TelegramAdapter) -> None:
        """A WARNING anomaly is sent silent: disable_notification is true."""
        with respx.mock() as router:
            route = router.post(_API_URL).mock(return_value=_ok())
            adapter.notify(_anomaly(AnomalySeverity.WARNING))
        assert json.loads(route.calls[0].request.content)["disable_notification"] is True

    def test_notify_wire_failure_raises_notifier_error(self, adapter: TelegramAdapter) -> None:
        """A network error inside notify() surfaces as TelegramNotifierError."""
        with respx.mock() as router:
            router.post(_API_URL).mock(side_effect=httpx.ConnectError("DNS failed"))
            with pytest.raises(
                TelegramNotifierError, match=r"transport failure calling sendMessage"
            ):
                adapter.notify(_anomaly())


class TestGetMe:
    """Verify :meth:`get_me` — the read-only health probe used by `hermes health`."""

    def test_get_me_returns_the_result_object(self, adapter: TelegramAdapter) -> None:
        """get_me GETs /bot<TOKEN>/getMe and returns the 'result' object unchanged."""
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
        assert result == {
            "id": 42,
            "is_bot": True,
            "username": "hermes_bot",
            "first_name": "Hermes",
        }

    def test_get_me_401_raises_with_status_and_body(self, adapter: TelegramAdapter) -> None:
        """HTTP 401 from getMe raises with the status and body in the message."""
        assert _get_me_error(adapter, httpx.Response(401, text="Unauthorized")) == (
            "Telegram notifier error: HTTP 401 from getMe: Unauthorized"
        )

    def test_get_me_status_400_is_an_error_with_a_capped_excerpt(
        self, adapter: TelegramAdapter
    ) -> None:
        """HTTP 400 is the first error status for getMe; the body is cut to 200 chars."""
        assert _get_me_error(adapter, httpx.Response(400, text="y" * 200 + "TAIL")) == (
            f"Telegram notifier error: HTTP 400 from getMe: {'y' * 200}"
        )

    def test_get_me_status_399_with_result_is_accepted(self, adapter: TelegramAdapter) -> None:
        """A status just below 400 with ok=true returns the result (threshold is >= 400)."""
        with respx.mock() as router:
            router.get(_GET_ME_URL).mock(
                return_value=httpx.Response(399, json={"ok": True, "result": {"id": 7}})
            )
            assert adapter.get_me() == {"id": 7}

    def test_get_me_ok_false_raises(self, adapter: TelegramAdapter) -> None:
        """A 200 with ok=false still raises, quoting the parsed body."""
        response = httpx.Response(200, json={"ok": False, "description": "Bad Request"})
        assert _get_me_error(adapter, response) == (
            "Telegram notifier error: Bot API rejected getMe: "
            "{'ok': False, 'description': 'Bad Request'}"
        )

    def test_get_me_body_without_ok_raises(self, adapter: TelegramAdapter) -> None:
        """A getMe body with a result but no ok field counts as a rejection."""
        assert _get_me_error(adapter, httpx.Response(200, json={"result": {"id": 1}})) == (
            "Telegram notifier error: Bot API rejected getMe: {'result': {'id': 1}}"
        )

    def test_get_me_non_object_body_raises(self, adapter: TelegramAdapter) -> None:
        """A getMe body that is a JSON list counts as a rejection."""
        assert _get_me_error(adapter, httpx.Response(200, json=[1])) == (
            "Telegram notifier error: Bot API rejected getMe: [1]"
        )

    def test_get_me_rejection_excerpt_is_capped_at_200_chars(
        self, adapter: TelegramAdapter
    ) -> None:
        """A huge ok=false getMe body is quoted only up to its first 200 chars."""
        response = httpx.Response(200, json={"ok": False, "description": "d" * 300})
        quoted = "{'ok': False, 'description': '" + "d" * 300
        assert _get_me_error(adapter, response) == (
            f"Telegram notifier error: Bot API rejected getMe: {quoted[:200]}"
        )

    def test_get_me_transport_error_wraps_the_httpx_exception(
        self, adapter: TelegramAdapter
    ) -> None:
        """A transport failure during getMe is re-raised chained to the cause."""
        cause = httpx.ConnectError("DNS failed")
        with respx.mock() as router:
            router.get(_GET_ME_URL).mock(side_effect=cause)
            with pytest.raises(TelegramNotifierError) as excinfo:
                adapter.get_me()
        assert (str(excinfo.value), excinfo.value.__cause__) == (
            "Telegram notifier error: transport failure calling getMe: ConnectError('DNS failed')",
            cause,
        )

    def test_get_me_non_json_response_raises(self, adapter: TelegramAdapter) -> None:
        """A 2xx getMe response that is not JSON raises with a capped body excerpt."""
        assert _get_me_error(adapter, httpx.Response(200, text="<" * 200 + "TAIL")) == (
            f"Telegram notifier error: non-JSON response from getMe: {'<' * 200}"
        )

    def test_get_me_missing_result_object_raises(self, adapter: TelegramAdapter) -> None:
        """A 200 ok=true response without a 'result' object raises, quoting the body."""
        assert _get_me_error(adapter, httpx.Response(200, json={"ok": True})) == (
            "Telegram notifier error: getMe response missing 'result' object: {'ok': True}"
        )

    def test_get_me_non_object_result_raises(self, adapter: TelegramAdapter) -> None:
        """A 'result' that is not an object is as unusable as a missing one."""
        response = httpx.Response(200, json={"ok": True, "result": "bot", "pad": "p" * 300})
        quoted = "{'ok': True, 'result': 'bot', 'pad': '" + "p" * 300
        assert _get_me_error(adapter, response) == (
            f"Telegram notifier error: getMe response missing 'result' object: {quoted[:200]}"
        )


class TestEurRate:
    """The injected rate lookup decides whether a push shows EUR or USDT (change 668)."""

    @staticmethod
    def _sent_headline(adapter: TelegramAdapter) -> str:
        """Notify the sample signal entry and return the first line of the sent text."""
        with respx.mock() as router:
            route = router.post(_API_URL).mock(return_value=_ok())
            adapter.notify(samples()["signal-entry"])
        return str(json.loads(route.calls[0].request.content)["text"]).splitlines()[0]

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

        headline = self._sent_headline(adapter)

        assert (seen, headline) == (
            [samples()["signal-entry"]],
            "▲ Entry long BTC · €41,928\\.16",
        )

    def test_a_lookup_with_no_rate_sends_the_usdt_text(self, config: TelegramConfig) -> None:
        """Invariant (668 R-2): a cold cache keeps the USDT text, visibly and never wrong."""
        client = httpx.Client(base_url="https://api.telegram.org", timeout=5.0)
        adapter = TelegramAdapter(config=config, client=client, eur_rate=lambda _event: None)
        assert self._sent_headline(adapter) == "▲ Entry long BTC · 52,410\\.20 USDT"

    def test_a_failing_lookup_still_delivers_the_push_in_usdt(self, config: TelegramConfig) -> None:
        """Invariant (668 R-2): a lookup that raises never stops the notification."""

        def boom(_event: DomainEvent) -> Decimal | None:
            raise RuntimeError("cache exploded")

        client = httpx.Client(base_url="https://api.telegram.org", timeout=5.0)
        adapter = TelegramAdapter(config=config, client=client, eur_rate=boom)
        assert self._sent_headline(adapter) == "▲ Entry long BTC · 52,410\\.20 USDT"

    def test_create_wires_the_lookup_into_the_sent_push(self, config: TelegramConfig) -> None:
        """Invariant (668 D-2): an adapter built by the factory prices pushes with the lookup."""
        adapter = TelegramAdapter.create(config, eur_rate=lambda _event: Decimal("1.25"))
        try:
            assert self._sent_headline(adapter) == "▲ Entry long BTC · €41,928\\.16"
        finally:
            adapter.close()
