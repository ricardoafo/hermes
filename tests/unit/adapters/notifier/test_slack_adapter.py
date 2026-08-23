"""Contract tests for :class:`hermes.adapters.notifier.SlackAdapter`.

Every test protects one invariant of the Slack adapter contract. Wire
calls are mocked via ``respx`` (declared in dev deps per ADR-007). No real
socket ever opens; no real credentials are ever transmitted.

Coverage focus areas:

- Config loading via :meth:`SlackConfig.from_env` (missing / whitespace).
- :meth:`notify_urgent` happy path (payload shape, headers, channel).
- Slack HTTP-200 with ``ok=false`` in body — the "raise_for_status is not
  enough" footgun. Every response-validation path exercises this.
- HTTP-status error paths (401 auth, 429 rate-limit).
- Transport error wrapping (httpx.ConnectError → SlackNotifierError).
- Non-JSON response body handling.
- :meth:`notify_report` Block Kit payload shape + attachment 3-step upload.
- :meth:`post_message` extension surface (explicit blocks, channel override).
- :class:`NotifierPort` Protocol conformance.
- Factory + close lifecycle.

Reference: docs/decisions/2026-08-23-slack-alongside-telegram.md;
    docs/guides/ai-authored-tests.md §"The hermes protocol".
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
import respx

from hermes.adapters.notifier import (
    SlackAdapter,
    SlackConfig,
    SlackNotifierError,
)
from hermes.domain.errors import HermesError, InfraError
from hermes.domain.events.anomaly_detected import AnomalySeverity
from hermes.domain.events.daily_trade_digest import DailyTradeDigest, TradeSummary
from hermes.domain.events.strategy_paused import StrategyPaused
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.pair import Pair
from hermes.ports.notifier import NotifierPort

_TEST_TOKEN = "xoxb-test"  # noqa: S105 - test-only bot token, never real
_TEST_CHANNEL = "C0123ABCD"
_TEST_CORRELATION_ID = "01HKQMZXYZ0123456789ABCDEF"  # 26-char pseudo-ULID
_TEST_CORRELATION_TAIL = "ABCDEF"  # last 6 chars

_POST_MESSAGE_URL = "https://slack.com/api/chat.postMessage"
_GET_UPLOAD_URL = "https://slack.com/api/files.getUploadURLExternal"
_COMPLETE_UPLOAD_URL = "https://slack.com/api/files.completeUploadExternal"


@pytest.fixture
def config() -> SlackConfig:
    """Return a fixed :class:`SlackConfig` for adapter construction."""
    return SlackConfig(bot_token=_TEST_TOKEN, default_channel=_TEST_CHANNEL)


@pytest.fixture
def adapter(config: SlackConfig) -> SlackAdapter:
    """Return a :class:`SlackAdapter` wired with a plain httpx.Client.

    ``respx`` patches transport globally in the ``respx_mock`` context
    manager per test, so the client here does NOT need MockTransport.
    """
    client = httpx.Client(base_url="https://slack.com", timeout=5.0)
    return SlackAdapter(config=config, client=client)


class TestSlackConfig:
    """Verify credential-loading contract for :class:`SlackConfig`."""

    def test_from_env_loads_both_variables(self) -> None:
        """from_env returns config populated from the two HERMES_SLACK__* env vars."""
        env = {
            "HERMES_SLACK__BOT_TOKEN": "xoxb-token-abc",
            "HERMES_SLACK__DEFAULT_CHANNEL": "#general",
        }
        cfg = SlackConfig.from_env(env)
        assert cfg.bot_token == "xoxb-token-abc"  # noqa: S105 - test literal
        assert cfg.default_channel == "#general"

    def test_from_env_raises_when_bot_token_missing(self) -> None:
        """Missing HERMES_SLACK__BOT_TOKEN raises SlackNotifierError at load time."""
        env = {"HERMES_SLACK__DEFAULT_CHANNEL": "#general"}
        with pytest.raises(SlackNotifierError, match="BOT_TOKEN"):
            SlackConfig.from_env(env)

    def test_from_env_raises_when_default_channel_missing(self) -> None:
        """Missing HERMES_SLACK__DEFAULT_CHANNEL raises SlackNotifierError at load time."""
        env = {"HERMES_SLACK__BOT_TOKEN": "xoxb-token-abc"}
        with pytest.raises(SlackNotifierError, match="DEFAULT_CHANNEL"):
            SlackConfig.from_env(env)

    def test_from_env_treats_whitespace_as_missing(self) -> None:
        """A whitespace-only env var is treated as missing (fail-fast, no silent success)."""
        env = {
            "HERMES_SLACK__BOT_TOKEN": "   ",
            "HERMES_SLACK__DEFAULT_CHANNEL": "#general",
        }
        with pytest.raises(SlackNotifierError, match="BOT_TOKEN"):
            SlackConfig.from_env(env)


class TestNotifyUrgentHappyPath:
    """Verify successful message-send behaviour of :meth:`notify_urgent`."""

    def test_notify_urgent_posts_to_chat_post_message(self, adapter: SlackAdapter) -> None:
        """notify_urgent hits POST /api/chat.postMessage on slack.com."""
        with respx.mock(assert_all_called=True) as router:
            route = router.post(_POST_MESSAGE_URL).mock(
                return_value=httpx.Response(200, json={"ok": True, "channel": _TEST_CHANNEL})
            )
            adapter.notify_urgent(
                event="rollback.fired",
                correlation_id=_TEST_CORRELATION_ID,
                message="Rollback executed on api node 3.",
            )
            assert route.called
            assert route.call_count == 1

    def test_notify_urgent_payload_targets_default_channel(self, adapter: SlackAdapter) -> None:
        """Payload carries the configured default channel and a text body."""
        with respx.mock() as router:
            route = router.post(_POST_MESSAGE_URL).mock(
                return_value=httpx.Response(200, json={"ok": True})
            )
            adapter.notify_urgent(
                event="health.check.fail",
                correlation_id=_TEST_CORRELATION_ID,
                message="db unreachable",
            )
            payload = json.loads(route.calls[0].request.content)
            assert payload["channel"] == _TEST_CHANNEL
            assert "db unreachable" in payload["text"]

    def test_notify_urgent_includes_correlation_tail_in_context_block(
        self, adapter: SlackAdapter
    ) -> None:
        """The Block Kit context footer carries the last 6 chars of the correlation ID.

        Template-driven path: the tail lives in the context block
        (``ref: <tail>``) rather than the plain-text field. Asserting on
        the block preserves the ADR-008 contract while matching the new
        rendering surface (:func:`slack_template`).
        """
        with respx.mock() as router:
            route = router.post(_POST_MESSAGE_URL).mock(
                return_value=httpx.Response(200, json={"ok": True})
            )
            adapter.notify_urgent(
                event="service.crash",
                correlation_id=_TEST_CORRELATION_ID,
                message="node down",
            )
            payload = json.loads(route.calls[0].request.content)
            context_block = payload["blocks"][-1]
            assert context_block["type"] == "context"
            context_text = context_block["elements"][0]["text"]
            assert _TEST_CORRELATION_TAIL in context_text

    def test_notify_urgent_short_correlation_id_used_verbatim(self, adapter: SlackAdapter) -> None:
        """A correlation ID shorter than 6 chars is used in full (no exception).

        Template-driven path: the tail lives in the context footer
        block. A 3-char ID (``abc``) is shorter than the 6-char cap so
        it appears verbatim there, not silently truncated.
        """
        with respx.mock() as router:
            route = router.post(_POST_MESSAGE_URL).mock(
                return_value=httpx.Response(200, json={"ok": True})
            )
            adapter.notify_urgent(
                event="deploy.ok",
                correlation_id="abc",
                message="ok",
            )
            payload = json.loads(route.calls[0].request.content)
            context_block = payload["blocks"][-1]
            assert context_block["type"] == "context"
            assert "abc" in context_block["elements"][0]["text"]

    def test_notify_urgent_sends_bearer_authorization_header(self, adapter: SlackAdapter) -> None:
        """The Authorization header is Bearer <bot_token> — Slack's required auth mode."""
        with respx.mock() as router:
            route = router.post(_POST_MESSAGE_URL).mock(
                return_value=httpx.Response(200, json={"ok": True})
            )
            adapter.notify_urgent(
                event="deploy.ok",
                correlation_id=_TEST_CORRELATION_ID,
                message="shipped",
            )
            auth = route.calls[0].request.headers.get("authorization", "")
            assert auth == f"Bearer {_TEST_TOKEN}"


