"""Contract tests for :class:`hermes.adapters.notifier.SlackAdapter`.

Every test protects one invariant of the Slack adapter contract. Wire
calls are mocked via ``respx`` (declared in dev deps per ADR-007); every
request the adapter makes is pinned by URL, headers and body, and every
failure by its exact exception message. No real socket ever opens; no real
credentials are ever transmitted.

Coverage focus areas:

- Config loading via :meth:`SlackConfig.from_env` (missing / whitespace).
- Request shape of ``chat.postMessage``, ``auth.test`` and the three-step
  v2 file upload (form body, signed PUT, completion JSON, threading).
- Slack HTTP-200 with ``ok=false`` in body — the "raise_for_status is not
  enough" footgun — plus HTTP status, transport and non-JSON failures,
  each with its exact operator-facing message.
- The reporting-phase blind on the attachment upload (ADR-012 ruling 1).

The mocked wire is the Slack Web API seam; its real counterpart is
``tests/contract/test_slack_auth_test.py::test_auth_test_against_real_slack_api``.

Reference: docs/decisions/2026-08-23-slack-alongside-telegram.md;
    docs/guides/ai-authored-tests.md §"The unit-test standard".
"""

from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from structlog.testing import capture_logs

from hermes.adapters.notifier import (
    SlackAdapter,
    SlackConfig,
    SlackNotifierError,
)
from hermes.application.messages.reporting_phase import ReportingPhase
from hermes.domain.errors import InfraError
from hermes.domain.events.anomaly_detected import AnomalySeverity
from hermes.domain.events.base import DomainEvent
from hermes.domain.events.daily_trade_digest import DailyTradeDigest, TradeSummary
from hermes.domain.events.strategy_paused import StrategyPaused
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.pair import Pair
from tests.support.notification_samples import samples

_TEST_TOKEN = "xoxb-test"  # noqa: S105 - test-only bot token, never real  # gitleaks:allow
_TEST_CHANNEL = "C0123ABCD"
_TEST_CORRELATION_ID = "01HKQMZXYZ0123456789ABCDEF"  # 26-char pseudo-ULID

_POST_MESSAGE_URL = "https://slack.com/api/chat.postMessage"
_GET_UPLOAD_URL = "https://slack.com/api/files.getUploadURLExternal"
_COMPLETE_UPLOAD_URL = "https://slack.com/api/files.completeUploadExternal"
_AUTH_TEST_URL = "https://slack.com/api/auth.test"
_SIGNED_URL = "https://files.slack.com/upload/signed-abc123"
_PARENT_TS = "1700000000.000100"
# The CSV the digest template attaches for _digest_event(), written out by hand:
# one header row, then the winner and the loser, CRLF line ends.
_DIGEST_CSV = (
    b"bucket,pair,pnl_amount,pnl_currency\r\n"
    b"winner,BTC/USDT,120.00,EUR\r\n"
    b"loser,ETH/USDT,-45.50,EUR\r\n"
)


@pytest.fixture
def config() -> SlackConfig:
    """Return a fixed :class:`SlackConfig` for adapter construction."""
    return SlackConfig(bot_token=_TEST_TOKEN, default_channel=_TEST_CHANNEL)


@pytest.fixture
def adapter(config: SlackConfig) -> SlackAdapter:
    """Return a :class:`SlackAdapter` wired with a plain httpx.Client.

    ``respx`` patches transport globally in the ``respx.mock`` context
    manager per test, so the client here does NOT need MockTransport.
    """
    client = httpx.Client(base_url="https://slack.com", timeout=5.0)
    return SlackAdapter(config=config, client=client)


@pytest.fixture
def unblinded_adapter(config: SlackConfig) -> SlackAdapter:
    """Return a :class:`SlackAdapter` built in the CERTIFIED reporting phase.

    Only the attachment-upload tests need it: before certification no
    template produces an attachment, so the upload flow is reachable
    only after the explicit unblinding step.
    """
    client = httpx.Client(base_url="https://slack.com", timeout=5.0)
    return SlackAdapter(config=config, client=client, reporting_phase=ReportingPhase.CERTIFIED)


def _ok(**fields: object) -> httpx.Response:
    """Return a fresh ``ok=true`` Slack response carrying ``fields``."""
    return httpx.Response(200, json={"ok": True, **fields})


def _json(route: respx.Route, index: int = 0) -> Any:
    """Return the JSON body of the ``index``-th request the route received."""
    return json.loads(route.calls[index].request.content)


def _urgent_error(adapter: SlackAdapter, response: httpx.Response) -> str:
    """Return the message of the error ``notify_urgent`` raises for ``response``."""
    with respx.mock() as router:
        router.post(_POST_MESSAGE_URL).mock(return_value=response)
        with pytest.raises(SlackNotifierError) as excinfo:
            adapter.notify_urgent(event="deploy.done", correlation_id="c", message="m")
    return str(excinfo.value)


def _strategy_paused_event() -> StrategyPaused:
    """Return a StrategyPaused event at 14:22 UTC on Sunday 2026-08-23 (16:22 Madrid)."""
    return StrategyPaused(
        occurred_at=datetime(2026, 8, 23, 14, 22, 0, tzinfo=UTC),
        correlation_id=_TEST_CORRELATION_ID,
        strategy_id="swing-btc",
        severity=AnomalySeverity.CRITICAL,
        reason="drawdown breach — 6.2% over daily cap",
        paused_at=datetime(2026, 8, 23, 14, 22, 0, tzinfo=UTC),
    )


def _digest_event() -> DailyTradeDigest:
    """Return a DailyTradeDigest with one winner and one loser row."""
    winner = TradeSummary(
        pair=Pair(base=Currency.BTC, quote=Currency.USDT),
        pnl=Money(amount=Decimal("120.00"), currency=Currency.EUR),
    )
    loser = TradeSummary(
        pair=Pair(base=Currency.ETH, quote=Currency.USDT),
        pnl=Money(amount=Decimal("-45.50"), currency=Currency.EUR),
    )
    return DailyTradeDigest(
        occurred_at=datetime(2026, 8, 23, 22, 0, 0, tzinfo=UTC),
        correlation_id=_TEST_CORRELATION_ID,
        day=date(2026, 8, 23),
        total_trades=12,
        filled=10,
        rejected=2,
        total_notional=Money(amount=Decimal("12500.50"), currency=Currency.EUR),
        top_winners=(winner,),
        top_losers=(loser,),
    )


def _mock_upload(router: respx.Router, *, parent: httpx.Response | None = None) -> dict[str, Any]:
    """Mock a successful parent post and three-step upload; return the routes by name."""
    return {
        "post": router.post(_POST_MESSAGE_URL).mock(
            return_value=parent if parent is not None else _ok(ts=_PARENT_TS)
        ),
        "step1": router.post(_GET_UPLOAD_URL).mock(
            return_value=_ok(upload_url=_SIGNED_URL, file_id="F999")
        ),
        "step2": router.put(_SIGNED_URL).mock(return_value=httpx.Response(200, text="OK")),
        "step3": router.post(_COMPLETE_UPLOAD_URL).mock(return_value=_ok()),
    }


def _report(adapter: SlackAdapter, attachments: list[Path] | None) -> None:
    """Send a fixed notify_report with ``attachments``."""
    adapter.notify_report(
        event="monthly.tax.report",
        correlation_id=_TEST_CORRELATION_ID,
        title="Q1",
        body="see attached",
        attachments=attachments,
    )


def _report_error(adapter: SlackAdapter, tmp_path: Path, **routes: httpx.Response) -> str:
    """Return the message of the error a one-attachment report raises.

    ``routes`` overrides the upload steps: ``step1`` for getUploadURLExternal,
    ``step2`` for the signed PUT, ``step3`` for completeUploadExternal.
    """
    csv_path = tmp_path / "r.csv"
    csv_path.write_bytes(b"x")
    with respx.mock(assert_all_called=False) as router:
        router.post(_POST_MESSAGE_URL).mock(return_value=_ok(ts=_PARENT_TS))
        router.post(_GET_UPLOAD_URL).mock(
            return_value=routes.get("step1", _ok(upload_url=_SIGNED_URL, file_id="F2"))
        )
        router.put(_SIGNED_URL).mock(return_value=routes.get("step2", httpx.Response(200)))
        router.post(_COMPLETE_UPLOAD_URL).mock(return_value=routes.get("step3", _ok()))
        with pytest.raises(SlackNotifierError) as excinfo:
            _report(adapter, [csv_path])
    return str(excinfo.value)


class TestReportingPhaseLogging:
    """Building the adapter unblinded is the explicit, logged step of ADR-012 ruling 1."""

    def test_certified_adapter_logs_one_unblinding_warning(self, config: SlackConfig) -> None:
        """A CERTIFIED adapter logs exactly one warning naming the certified phase."""
        client = httpx.Client(base_url="https://slack.com", timeout=5.0)
        with capture_logs() as logs:
            SlackAdapter(config=config, client=client, reporting_phase=ReportingPhase.CERTIFIED)
        assert logs == [
            {
                "event": "slack_notifier_unblinded",
                "log_level": "warning",
                "reporting_phase": "certified",
            }
        ]

    def test_default_adapter_logs_nothing(self, config: SlackConfig) -> None:
        """The default adapter is blind and builds silently (pair of the certified test)."""
        client = httpx.Client(base_url="https://slack.com", timeout=5.0)
        with capture_logs() as logs:
            SlackAdapter(config=config, client=client)
        assert logs == []


class TestSlackConfig:
    """Verify credential-loading contract for :class:`SlackConfig`."""

    def test_repr_hides_the_bot_token(self, adapter: SlackAdapter) -> None:
        """Rendering the config or the adapter never prints the token (#506).

        Logs, tracebacks and error reports render objects with ``repr``.
        """
        rendered = repr(adapter.config) + repr(adapter)
        assert (_TEST_TOKEN in rendered, _TEST_CHANNEL in rendered) == (False, True)

    def test_from_env_loads_both_variables_stripped(self) -> None:
        """from_env takes the two HERMES_SLACK__* values with surrounding blanks removed."""
        env = {
            "HERMES_SLACK__BOT_TOKEN": " xoxb-token-abc\n",
            "HERMES_SLACK__DEFAULT_CHANNEL": "  #general ",
        }
        cfg = SlackConfig.from_env(env)
        assert (cfg.bot_token, cfg.default_channel) == ("xoxb-token-abc", "#general")

    def test_from_env_raises_when_bot_token_missing(self) -> None:
        """A missing HERMES_SLACK__BOT_TOKEN fails at load time and names the variable."""
        with pytest.raises(SlackNotifierError) as excinfo:
            SlackConfig.from_env({"HERMES_SLACK__DEFAULT_CHANNEL": "#general"})
        assert str(excinfo.value) == (
            "Slack notifier error: HERMES_SLACK__BOT_TOKEN is not set (or empty)"
        )

    def test_from_env_raises_when_default_channel_missing(self) -> None:
        """A missing HERMES_SLACK__DEFAULT_CHANNEL fails at load time and names the variable."""
        with pytest.raises(SlackNotifierError) as excinfo:
            SlackConfig.from_env({"HERMES_SLACK__BOT_TOKEN": "xoxb-token-abc"})
        assert str(excinfo.value) == (
            "Slack notifier error: HERMES_SLACK__DEFAULT_CHANNEL is not set (or empty)"
        )

    def test_from_env_treats_whitespace_token_as_missing(self) -> None:
        """A whitespace-only token is missing (fail-fast, no silent success)."""
        env = {"HERMES_SLACK__BOT_TOKEN": "   ", "HERMES_SLACK__DEFAULT_CHANNEL": "#general"}
        with pytest.raises(SlackNotifierError, match=r"BOT_TOKEN is not set \(or empty\)$"):
            SlackConfig.from_env(env)

    def test_from_env_treats_whitespace_channel_as_missing(self) -> None:
        """A whitespace-only channel is missing (fail-fast, no silent success)."""
        env = {"HERMES_SLACK__BOT_TOKEN": "xoxb-a", "HERMES_SLACK__DEFAULT_CHANNEL": " \t"}
        with pytest.raises(SlackNotifierError, match=r"DEFAULT_CHANNEL is not set \(or empty\)$"):
            SlackConfig.from_env(env)