class TestNotifyUrgentErrorPaths:
    """Verify :meth:`notify_urgent` raises on every documented failure mode."""

    def test_ok_false_body_on_http_200_raises(self, adapter: SlackAdapter) -> None:
        """HTTP 200 with ``ok=false`` in body raises — Slack's HTTP-200 footgun.

        Slack routinely returns 200 for auth errors, missing scopes, and
        channel-not-found. A naive raise_for_status() would swallow these
        silently; our adapter MUST inspect body["ok"].
        """
        with respx.mock() as router:
            router.post(_POST_MESSAGE_URL).mock(
                return_value=httpx.Response(
                    200,
                    json={"ok": False, "error": "channel_not_found"},
                )
            )
            with pytest.raises(SlackNotifierError, match="rejected"):
                adapter.notify_urgent(
                    event="deploy.failed",
                    correlation_id=_TEST_CORRELATION_ID,
                    message="deploy 1.2.3 rolled back",
                )

    def test_ok_false_response_preserves_ok_flag_value(self, adapter: SlackAdapter) -> None:
        """The raised error surfaces the ``ok=false`` payload so operators can diagnose."""
        payload = {"ok": False, "error": "invalid_auth"}
        with respx.mock() as router:
            router.post(_POST_MESSAGE_URL).mock(return_value=httpx.Response(200, json=payload))
            with pytest.raises(SlackNotifierError) as excinfo:
                adapter.notify_urgent(
                    event="health.check.fail",
                    correlation_id=_TEST_CORRELATION_ID,
                    message="db down",
                )
            # The rendered error message must include the Slack error code
            # so an on-call operator can act without opening a debugger.
            assert "invalid_auth" in str(excinfo.value)

    def test_auth_failure_401_raises_notifier_error(self, adapter: SlackAdapter) -> None:
        """HTTP 401 (bad bot token, rare — Slack usually returns 200) raises with the status."""
        with respx.mock() as router:
            router.post(_POST_MESSAGE_URL).mock(
                return_value=httpx.Response(401, text="Unauthorized")
            )
            with pytest.raises(SlackNotifierError, match="401"):
                adapter.notify_urgent(
                    event="cve.detected",
                    correlation_id=_TEST_CORRELATION_ID,
                    message="CVE-2026-9999 in urllib3",
                )

    def test_rate_limit_429_raises_notifier_error(self, adapter: SlackAdapter) -> None:
        """HTTP 429 (rate limit) raises SlackNotifierError instead of silent retry.

        Slack rate-limits per method; ``chat.postMessage`` is tier 4 with
        per-channel limits on top. Adaptive back-off is a router-layer
        concern — the adapter surfaces pressure so the caller sees it.
        """
        with respx.mock() as router:
            router.post(_POST_MESSAGE_URL).mock(
                return_value=httpx.Response(
                    429,
                    headers={"Retry-After": "30"},
                    text="rate limited",
                )
            )
            with pytest.raises(SlackNotifierError, match="429"):
                adapter.notify_urgent(
                    event="deploy.done",
                    correlation_id=_TEST_CORRELATION_ID,
                    message="deploy 1.2.3 shipped",
                )

    def test_network_error_wraps_httpx_exception(self, adapter: SlackAdapter) -> None:
        """A transport-level failure (httpx.ConnectError) is wrapped as SlackNotifierError."""
        with respx.mock() as router:
            router.post(_POST_MESSAGE_URL).mock(side_effect=httpx.ConnectError("DNS failed"))
            with pytest.raises(SlackNotifierError, match="transport failure"):
                adapter.notify_urgent(
                    event="health.check.fail",
                    correlation_id=_TEST_CORRELATION_ID,
                    message="db unreachable",
                )

    def test_non_json_response_raises_notifier_error(self, adapter: SlackAdapter) -> None:
        """A 2xx response that is not JSON raises SlackNotifierError, not ValueError."""
        with respx.mock() as router:
            router.post(_POST_MESSAGE_URL).mock(
                return_value=httpx.Response(
                    200,
                    content=b"<html>gateway proxy error</html>",
                    headers={"content-type": "text/html"},
                )
            )
            with pytest.raises(SlackNotifierError, match="non-JSON"):
                adapter.notify_urgent(
                    event="service.crash",
                    correlation_id=_TEST_CORRELATION_ID,
                    message="oops",
                )

    def test_notifier_error_inherits_from_infra_error(self) -> None:
        """SlackNotifierError is an InfraError so 'except InfraError' catches it."""
        err = SlackNotifierError("something")
        assert isinstance(err, InfraError)
        assert isinstance(err, HermesError)


class TestNotifyReport:
    """Verify :meth:`notify_report` posts Block Kit and uploads attachments."""

    def test_notify_report_posts_header_context_and_section_blocks(
        self, adapter: SlackAdapter
    ) -> None:
        """The payload carries header, context (with corr-tail), and section blocks in order."""
        with respx.mock() as router:
            route = router.post(_POST_MESSAGE_URL).mock(
                return_value=httpx.Response(200, json={"ok": True})
            )
            adapter.notify_report(
                event="weekly.rebalance.report",
                correlation_id=_TEST_CORRELATION_ID,
                title="Weekly Rebalance",
                body="*net delta*: +0.3%\n*pairs touched*: 4",
                attachments=None,
            )
            payload = json.loads(route.calls[0].request.content)
            block_types = [b["type"] for b in payload["blocks"]]
            assert block_types == ["header", "context", "section"]
            # Correlation tail rendered in context block for grep-to-journald triage
            context_text = payload["blocks"][1]["elements"][0]["text"]
            assert _TEST_CORRELATION_TAIL in context_text
            assert "weekly.rebalance.report" in context_text

    def test_notify_report_includes_text_fallback_for_preview_clients(
        self, adapter: SlackAdapter
    ) -> None:
        """The `text` field is populated so notification previews render meaningfully."""
        with respx.mock() as router:
            route = router.post(_POST_MESSAGE_URL).mock(
                return_value=httpx.Response(200, json={"ok": True})
            )
            adapter.notify_report(
                event="monthly.tax.report",
                correlation_id=_TEST_CORRELATION_ID,
                title="Q1 Tax Summary",
                body="tables would render here",
                attachments=None,
            )
            payload = json.loads(route.calls[0].request.content)
            # Fallback text carries title + event + corr-tail so mobile previews are useful
            assert "Q1 Tax Summary" in payload["text"]
            assert "monthly.tax.report" in payload["text"]

    def test_notify_report_empty_attachments_list_makes_only_one_post(
        self, adapter: SlackAdapter
    ) -> None:
        """attachments=[] means no upload calls fire — only the chat.postMessage."""
        with respx.mock(assert_all_called=False) as router:
            post_route = router.post(_POST_MESSAGE_URL).mock(
                return_value=httpx.Response(200, json={"ok": True})
            )
            upload_route = router.post(_GET_UPLOAD_URL).mock(
                return_value=httpx.Response(200, json={"ok": True})
            )
            adapter.notify_report(
                event="weekly.rebalance.report",
                correlation_id=_TEST_CORRELATION_ID,
                title="t",
                body="b",
                attachments=[],
            )
            assert post_route.call_count == 1
            assert upload_route.call_count == 0

    def test_notify_report_uploads_attachment_via_three_step_flow(
        self, adapter: SlackAdapter, tmp_path: Path
    ) -> None:
        """A single attachment triggers getUploadURLExternal → PUT → completeUploadExternal."""
        csv_path = tmp_path / "report.csv"
        csv_path.write_text("col1,col2\n1,2\n", encoding="utf-8")

        signed_put_url = "https://files.slack.com/upload/signed-abc123"

        with respx.mock() as router:
            router.post(_POST_MESSAGE_URL).mock(return_value=httpx.Response(200, json={"ok": True}))
            step1 = router.post(_GET_UPLOAD_URL).mock(
                return_value=httpx.Response(
                    200,
                    json={"ok": True, "upload_url": signed_put_url, "file_id": "F999"},
                )
            )
            step2 = router.put(signed_put_url).mock(return_value=httpx.Response(200, text="OK"))
            step3 = router.post(_COMPLETE_UPLOAD_URL).mock(
                return_value=httpx.Response(200, json={"ok": True})
            )

            adapter.notify_report(
                event="monthly.tax.report",
                correlation_id=_TEST_CORRELATION_ID,
                title="Q1",
                body="see attached",
                attachments=[csv_path],
            )

            assert step1.call_count == 1
            assert step2.call_count == 1
            assert step3.call_count == 1

            # Step 1 form carries filename + byte-length
            step1_body = step1.calls[0].request.content.decode()
            assert "filename=report.csv" in step1_body
            # Step 3 JSON references the file_id from step 1 and the target channel
            step3_payload = json.loads(step3.calls[0].request.content)
            assert step3_payload["files"] == [{"id": "F999", "title": "report.csv"}]
            assert step3_payload["channel_id"] == _TEST_CHANNEL

    def test_notify_report_raises_when_attachment_unreadable(
        self, adapter: SlackAdapter, tmp_path: Path
    ) -> None:
        """A missing attachment path raises SlackNotifierError before any Slack call."""
        missing = tmp_path / "does-not-exist.csv"
        with respx.mock() as router:
            router.post(_POST_MESSAGE_URL).mock(return_value=httpx.Response(200, json={"ok": True}))
            with pytest.raises(SlackNotifierError, match="cannot read attachment"):
                adapter.notify_report(
                    event="monthly.tax.report",
                    correlation_id=_TEST_CORRELATION_ID,
                    title="Q1",
                    body="see attached",
                    attachments=[missing],
                )

    def test_notify_report_raises_when_signed_url_missing_from_step1(
        self, adapter: SlackAdapter, tmp_path: Path
    ) -> None:
        """A malformed getUploadURLExternal response (no upload_url) raises."""
        csv_path = tmp_path / "r.csv"
        csv_path.write_bytes(b"x")
        with respx.mock() as router:
            router.post(_POST_MESSAGE_URL).mock(return_value=httpx.Response(200, json={"ok": True}))
            router.post(_GET_UPLOAD_URL).mock(
                return_value=httpx.Response(200, json={"ok": True, "file_id": "F1"})
            )
            with pytest.raises(SlackNotifierError, match="malformed"):
                adapter.notify_report(
                    event="e",
                    correlation_id=_TEST_CORRELATION_ID,
                    title="t",
                    body="b",
                    attachments=[csv_path],
                )

    def test_notify_report_raises_when_signed_put_returns_error_status(
        self, adapter: SlackAdapter, tmp_path: Path
    ) -> None:
        """A non-2xx from the signed PUT URL surfaces as SlackNotifierError."""
        csv_path = tmp_path / "r.csv"
        csv_path.write_bytes(b"x")
        signed_url = "https://files.slack.com/upload/signed-xyz"
        with respx.mock() as router:
            router.post(_POST_MESSAGE_URL).mock(return_value=httpx.Response(200, json={"ok": True}))
            router.post(_GET_UPLOAD_URL).mock(
                return_value=httpx.Response(
                    200, json={"ok": True, "upload_url": signed_url, "file_id": "F2"}
                )
            )
            router.put(signed_url).mock(return_value=httpx.Response(500, text="proxy oops"))
            with pytest.raises(SlackNotifierError, match="500 uploading"):
                adapter.notify_report(
                    event="e",
                    correlation_id=_TEST_CORRELATION_ID,
                    title="t",
                    body="b",
                    attachments=[csv_path],
                )

    def test_notify_report_raises_when_get_upload_url_hits_transport_error(
        self, adapter: SlackAdapter, tmp_path: Path
    ) -> None:
        """A transport failure at step 1 (files.getUploadURLExternal) is wrapped."""
        csv_path = tmp_path / "r.csv"
        csv_path.write_bytes(b"x")
        with respx.mock() as router:
            router.post(_POST_MESSAGE_URL).mock(return_value=httpx.Response(200, json={"ok": True}))
            router.post(_GET_UPLOAD_URL).mock(side_effect=httpx.ConnectError("upstream down"))
            with pytest.raises(
                SlackNotifierError,
                match=r"transport failure calling /api/files\.getUploadURLExternal",
            ):
                adapter.notify_report(
                    event="e",
                    correlation_id=_TEST_CORRELATION_ID,
                    title="t",
                    body="b",
                    attachments=[csv_path],
                )

    def test_notify_report_raises_when_signed_put_hits_transport_error(
        self, adapter: SlackAdapter, tmp_path: Path
    ) -> None:
        """A transport failure during the PUT is wrapped as SlackNotifierError."""
        csv_path = tmp_path / "r.csv"
        csv_path.write_bytes(b"x")
        signed_url = "https://files.slack.com/upload/signed-net"
        with respx.mock() as router:
            router.post(_POST_MESSAGE_URL).mock(return_value=httpx.Response(200, json={"ok": True}))
            router.post(_GET_UPLOAD_URL).mock(
                return_value=httpx.Response(
                    200, json={"ok": True, "upload_url": signed_url, "file_id": "F3"}
                )
            )
            router.put(signed_url).mock(side_effect=httpx.ConnectError("edge failure"))
            with pytest.raises(SlackNotifierError, match="transport failure uploading"):
                adapter.notify_report(
                    event="e",
                    correlation_id=_TEST_CORRELATION_ID,
                    title="t",
                    body="b",
                    attachments=[csv_path],
                )