class TestNotifierError:
    """The error type operators see in logs and the router catches."""

    def test_error_text_names_the_channel_and_the_cause(self) -> None:
        """str() of the error prefixes the Slack channel to the wrapped message."""
        assert str(SlackNotifierError("boom")) == "Slack notifier error: boom"

    def test_wire_errors_are_caught_as_infra_errors(self, adapter: SlackAdapter) -> None:
        """A failed post is catchable as InfraError, the layer the router converts to Result."""
        with respx.mock() as router:
            router.post(_POST_MESSAGE_URL).mock(return_value=httpx.Response(500, text="down"))
            with pytest.raises(InfraError, match=r"HTTP 500 from /api/chat\.postMessage: down"):
                adapter.notify_urgent(event="e", correlation_id="c", message="m")


class TestNotifyUrgentHappyPath:
    """Verify the request :meth:`notify_urgent` puts on the wire."""

    def test_notify_urgent_is_one_authorized_json_post(self, adapter: SlackAdapter) -> None:
        """notify_urgent is one JSON POST to chat.postMessage with the Bearer bot token."""
        with respx.mock(assert_all_called=True) as router:
            route = router.post(_POST_MESSAGE_URL).mock(return_value=_ok())
            adapter.notify_urgent(event="rollback.fired", correlation_id="c", message="m")
        (call,) = route.calls
        headers = call.request.headers
        assert (str(call.request.url), headers["authorization"], headers["content-type"]) == (
            _POST_MESSAGE_URL,
            f"Bearer {_TEST_TOKEN}",
            "application/json",
        )

    def test_notify_urgent_text_names_the_event_and_the_message(
        self, adapter: SlackAdapter
    ) -> None:
        """The fallback text is the anomaly headline for the event, ending with the message."""
        with respx.mock() as router:
            route = router.post(_POST_MESSAGE_URL).mock(return_value=_ok())
            adapter.notify_urgent(
                event="health.check.fail", correlation_id="c", message="db unreachable"
            )
        # notify_urgent stamps the wall clock into the headline, so mask the time.
        text = re.sub(r"\w{3} \d\d:\d\d", "<time>", _json(route)["text"])
        assert text == "\u25a0 Anomaly \u00b7 health.check.fail \u00b7 <time> \u00b7 db unreachable"

    def test_notify_urgent_footer_is_the_correlation_tail(self, adapter: SlackAdapter) -> None:
        """The Block Kit context footer is the last 6 chars of the correlation ID (ADR-008)."""
        with respx.mock() as router:
            route = router.post(_POST_MESSAGE_URL).mock(return_value=_ok())
            adapter.notify_urgent(
                event="service.crash", correlation_id=_TEST_CORRELATION_ID, message="node down"
            )
        assert _json(route)["blocks"][-1] == {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": "\u2026ABCDEF"}],
        }

    def test_notify_urgent_short_correlation_id_used_verbatim(self, adapter: SlackAdapter) -> None:
        """A correlation ID shorter than 6 chars is the whole footer, not padded or dropped."""
        with respx.mock() as router:
            route = router.post(_POST_MESSAGE_URL).mock(return_value=_ok())
            adapter.notify_urgent(event="deploy.ok", correlation_id="abc", message="ok")
        assert _json(route)["blocks"][-1]["elements"] == [{"type": "mrkdwn", "text": "\u2026abc"}]


class TestResponseValidation:
    """Every Web API failure raises with an operator-readable message."""

    def test_ok_false_body_on_http_200_raises_with_the_slack_error(
        self, adapter: SlackAdapter
    ) -> None:
        """HTTP 200 with ``ok=false`` raises and quotes the body — Slack's HTTP-200 footgun.

        Slack routinely returns 200 for auth errors, missing scopes, and
        channel-not-found. A naive raise_for_status() would swallow these.
        """
        response = httpx.Response(200, json={"ok": False, "error": "channel_not_found"})
        assert _urgent_error(adapter, response) == (
            "Slack notifier error: Slack API rejected /api/chat.postMessage: "
            "{'ok': False, 'error': 'channel_not_found'}"
        )

    def test_body_without_ok_field_raises(self, adapter: SlackAdapter) -> None:
        """A 200 JSON body with no ok field counts as a rejection, not a success."""
        assert _urgent_error(adapter, httpx.Response(200, json={"ts": "1.0"})) == (
            "Slack notifier error: Slack API rejected /api/chat.postMessage: {'ts': '1.0'}"
        )

    def test_non_object_json_body_raises(self, adapter: SlackAdapter) -> None:
        """A 200 JSON body that is not an object counts as a rejection."""
        assert _urgent_error(adapter, httpx.Response(200, json=[True])) == (
            "Slack notifier error: Slack API rejected /api/chat.postMessage: [True]"
        )

    def test_rejection_excerpt_is_capped_at_200_chars(self, adapter: SlackAdapter) -> None:
        """A huge ok=false body is quoted only up to its first 200 chars."""
        response = httpx.Response(200, json={"ok": False, "error": "e" * 300})
        quoted = "{'ok': False, 'error': '" + "e" * 300
        assert _urgent_error(adapter, response) == (
            f"Slack notifier error: Slack API rejected /api/chat.postMessage: {quoted[:200]}"
        )

    def test_auth_failure_401_raises_with_status_and_body(self, adapter: SlackAdapter) -> None:
        """HTTP 401 (bad bot token) raises with the status and the body."""
        assert _urgent_error(adapter, httpx.Response(401, text="Unauthorized")) == (
            "Slack notifier error: HTTP 401 from /api/chat.postMessage: Unauthorized"
        )

    def test_rate_limit_429_raises_instead_of_retrying(self, adapter: SlackAdapter) -> None:
        """HTTP 429 raises so the router sees the pressure; adaptive back-off is not ours."""
        response = httpx.Response(429, headers={"Retry-After": "30"}, text="rate limited")
        assert _urgent_error(adapter, response) == (
            "Slack notifier error: HTTP 429 from /api/chat.postMessage: rate limited"
        )

    def test_status_400_is_an_error_even_with_an_ok_body(self, adapter: SlackAdapter) -> None:
        """HTTP 400 is the first error status, whatever the body says (threshold at 400)."""
        assert _urgent_error(adapter, httpx.Response(400, text='{"ok":true}')) == (
            'Slack notifier error: HTTP 400 from /api/chat.postMessage: {"ok":true}'
        )

    def test_status_399_with_ok_body_is_accepted(self, adapter: SlackAdapter) -> None:
        """A status just below 400 with ok=true is a success (threshold is >= 400)."""
        with respx.mock() as router:
            router.post(_AUTH_TEST_URL).mock(
                return_value=httpx.Response(399, json={"ok": True, "team": "t"})
            )
            assert adapter.auth_test() == {"ok": True, "team": "t"}

    def test_http_error_excerpt_is_capped_at_200_chars(self, adapter: SlackAdapter) -> None:
        """A huge error body is cut to its first 200 chars in the message."""
        assert _urgent_error(adapter, httpx.Response(502, text="x" * 200 + "TAIL")) == (
            f"Slack notifier error: HTTP 502 from /api/chat.postMessage: {'x' * 200}"
        )

    def test_network_error_wraps_the_httpx_exception(self, adapter: SlackAdapter) -> None:
        """A transport failure is re-raised as SlackNotifierError chained to the cause."""
        cause = httpx.ConnectError("DNS failed")
        with respx.mock() as router:
            router.post(_POST_MESSAGE_URL).mock(side_effect=cause)
            with pytest.raises(SlackNotifierError) as excinfo:
                adapter.notify_urgent(event="e", correlation_id="c", message="m")
        assert (str(excinfo.value), excinfo.value.__cause__) == (
            "Slack notifier error: transport failure calling /api/chat.postMessage: "
            "ConnectError('DNS failed')",
            cause,
        )

    def test_non_json_response_raises_with_a_capped_excerpt(self, adapter: SlackAdapter) -> None:
        """A 2xx response that is not JSON raises SlackNotifierError, body cut to 200 chars."""
        response = httpx.Response(
            200, content=b"<" * 200 + b"TAIL", headers={"content-type": "text/html"}
        )
        assert _urgent_error(adapter, response) == (
            f"Slack notifier error: non-JSON response from /api/chat.postMessage: {'<' * 200}"
        )