class TestPostMessageExtension:
    """Verify the :meth:`post_message` extension surface (beyond NotifierPort)."""

    def test_post_message_with_explicit_blocks_carries_them_verbatim(
        self, adapter: SlackAdapter
    ) -> None:
        """Explicit Block Kit blocks are forwarded to Slack unchanged."""
        blocks = [
            {"type": "divider"},
            {"type": "section", "text": {"type": "mrkdwn", "text": "*hello*"}},
        ]
        with respx.mock() as router:
            route = router.post(_POST_MESSAGE_URL).mock(
                return_value=httpx.Response(200, json={"ok": True})
            )
            adapter.post_message(message="fallback preview", blocks=blocks)
            payload = json.loads(route.calls[0].request.content)
            assert payload["blocks"] == blocks
            assert payload["text"] == "fallback preview"

    def test_post_message_channel_override_targets_alternate_channel(
        self, adapter: SlackAdapter
    ) -> None:
        """A ``channel=`` kwarg overrides the default channel in the payload."""
        with respx.mock() as router:
            route = router.post(_POST_MESSAGE_URL).mock(
                return_value=httpx.Response(200, json={"ok": True})
            )
            adapter.post_message(message="hi", channel="#other-room")
            payload = json.loads(route.calls[0].request.content)
            assert payload["channel"] == "#other-room"

    def test_post_message_without_blocks_omits_the_key(self, adapter: SlackAdapter) -> None:
        """When blocks=None the payload has no `blocks` key (Slack rejects null blocks)."""
        with respx.mock() as router:
            route = router.post(_POST_MESSAGE_URL).mock(
                return_value=httpx.Response(200, json={"ok": True})
            )
            adapter.post_message(message="text only")
            payload = json.loads(route.calls[0].request.content)
            assert "blocks" not in payload


class TestPortConformance:
    """Verify :class:`SlackAdapter` satisfies the :class:`NotifierPort` Protocol."""

    def test_adapter_is_notifier_port_instance(self, adapter: SlackAdapter) -> None:
        """The isinstance check against the runtime_checkable Protocol returns True."""
        assert isinstance(adapter, NotifierPort)


class TestCreateAndClose:
    """Verify the :meth:`SlackAdapter.create` factory and :meth:`close` lifecycle."""

    def test_create_returns_adapter_with_default_client(self, config: SlackConfig) -> None:
        """create() builds an adapter whose client targets slack.com."""
        adapter = SlackAdapter.create(config)
        try:
            assert isinstance(adapter.client, httpx.Client)
            assert str(adapter.client.base_url) == "https://slack.com"
        finally:
            adapter.close()

    def test_close_is_idempotent(self, config: SlackConfig) -> None:
        """close() called twice does not raise (second call is a no-op)."""
        adapter = SlackAdapter.create(config)
        adapter.close()
        adapter.close()  # must not raise


# ---------------------------------------------------------------------------
# Template-driven notify(event) + auth.test() — new preferred surface.
# ---------------------------------------------------------------------------

_AUTH_TEST_URL = "https://slack.com/api/auth.test"


class TestNotifyEvent:
    """`notify(event)` delegates rendering to slack_template and posts the blocks."""

    def _strategy_paused_event(self) -> StrategyPaused:
        """Return a minimal StrategyPaused event with no attachments."""
        return StrategyPaused(
            occurred_at=datetime(2026, 8, 23, 14, 22, 0, tzinfo=UTC),
            correlation_id=_TEST_CORRELATION_ID,
            strategy_id="swing-btc",
            severity=AnomalySeverity.CRITICAL,
            reason="drawdown breach — 6.2% over daily cap",
            paused_at=datetime(2026, 8, 23, 14, 22, 0, tzinfo=UTC),
        )

    def test_notify_posts_rendered_text_and_blocks_from_template(
        self, adapter: SlackAdapter
    ) -> None:
        """notify() posts the template's text + blocks to chat.postMessage."""
        with respx.mock() as router:
            route = router.post(_POST_MESSAGE_URL).mock(
                return_value=httpx.Response(200, json={"ok": True, "ts": "1234.5"})
            )
            adapter.notify(self._strategy_paused_event())
            assert route.call_count == 1
            payload = json.loads(route.calls[0].request.content)
            assert payload["channel"] == _TEST_CHANNEL
            # Template renders "Strategy paused — <strategy_id> — <reason>" as fallback text.
            assert "Strategy paused" in payload["text"]
            assert "swing-btc" in payload["text"]
            # Blocks list carries header + section + context (per template shape).
            block_types = [b["type"] for b in payload["blocks"]]
            assert block_types == ["header", "section", "context"]

    def test_notify_sends_bearer_authorization_header(self, adapter: SlackAdapter) -> None:
        """The Authorization header is Bearer <bot_token> for the template-driven path too."""
        with respx.mock() as router:
            route = router.post(_POST_MESSAGE_URL).mock(
                return_value=httpx.Response(200, json={"ok": True, "ts": "1.0"})
            )
            adapter.notify(self._strategy_paused_event())
            auth = route.calls[0].request.headers.get("authorization", "")
            assert auth == f"Bearer {_TEST_TOKEN}"

    def test_notify_without_attachments_makes_exactly_one_post(self, adapter: SlackAdapter) -> None:
        """A template with no attachments (short event) triggers only chat.postMessage."""
        with respx.mock(assert_all_called=False) as router:
            post_route = router.post(_POST_MESSAGE_URL).mock(
                return_value=httpx.Response(200, json={"ok": True, "ts": "1.0"})
            )
            upload_route = router.post(_GET_UPLOAD_URL).mock(
                return_value=httpx.Response(200, json={"ok": True})
            )
            adapter.notify(self._strategy_paused_event())
            assert post_route.call_count == 1
            assert upload_route.call_count == 0

    def test_notify_ok_false_body_raises(self, adapter: SlackAdapter) -> None:
        """The template-driven path surfaces Slack's HTTP-200-with-ok-false footgun."""
        with respx.mock() as router:
            router.post(_POST_MESSAGE_URL).mock(
                return_value=httpx.Response(200, json={"ok": False, "error": "channel_not_found"})
            )
            with pytest.raises(SlackNotifierError, match="rejected"):
                adapter.notify(self._strategy_paused_event())

    def test_notify_transport_failure_wraps(self, adapter: SlackAdapter) -> None:
        """A httpx transport error during notify() is wrapped as SlackNotifierError."""
        with respx.mock() as router:
            router.post(_POST_MESSAGE_URL).mock(side_effect=httpx.ConnectError("DNS failed"))
            with pytest.raises(SlackNotifierError, match="transport failure"):
                adapter.notify(self._strategy_paused_event())