class TestNotifyReport:
    """Verify :meth:`notify_report` posts Block Kit and uploads attachments."""

    def test_notify_report_posts_header_context_and_section(self, adapter: SlackAdapter) -> None:
        """The post is the title header, an event+tail context line and the body section."""
        with respx.mock() as router:
            route = router.post(_POST_MESSAGE_URL).mock(return_value=_ok())
            adapter.notify_report(
                event="monthly.tax.report",
                correlation_id=_TEST_CORRELATION_ID,
                title="Q1 Tax Summary",
                body="*net delta*: +0.3%",
                attachments=None,
            )
        assert _json(route) == {
            "channel": _TEST_CHANNEL,
            "text": "Q1 Tax Summary \u2014 monthly.tax.report [ABCDEF]",
            "blocks": [
                {
                    "type": "header",
                    "text": {"type": "plain_text", "text": "Q1 Tax Summary", "emoji": True},
                },
                {
                    "type": "context",
                    "elements": [
                        {"type": "mrkdwn", "text": "*monthly.tax.report* \u00b7 `ABCDEF`"}
                    ],
                },
                {"type": "section", "text": {"type": "mrkdwn", "text": "*net delta*: +0.3%"}},
            ],
        }

    def test_notify_report_with_empty_attachments_posts_only_the_message(
        self, adapter: SlackAdapter
    ) -> None:
        """attachments=[] posts the message and starts no upload."""
        # No upload route is registered: an upload call would raise inside respx.mock.
        with respx.mock(assert_all_mocked=True) as router:
            route = router.post(_POST_MESSAGE_URL).mock(return_value=_ok())
            _report(adapter, [])
            urls = [str(call.request.url) for call in router.calls]
        assert (urls, route.call_count) == ([_POST_MESSAGE_URL], 1)

    def test_step1_is_an_authorized_form_with_filename_and_byte_length(
        self, adapter: SlackAdapter, tmp_path: Path
    ) -> None:
        """Step 1 (getUploadURLExternal) is a Bearer form naming the file and its byte length."""
        csv_path = tmp_path / "report.csv"
        csv_path.write_bytes(b"col1,col2\n1,2\n")
        with respx.mock() as router:
            routes = _mock_upload(router)
            _report(adapter, [csv_path])
        request = routes["step1"].calls[0].request
        assert (
            request.content,
            request.headers["content-type"],
            request.headers["authorization"],
        ) == (
            b"filename=report.csv&length=14",
            "application/x-www-form-urlencoded",
            f"Bearer {_TEST_TOKEN}",
        )

    def test_step2_puts_the_file_bytes_unauthenticated_to_the_signed_url(
        self, adapter: SlackAdapter, tmp_path: Path
    ) -> None:
        """The raw file bytes go to the signed URL with no bot token attached."""
        csv_path = tmp_path / "report.csv"
        csv_path.write_bytes(b"col1,col2\n1,2\n")
        with respx.mock() as router:
            routes = _mock_upload(router)
            _report(adapter, [csv_path])
        request = routes["step2"].calls[0].request
        assert (request.content, "authorization" in request.headers) == (
            b"col1,col2\n1,2\n",
            False,
        )

    def test_step3_shares_the_file_in_the_parent_thread(
        self, adapter: SlackAdapter, tmp_path: Path
    ) -> None:
        """Step 3 (completeUploadExternal) names step 1's file_id, the channel and the parent ts."""
        csv_path = tmp_path / "report.csv"
        csv_path.write_bytes(b"x")
        with respx.mock() as router:
            routes = _mock_upload(router)
            _report(adapter, [csv_path])
        assert _json(routes["step3"]) == {
            "files": [{"id": "F999", "title": "report.csv"}],
            "channel_id": _TEST_CHANNEL,
            "thread_ts": _PARENT_TS,
        }

    @pytest.mark.parametrize(
        "parent_body",
        [{"ok": True}, {"ok": True, "ts": ""}, {"ok": True, "ts": 1700000000}],
        ids=["no-ts", "empty-ts", "numeric-ts"],
    )
    def test_without_a_usable_parent_ts_the_file_is_shared_top_level(
        self, adapter: SlackAdapter, tmp_path: Path, parent_body: dict[str, object]
    ) -> None:
        """A parent without a non-empty string ts gets an unthreaded file share."""
        csv_path = tmp_path / "report.csv"
        csv_path.write_bytes(b"x")
        with respx.mock() as router:
            routes = _mock_upload(router, parent=httpx.Response(200, json=parent_body))
            _report(adapter, [csv_path])
        assert _json(routes["step3"]) == {
            "files": [{"id": "F999", "title": "report.csv"}],
            "channel_id": _TEST_CHANNEL,
        }

    def test_each_attachment_is_uploaded_in_order(
        self, adapter: SlackAdapter, tmp_path: Path
    ) -> None:
        """Two attachments run the upload twice, in the order given."""
        first, second = tmp_path / "a.csv", tmp_path / "b.pdf"
        first.write_bytes(b"1")
        second.write_bytes(b"22")
        with respx.mock() as router:
            routes = _mock_upload(router)
            _report(adapter, [first, second])
        bodies = [call.request.content for call in routes["step1"].calls]
        assert bodies == [b"filename=a.csv&length=1", b"filename=b.pdf&length=2"]

    def test_unreadable_attachment_raises_naming_the_path(
        self, adapter: SlackAdapter, tmp_path: Path
    ) -> None:
        """A missing attachment raises before any upload call, naming the path and cause."""
        missing = tmp_path / "does-not-exist.csv"
        # Only the parent post is mocked: an upload call would raise inside respx.mock.
        with respx.mock(assert_all_mocked=True) as router:
            router.post(_POST_MESSAGE_URL).mock(return_value=_ok(ts=_PARENT_TS))
            with pytest.raises(SlackNotifierError) as excinfo:
                _report(adapter, [missing])
        assert (
            str(excinfo.value).startswith(
                f"Slack notifier error: cannot read attachment {missing}: FileNotFoundError("
            ),
            type(excinfo.value.__cause__),
        ) == (True, FileNotFoundError)

    def test_step1_without_upload_url_raises(self, adapter: SlackAdapter, tmp_path: Path) -> None:
        """A getUploadURLExternal body with no upload_url is malformed and raises."""
        assert _report_error(adapter, tmp_path, step1=_ok(file_id="F1")) == (
            "Slack notifier error: files.getUploadURLExternal returned malformed body: "
            "{'ok': True, 'file_id': 'F1'}"
        )

    def test_step1_without_file_id_raises(self, adapter: SlackAdapter, tmp_path: Path) -> None:
        """A getUploadURLExternal body with no file_id is malformed and raises."""
        assert _report_error(adapter, tmp_path, step1=_ok(upload_url=_SIGNED_URL)) == (
            "Slack notifier error: files.getUploadURLExternal returned malformed body: "
            f"{{'ok': True, 'upload_url': '{_SIGNED_URL}'}}"
        )

    def test_malformed_step1_excerpt_is_capped_at_200_chars(
        self, adapter: SlackAdapter, tmp_path: Path
    ) -> None:
        """A huge malformed step-1 body is quoted only up to its first 200 chars."""
        quoted = "{'ok': True, 'pad': '" + "p" * 300
        assert _report_error(adapter, tmp_path, step1=_ok(pad="p" * 300)) == (
            "Slack notifier error: files.getUploadURLExternal returned malformed body: "
            f"{quoted[:200]}"
        )

    def test_step1_rejection_raises(self, adapter: SlackAdapter, tmp_path: Path) -> None:
        """An ok=false from getUploadURLExternal raises like any other Web API rejection."""
        response = httpx.Response(200, json={"ok": False, "error": "missing_scope"})
        assert _report_error(adapter, tmp_path, step1=response) == (
            "Slack notifier error: Slack API rejected /api/files.getUploadURLExternal: "
            "{'ok': False, 'error': 'missing_scope'}"
        )

    def test_signed_put_error_status_raises(self, adapter: SlackAdapter, tmp_path: Path) -> None:
        """HTTP 400 from the signed PUT is an error; the message names the file and body."""
        assert _report_error(adapter, tmp_path, step2=httpx.Response(400, text="bad")) == (
            "Slack notifier error: HTTP 400 uploading r.csv: bad"
        )

    def test_signed_put_error_excerpt_is_capped_at_200_chars(
        self, adapter: SlackAdapter, tmp_path: Path
    ) -> None:
        """A huge PUT error body is cut to its first 200 chars in the message."""
        response = httpx.Response(500, text="z" * 200 + "TAIL")
        assert _report_error(adapter, tmp_path, step2=response) == (
            f"Slack notifier error: HTTP 500 uploading r.csv: {'z' * 200}"
        )

    def test_signed_put_status_399_still_completes_the_upload(
        self, adapter: SlackAdapter, tmp_path: Path
    ) -> None:
        """A PUT status just below 400 is a success and the upload is completed."""
        csv_path = tmp_path / "r.csv"
        csv_path.write_bytes(b"x")
        with respx.mock() as router:
            routes = _mock_upload(router)
            routes["step2"].mock(return_value=httpx.Response(399))
            _report(adapter, [csv_path])
        assert _json(routes["step3"])["files"] == [{"id": "F999", "title": "r.csv"}]

    def test_signed_put_transport_error_raises(self, adapter: SlackAdapter, tmp_path: Path) -> None:
        """A dropped connection during the PUT is wrapped, naming the file."""
        csv_path = tmp_path / "r.csv"
        csv_path.write_bytes(b"x")
        with respx.mock(assert_all_called=False) as router:
            routes = _mock_upload(router)
            routes["step2"].mock(side_effect=httpx.ConnectError("edge failure"))
            with pytest.raises(SlackNotifierError) as excinfo:
                _report(adapter, [csv_path])
        assert str(excinfo.value) == (
            "Slack notifier error: transport failure uploading r.csv: ConnectError('edge failure')"
        )

    def test_step1_transport_error_raises(self, adapter: SlackAdapter, tmp_path: Path) -> None:
        """A dropped connection at getUploadURLExternal is wrapped, naming the endpoint."""
        csv_path = tmp_path / "r.csv"
        csv_path.write_bytes(b"x")
        with respx.mock(assert_all_called=False) as router:
            routes = _mock_upload(router)
            routes["step1"].mock(side_effect=httpx.ConnectError("upstream down"))
            with pytest.raises(SlackNotifierError) as excinfo:
                _report(adapter, [csv_path])
        assert str(excinfo.value) == (
            "Slack notifier error: transport failure calling /api/files.getUploadURLExternal: "
            "ConnectError('upstream down')"
        )

    def test_step3_rejection_raises(self, adapter: SlackAdapter, tmp_path: Path) -> None:
        """An ok=false from completeUploadExternal raises; the share did not happen."""
        response = httpx.Response(200, json={"ok": False, "error": "not_in_channel"})
        assert _report_error(adapter, tmp_path, step3=response) == (
            "Slack notifier error: Slack API rejected /api/files.completeUploadExternal: "
            "{'ok': False, 'error': 'not_in_channel'}"
        )