class TestNotifyDailyDigestThreadedAttachment:
    """DailyTradeDigest is the attachment-bearing event — CSV lands as a threaded reply."""

    def _digest_event(self) -> DailyTradeDigest:
        """Return a minimal DailyTradeDigest event with a single winner + loser row."""
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

    def test_daily_digest_uploads_csv_via_three_step_flow(self, adapter: SlackAdapter) -> None:
        """DailyTradeDigest triggers getUploadURLExternal → PUT → completeUploadExternal."""
        signed_put_url = "https://files.slack.com/upload/signed-digest"
        parent_ts = "1700000000.000100"

        with respx.mock() as router:
            router.post(_POST_MESSAGE_URL).mock(
                return_value=httpx.Response(200, json={"ok": True, "ts": parent_ts})
            )
            step1 = router.post(_GET_UPLOAD_URL).mock(
                return_value=httpx.Response(
                    200,
                    json={"ok": True, "upload_url": signed_put_url, "file_id": "F888"},
                )
            )
            step2 = router.put(signed_put_url).mock(return_value=httpx.Response(200, text="OK"))
            step3 = router.post(_COMPLETE_UPLOAD_URL).mock(
                return_value=httpx.Response(200, json={"ok": True})
            )

            adapter.notify(self._digest_event())

            assert step1.call_count == 1
            assert step2.call_count == 1
            assert step3.call_count == 1

    def test_daily_digest_attachment_lands_in_parent_thread(self, adapter: SlackAdapter) -> None:
        """The completeUploadExternal payload carries thread_ts from the parent post."""
        signed_put_url = "https://files.slack.com/upload/signed-thread"
        parent_ts = "1700000000.000200"

        with respx.mock() as router:
            router.post(_POST_MESSAGE_URL).mock(
                return_value=httpx.Response(200, json={"ok": True, "ts": parent_ts})
            )
            router.post(_GET_UPLOAD_URL).mock(
                return_value=httpx.Response(
                    200,
                    json={"ok": True, "upload_url": signed_put_url, "file_id": "F777"},
                )
            )
            router.put(signed_put_url).mock(return_value=httpx.Response(200, text="OK"))
            step3 = router.post(_COMPLETE_UPLOAD_URL).mock(
                return_value=httpx.Response(200, json={"ok": True})
            )

            adapter.notify(self._digest_event())

            step3_payload = json.loads(step3.calls[0].request.content)
            # The thread_ts field pins the upload as a threaded reply
            # rather than a top-level channel message — keeps the timeline
            # a single card per event.
            assert step3_payload["thread_ts"] == parent_ts
            assert step3_payload["files"] == [{"id": "F777", "title": "digest-2026-08-23.csv"}]
            assert step3_payload["channel_id"] == _TEST_CHANNEL

    def test_daily_digest_step1_form_carries_filename_and_length(
        self, adapter: SlackAdapter
    ) -> None:
        """Step-1 form body carries filename + byte-length of the rendered CSV."""
        signed_put_url = "https://files.slack.com/upload/signed-form"
        with respx.mock() as router:
            router.post(_POST_MESSAGE_URL).mock(
                return_value=httpx.Response(200, json={"ok": True, "ts": "1.0"})
            )
            step1 = router.post(_GET_UPLOAD_URL).mock(
                return_value=httpx.Response(
                    200,
                    json={"ok": True, "upload_url": signed_put_url, "file_id": "F1"},
                )
            )
            router.put(signed_put_url).mock(return_value=httpx.Response(200, text="OK"))
            router.post(_COMPLETE_UPLOAD_URL).mock(
                return_value=httpx.Response(200, json={"ok": True})
            )

            adapter.notify(self._digest_event())

            step1_body = step1.calls[0].request.content.decode()
            # Template names the file `digest-<day>.csv` — must reach step 1.
            assert "filename=digest-2026-08-23.csv" in step1_body
            # Byte length is a positive integer — sanity assertion, no drift target.
            assert "length=" in step1_body