class TestPostMessageExtension:
    """Verify the :meth:`post_message` extension surface (beyond NotifierPort)."""

    def test_post_message_forwards_explicit_blocks_verbatim(self, adapter: SlackAdapter) -> None:
        """Explicit Block Kit blocks reach Slack unchanged, beside the text fallback."""
        blocks: list[dict[str, Any]] = [
            {"type": "divider"},
            {"type": "section", "text": {"type": "mrkdwn", "text": "*hello*"}},
        ]
        with respx.mock() as router:
            route = router.post(_POST_MESSAGE_URL).mock(return_value=_ok())
            adapter.post_message(message="fallback preview", blocks=blocks)
        assert _json(route) == {
            "channel": _TEST_CHANNEL,
            "text": "fallback preview",
            "blocks": blocks,
        }

    def test_post_message_channel_override_targets_alternate_channel(
        self, adapter: SlackAdapter
    ) -> None:
        """A ``channel=`` kwarg replaces the default channel in the payload."""
        with respx.mock() as router:
            route = router.post(_POST_MESSAGE_URL).mock(return_value=_ok())
            adapter.post_message(message="hi", channel="#other-room")
        assert _json(route) == {"channel": "#other-room", "text": "hi"}

    def test_post_message_without_blocks_omits_the_key(self, adapter: SlackAdapter) -> None:
        """With blocks=None the payload is channel + text only (Slack rejects null blocks)."""
        with respx.mock() as router:
            route = router.post(_POST_MESSAGE_URL).mock(return_value=_ok())
            adapter.post_message(message="text only")
        assert _json(route) == {"channel": _TEST_CHANNEL, "text": "text only"}

    def test_post_message_rejection_raises(self, adapter: SlackAdapter) -> None:
        """post_message validates the response like every other call."""
        with respx.mock() as router:
            router.post(_POST_MESSAGE_URL).mock(
                return_value=httpx.Response(200, json={"ok": False, "error": "invalid_blocks"})
            )
            with pytest.raises(SlackNotifierError, match="'error': 'invalid_blocks'"):
                adapter.post_message(message="m", blocks=[{"type": "bogus"}])


class TestCreateAndClose:
    """Verify the :meth:`SlackAdapter.create` factory and :meth:`close` lifecycle."""

    def test_create_targets_slack_with_the_given_timeout(self, config: SlackConfig) -> None:
        """create() builds a client on slack.com bounded by the requested timeout."""
        adapter = SlackAdapter.create(config, timeout_sec=7.5)
        try:
            assert (str(adapter.client.base_url), adapter.client.timeout) == (
                "https://slack.com",
                httpx.Timeout(7.5),
            )
        finally:
            adapter.close()

    def test_create_defaults_to_a_ten_second_timeout(self, config: SlackConfig) -> None:
        """Without a timeout, create() bounds every request at 10 s (module design notes)."""
        adapter = SlackAdapter.create(config)
        try:
            assert adapter.client.timeout == httpx.Timeout(10.0)
        finally:
            adapter.close()

    def test_close_releases_the_client_and_tolerates_a_second_call(
        self, config: SlackConfig
    ) -> None:
        """close() closes the HTTP client, and calling it again does not raise."""
        adapter = SlackAdapter.create(config)
        adapter.close()
        adapter.close()
        assert adapter.client.is_closed is True


class TestNotifyEvent:
    """`notify(event)` posts the template's text and blocks."""

    def test_notify_posts_the_rendered_strategy_pause(self, adapter: SlackAdapter) -> None:
        """notify() posts the channel, fallback text and Block Kit blocks of the event."""
        with respx.mock() as router:
            route = router.post(_POST_MESSAGE_URL).mock(return_value=_ok(ts="1234.5"))
            adapter.notify(_strategy_paused_event())
        headline = "\u25a0 Strategy paused \u00b7 swing-btc \u00b7 Sun 16:22"
        reason = "drawdown breach \u2014 6.2% over daily cap"
        assert _json(route) == {
            "channel": _TEST_CHANNEL,
            "text": f"{headline} \u00b7 {reason}",
            "blocks": [
                {"type": "section", "text": {"type": "mrkdwn", "text": f"*{headline}*\n{reason}"}},
                {
                    "type": "context",
                    "elements": [
                        {"type": "mrkdwn", "text": "review the strategy \u00b7 \u2026ABCDEF"}
                    ],
                },
            ],
        }

    def test_notify_sends_bearer_authorization_header(self, adapter: SlackAdapter) -> None:
        """The Authorization header is Bearer <bot_token> for the template-driven path too."""
        with respx.mock() as router:
            route = router.post(_POST_MESSAGE_URL).mock(return_value=_ok(ts="1.0"))
            adapter.notify(_strategy_paused_event())
        assert route.calls[0].request.headers["authorization"] == f"Bearer {_TEST_TOKEN}"

    def test_notify_without_attachments_makes_exactly_one_post(self, adapter: SlackAdapter) -> None:
        """A short event with no attachment and no plan is one chat.postMessage call."""
        # Only chat.postMessage is mocked: any upload call would raise inside respx.mock.
        with respx.mock(assert_all_mocked=True) as router:
            router.post(_POST_MESSAGE_URL).mock(return_value=_ok(ts="1.0"))
            adapter.notify(_strategy_paused_event())
            urls = [str(call.request.url) for call in router.calls]
        assert urls == [_POST_MESSAGE_URL]

    def test_notify_ok_false_body_raises(self, adapter: SlackAdapter) -> None:
        """The template-driven path surfaces Slack's HTTP-200-with-ok-false footgun."""
        with respx.mock() as router:
            router.post(_POST_MESSAGE_URL).mock(
                return_value=httpx.Response(200, json={"ok": False, "error": "channel_not_found"})
            )
            with pytest.raises(SlackNotifierError) as excinfo:
                adapter.notify(_strategy_paused_event())
        assert str(excinfo.value) == (
            "Slack notifier error: Slack API rejected /api/chat.postMessage: "
            "{'ok': False, 'error': 'channel_not_found'}"
        )


class TestNotifyDailyDigestThreadedAttachment:
    """DailyTradeDigest is the attachment-bearing event — CSV lands as a threaded reply."""

    def test_blind_adapter_posts_digest_without_any_upload(self, adapter: SlackAdapter) -> None:
        """A default (pre-certification) adapter never uploads the per-pair P&L CSV.

        Protects the ADR-012 ruling 1 blind at the wire: the outcome CSV
        must not leave the process, not merely be hidden in the template.
        """
        # Only chat.postMessage is mocked: any upload call would raise inside respx.mock.
        with respx.mock(assert_all_mocked=True) as router:
            post = router.post(_POST_MESSAGE_URL).mock(return_value=_ok(ts="1.0"))
            adapter.notify(_digest_event())
            urls = [str(call.request.url) for call in router.calls]
        body = post.calls[0].request.content.decode()
        assert (
            urls,
            "120.00" in body,
            "-45.50" in body,
        ) == ([_POST_MESSAGE_URL], False, False)

    def test_daily_digest_puts_the_csv_bytes_and_announces_their_length(
        self, unblinded_adapter: SlackAdapter
    ) -> None:
        """Step 1 names the CSV and its byte count; step 2 PUTs exactly those CSV bytes."""
        with respx.mock() as router:
            routes = _mock_upload(router)
            unblinded_adapter.notify(_digest_event())
        assert (
            routes["step1"].calls[0].request.content,
            routes["step2"].calls[0].request.content,
        ) == (b"filename=digest-2026-08-23.csv&length=92", _DIGEST_CSV)

    def test_daily_digest_attachment_lands_in_parent_thread(
        self, unblinded_adapter: SlackAdapter
    ) -> None:
        """The completeUploadExternal payload carries thread_ts from the parent post."""
        with respx.mock() as router:
            routes = _mock_upload(router)
            unblinded_adapter.notify(_digest_event())
        assert _json(routes["step3"]) == {
            "files": [{"id": "F999", "title": "digest-2026-08-23.csv"}],
            "channel_id": _TEST_CHANNEL,
            "thread_ts": _PARENT_TS,
        }

    @pytest.mark.parametrize(
        "parent_body",
        [{"ok": True}, {"ok": True, "ts": ""}, {"ok": True, "ts": 1700000000}],
        ids=["no-ts", "empty-ts", "numeric-ts"],
    )
    def test_daily_digest_without_a_usable_parent_ts_shares_top_level(
        self, unblinded_adapter: SlackAdapter, parent_body: dict[str, object]
    ) -> None:
        """A parent without a non-empty string ts still gets the CSV, unthreaded."""
        with respx.mock() as router:
            routes = _mock_upload(router, parent=httpx.Response(200, json=parent_body))
            unblinded_adapter.notify(_digest_event())
        assert _json(routes["step3"]) == {
            "files": [{"id": "F999", "title": "digest-2026-08-23.csv"}],
            "channel_id": _TEST_CHANNEL,
        }