class TestAuthTest:
    """`auth_test()` powers `hermes health slack` — read-only, never sends messages."""

    def test_auth_test_returns_full_response_body_on_ok(self, adapter: SlackAdapter) -> None:
        """auth.test returns the parsed body dict with ok=True + team + user fields."""
        with respx.mock() as router:
            router.post(_AUTH_TEST_URL).mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "ok": True,
                        "team": "hermes-ws",
                        "user": "hermes_bot",
                        "team_id": "T01",
                        "user_id": "U01",
                        "url": "https://hermes-ws.slack.com/",
                    },
                )
            )
            result = adapter.auth_test()
            assert result["ok"] is True
            assert result["team"] == "hermes-ws"
            assert result["user"] == "hermes_bot"

    def test_auth_test_sends_bearer_authorization_header(self, adapter: SlackAdapter) -> None:
        """The Authorization header is Bearer <bot_token> — Slack's required auth mode."""
        with respx.mock() as router:
            route = router.post(_AUTH_TEST_URL).mock(
                return_value=httpx.Response(200, json={"ok": True, "team": "t", "user": "u"})
            )
            adapter.auth_test()
            auth = route.calls[0].request.headers.get("authorization", "")
            assert auth == f"Bearer {_TEST_TOKEN}"

    def test_auth_test_ok_false_raises_notifier_error(self, adapter: SlackAdapter) -> None:
        """HTTP 200 with ok=false (invalid_auth) raises — Slack's HTTP-200 footgun."""
        with respx.mock() as router:
            router.post(_AUTH_TEST_URL).mock(
                return_value=httpx.Response(200, json={"ok": False, "error": "invalid_auth"})
            )
            with pytest.raises(SlackNotifierError, match="invalid_auth"):
                adapter.auth_test()

    def test_auth_test_401_raises_notifier_error(self, adapter: SlackAdapter) -> None:
        """HTTP 401 raises with the status code in the message."""
        with respx.mock() as router:
            router.post(_AUTH_TEST_URL).mock(return_value=httpx.Response(401, text="Unauthorized"))
            with pytest.raises(SlackNotifierError, match="401"):
                adapter.auth_test()

    def test_auth_test_transport_failure_wraps(self, adapter: SlackAdapter) -> None:
        """A httpx transport error during auth.test is wrapped as SlackNotifierError."""
        with respx.mock() as router:
            router.post(_AUTH_TEST_URL).mock(side_effect=httpx.ConnectError("DNS failed"))
            with pytest.raises(SlackNotifierError, match="transport failure"):
                adapter.auth_test()