class TestAuthTest:
    """`auth_test()` powers `hermes health slack` — read-only, never sends messages."""

    def test_auth_test_returns_the_full_response_body(self, adapter: SlackAdapter) -> None:
        """auth.test returns the parsed body dict unchanged."""
        with respx.mock() as router:
            router.post(_AUTH_TEST_URL).mock(
                return_value=httpx.Response(
                    200, json={"ok": True, "team": "hermes-ws", "user": "hermes_bot"}
                )
            )
            result = adapter.auth_test()
        assert result == {"ok": True, "team": "hermes-ws", "user": "hermes_bot"}

    def test_auth_test_posts_an_empty_authorized_json_body(self, adapter: SlackAdapter) -> None:
        """The probe is a Bearer-authorized POST of ``{}`` to auth.test — nothing is sent."""
        with respx.mock() as router:
            route = router.post(_AUTH_TEST_URL).mock(return_value=_ok(team="t", user="u"))
            adapter.auth_test()
        request = route.calls[0].request
        assert (json.loads(request.content), request.headers["authorization"]) == (
            {},
            f"Bearer {_TEST_TOKEN}",
        )

    def test_auth_test_ok_false_raises_with_the_slack_error(self, adapter: SlackAdapter) -> None:
        """HTTP 200 with ok=false (invalid_auth) raises — Slack's HTTP-200 footgun."""
        with respx.mock() as router:
            router.post(_AUTH_TEST_URL).mock(
                return_value=httpx.Response(200, json={"ok": False, "error": "invalid_auth"})
            )
            with pytest.raises(SlackNotifierError) as excinfo:
                adapter.auth_test()
        assert str(excinfo.value) == (
            "Slack notifier error: Slack API rejected /api/auth.test: "
            "{'ok': False, 'error': 'invalid_auth'}"
        )

    def test_auth_test_transport_failure_wraps(self, adapter: SlackAdapter) -> None:
        """A httpx transport error during auth.test is wrapped, naming the endpoint."""
        with respx.mock() as router:
            router.post(_AUTH_TEST_URL).mock(side_effect=httpx.ConnectError("DNS failed"))
            with pytest.raises(SlackNotifierError) as excinfo:
                adapter.auth_test()
        assert str(excinfo.value) == (
            "Slack notifier error: transport failure calling /api/auth.test: "
            "ConnectError('DNS failed')"
        )


class TestEurRate:
    """The injected rate lookup decides whether a post shows EUR or USDT (change 668)."""

    @staticmethod
    def _sent_price(adapter: SlackAdapter) -> str:
        """Notify the sample signal entry and return the price field of the parent text."""
        with respx.mock() as router:
            route = router.post(_POST_MESSAGE_URL).mock(return_value=_ok(ts="1234.5"))
            adapter.notify(samples()["signal-entry"])
        return str(_json(route)["text"]).split(" \u00b7 ")[1]

    def test_the_lookup_receives_the_event_and_its_rate_reaches_the_wire(
        self, config: SlackConfig
    ) -> None:
        """Invariant (668 R-4): the lookup gets the event; its rate prices the post."""
        seen: list[DomainEvent] = []

        def lookup(event: DomainEvent) -> Decimal | None:
            seen.append(event)
            return Decimal("1.25")

        client = httpx.Client(base_url="https://slack.com", timeout=5.0)
        adapter = SlackAdapter(config=config, client=client, eur_rate=lookup)

        price = self._sent_price(adapter)

        assert (seen, price) == ([samples()["signal-entry"]], "\u20ac41,928.16")

    def test_a_lookup_with_no_rate_sends_the_usdt_text(self, config: SlackConfig) -> None:
        """Invariant (668 R-2): a cold cache keeps the USDT text."""
        client = httpx.Client(base_url="https://slack.com", timeout=5.0)
        adapter = SlackAdapter(config=config, client=client, eur_rate=lambda _event: None)
        assert self._sent_price(adapter) == "52,410.20 USDT"

    def test_a_failing_lookup_still_delivers_the_post_in_usdt(self, config: SlackConfig) -> None:
        """Invariant (668 R-2): a lookup that raises never stops the notification."""

        def boom(_event: DomainEvent) -> Decimal | None:
            raise RuntimeError("cache exploded")

        client = httpx.Client(base_url="https://slack.com", timeout=5.0)
        adapter = SlackAdapter(config=config, client=client, eur_rate=boom)
        assert self._sent_price(adapter) == "52,410.20 USDT"

    def test_create_wires_the_lookup_into_the_sent_post(self, config: SlackConfig) -> None:
        """Invariant (668 D-2): an adapter built by the factory prices posts with the lookup."""
        adapter = SlackAdapter.create(config, eur_rate=lambda _event: Decimal("1.25"))
        try:
            assert self._sent_price(adapter) == "\u20ac41,928.16"
        finally:
            adapter.close()
