"""Unit tests for :mod:`hermes.entrypoints._health` — integration probes.

Every test protects one invariant of the health-check registry OR one
of the per-integration probes (Telegram, Slack). Wire calls are mocked
via ``respx`` (declared in dev deps per ADR-007). No real socket ever
opens; no real credentials are ever transmitted.

Reference: docs/decisions/2026-08-23-contract-test-tier.md.
"""

from __future__ import annotations

import io

import httpx
import pytest
import respx

from hermes.entrypoints import _health as health_module

# Test-only fake credentials. Low-entropy so no future secret-scanner
# false-positives; the token patterns still match each vendor's shape
# enough that URL construction / header shaping stays realistic.
_TEST_TELEGRAM_TOKEN = "12345:test-tok"  # noqa: S105 — test-only bot token, never real
_TEST_TELEGRAM_CHAT = "-100test"
_TELEGRAM_GET_ME_URL = f"https://api.telegram.org/bot{_TEST_TELEGRAM_TOKEN}/getMe"

_TEST_SLACK_TOKEN = "xoxb-test"  # noqa: S105 — test-only bot token, never real
_TEST_SLACK_CHANNEL = "C0123ABCD"
_SLACK_AUTH_TEST_URL = "https://slack.com/api/auth.test"


@pytest.fixture
def env_with_creds() -> dict[str, str]:
    """Return an env-var snapshot with valid-looking Telegram creds."""
    return {
        "HERMES_TELEGRAM__BOT_TOKEN": _TEST_TELEGRAM_TOKEN,
        "HERMES_TELEGRAM__CHAT_ID": _TEST_TELEGRAM_CHAT,
    }


@pytest.fixture
def env_with_slack_creds() -> dict[str, str]:
    """Return an env-var snapshot with valid-looking Slack creds."""
    return {
        "HERMES_SLACK__BOT_TOKEN": _TEST_SLACK_TOKEN,
        "HERMES_SLACK__DEFAULT_CHANNEL": _TEST_SLACK_CHANNEL,
    }


class TestRegistry:
    """Verify the health-check registry surface."""

    def test_registered_integrations_returns_sorted_tuple(self) -> None:
        """registered_integrations returns a stable, sorted tuple of names."""
        names = health_module.registered_integrations()
        assert names == tuple(sorted(names))
        assert "telegram" in names
        assert "slack" in names

    def test_get_check_returns_callable_for_telegram(self) -> None:
        """get_check('telegram') returns the check_telegram callable."""
        assert health_module.get_check("telegram") is health_module.check_telegram

    def test_get_check_returns_callable_for_slack(self) -> None:
        """get_check('slack') returns the check_slack callable."""
        assert health_module.get_check("slack") is health_module.check_slack

    def test_get_check_returns_none_for_unknown_name(self) -> None:
        """get_check returns None (no KeyError) for an unregistered integration."""
        result = health_module.get_check("no-such-integration")
        # Two positive assertions: not-a-callable AND not either registered probe.
        # (The dispatcher relies on `is None` to skip; the shape guarantee is
        # what protects callers from silently receiving a wrong callable.)
        assert not callable(result), (
            f"unregistered integration must not yield a callable, got {result!r}"
        )
        assert result != health_module.check_telegram, (
            "unregistered integration must not resolve to the telegram probe"
        )
        assert result != health_module.check_slack, (
            "unregistered integration must not resolve to the slack probe"
        )


class TestCheckTelegramHappyPath:
    """`check_telegram` returns OK when getMe returns a valid bot."""

    def test_ok_result_carries_username_in_detail(self, env_with_creds: dict[str, str]) -> None:
        """A successful getMe response yields status OK and includes the bot username."""
        with respx.mock() as router:
            router.get(_TELEGRAM_GET_ME_URL).mock(
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
            result = health_module.check_telegram(env_with_creds)
        assert result.status == health_module.HealthStatus.OK
        assert result.exit_code == health_module.EXIT_OK
        assert result.integration == "telegram"
        assert "hermes_bot" in result.detail
        assert result.last_check.tzinfo is not None
        assert result.latency_ms >= 0.0

    def test_ok_result_last_check_is_utc_aware(self, env_with_creds: dict[str, str]) -> None:
        """The last_check timestamp is UTC-aware (never naive) per code-conventions §2."""
        with respx.mock() as router:
            router.get(_TELEGRAM_GET_ME_URL).mock(
                return_value=httpx.Response(200, json={"ok": True, "result": {"username": "u"}})
            )
            result = health_module.check_telegram(env_with_creds)
        # UTC-aware — utcoffset must exist and be zero (never None).
        offset = result.last_check.utcoffset()
        assert offset is not None
        assert offset.total_seconds() == 0.0


class TestCheckTelegramFailurePaths:
    """`check_telegram` reports FAIL on missing config, auth error, and network error."""

    def test_missing_bot_token_returns_exit_three(self) -> None:
        """No HERMES_TELEGRAM__BOT_TOKEN → exit code 3 (not configured) + .env.local hint."""
        result = health_module.check_telegram({})
        assert result.status == health_module.HealthStatus.FAIL
        assert result.exit_code == health_module.EXIT_NOT_CONFIGURED
        assert ".env.local" in result.detail
        # Latency is zero because we never hit the wire.
        assert result.latency_ms == 0.0

    def test_missing_chat_id_returns_exit_three(self) -> None:
        """Missing HERMES_TELEGRAM__CHAT_ID → exit code 3 (config error), not 1."""
        result = health_module.check_telegram({"HERMES_TELEGRAM__BOT_TOKEN": _TEST_TELEGRAM_TOKEN})
        assert result.exit_code == health_module.EXIT_NOT_CONFIGURED
        assert "CHAT_ID" in result.detail

    def test_auth_failure_401_returns_exit_one(self, env_with_creds: dict[str, str]) -> None:
        """HTTP 401 from getMe → exit code 1 (adapter down) + status in detail."""
        with respx.mock() as router:
            router.get(_TELEGRAM_GET_ME_URL).mock(
                return_value=httpx.Response(
                    401,
                    json={"ok": False, "error_code": 401, "description": "Unauthorized"},
                )
            )
            result = health_module.check_telegram(env_with_creds)
        assert result.status == health_module.HealthStatus.FAIL
        assert result.exit_code == health_module.EXIT_FAIL
        assert "401" in result.detail

    def test_network_error_returns_exit_one(self, env_with_creds: dict[str, str]) -> None:
        """A transport-level failure wraps to exit code 1 with a transport-failure detail."""
        with respx.mock() as router:
            router.get(_TELEGRAM_GET_ME_URL).mock(side_effect=httpx.ConnectError("DNS failed"))
            result = health_module.check_telegram(env_with_creds)
        assert result.exit_code == health_module.EXIT_FAIL
        assert "transport failure" in result.detail


class TestCheckSlackHappyPath:
    """`check_slack` returns OK when auth.test returns a valid workspace+user."""

    def test_ok_result_carries_workspace_and_bot_in_detail(
        self, env_with_slack_creds: dict[str, str]
    ) -> None:
        """A successful auth.test yields status OK and includes workspace + bot user."""
        with respx.mock() as router:
            router.post(_SLACK_AUTH_TEST_URL).mock(
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
            result = health_module.check_slack(env_with_slack_creds)
        assert result.status == health_module.HealthStatus.OK
        assert result.exit_code == health_module.EXIT_OK
        assert result.integration == "slack"
        # Both fields (team + user) must land in the detail for operator triage.
        assert "hermes-ws" in result.detail
        assert "hermes_bot" in result.detail
        assert result.last_check.tzinfo is not None
        assert result.latency_ms >= 0.0

    def test_ok_result_last_check_is_utc_aware(self, env_with_slack_creds: dict[str, str]) -> None:
        """The last_check timestamp is UTC-aware (never naive) per code-conventions §2."""
        with respx.mock() as router:
            router.post(_SLACK_AUTH_TEST_URL).mock(
                return_value=httpx.Response(200, json={"ok": True, "team": "t", "user": "u"})
            )
            result = health_module.check_slack(env_with_slack_creds)
        # UTC-aware — utcoffset must exist and be zero (never None).
        offset = result.last_check.utcoffset()
        assert offset is not None
        assert offset.total_seconds() == 0.0

    def test_ok_result_missing_optional_fields_uses_placeholders(
        self, env_with_slack_creds: dict[str, str]
    ) -> None:
        """An `ok=true` body without team/user falls back to <unknown> placeholders."""
        with respx.mock() as router:
            router.post(_SLACK_AUTH_TEST_URL).mock(
                return_value=httpx.Response(200, json={"ok": True})
            )
            result = health_module.check_slack(env_with_slack_creds)
        assert result.status == health_module.HealthStatus.OK
        # Placeholder tokens confirm the probe never crashes when Slack
        # returns a truncated body (defensive against upstream schema drift).
        assert "<unknown>" in result.detail


class TestCheckSlackFailurePaths:
    """`check_slack` reports FAIL on missing config, auth error, and network error."""

    def test_missing_bot_token_returns_exit_three(self) -> None:
        """No HERMES_SLACK__BOT_TOKEN → exit code 3 (not configured) + .env.local hint."""
        result = health_module.check_slack({})
        assert result.status == health_module.HealthStatus.FAIL
        assert result.exit_code == health_module.EXIT_NOT_CONFIGURED
        assert ".env.local" in result.detail
        # Latency is zero because we never hit the wire.
        assert result.latency_ms == 0.0
        assert "BOT_TOKEN" in result.detail

    def test_missing_default_channel_returns_exit_three(self) -> None:
        """Missing HERMES_SLACK__DEFAULT_CHANNEL → exit code 3 (config error), not 1."""
        result = health_module.check_slack({"HERMES_SLACK__BOT_TOKEN": _TEST_SLACK_TOKEN})
        assert result.exit_code == health_module.EXIT_NOT_CONFIGURED
        assert "DEFAULT_CHANNEL" in result.detail

    def test_whitespace_bot_token_returns_exit_three(self) -> None:
        """A whitespace-only bot token is treated as missing (fail-fast, no silent success)."""
        result = health_module.check_slack(
            {
                "HERMES_SLACK__BOT_TOKEN": "   ",
                "HERMES_SLACK__DEFAULT_CHANNEL": _TEST_SLACK_CHANNEL,
            }
        )
        assert result.exit_code == health_module.EXIT_NOT_CONFIGURED

    def test_auth_failure_401_returns_exit_one(self, env_with_slack_creds: dict[str, str]) -> None:
        """HTTP 401 from auth.test → exit code 1 (adapter down) + status in detail."""
        with respx.mock() as router:
            router.post(_SLACK_AUTH_TEST_URL).mock(
                return_value=httpx.Response(401, text="Unauthorized")
            )
            result = health_module.check_slack(env_with_slack_creds)
        assert result.status == health_module.HealthStatus.FAIL
        assert result.exit_code == health_module.EXIT_FAIL
        assert "401" in result.detail

    def test_ok_false_body_returns_exit_one(self, env_with_slack_creds: dict[str, str]) -> None:
        """HTTP 200 with `ok=false` body → exit code 1 (Slack's HTTP-200 footgun).

        Slack returns 200 with ``{"ok": false, "error": "invalid_auth"}``
        for bad tokens — a naive response.raise_for_status() would let
        the probe report OK. The adapter's response-validation MUST
        surface this as FAIL.
        """
        with respx.mock() as router:
            router.post(_SLACK_AUTH_TEST_URL).mock(
                return_value=httpx.Response(200, json={"ok": False, "error": "invalid_auth"})
            )
            result = health_module.check_slack(env_with_slack_creds)
        assert result.status == health_module.HealthStatus.FAIL
        assert result.exit_code == health_module.EXIT_FAIL
        # The Slack error code must appear in the detail so an operator
        # reading the CLI output can act without opening the wire log.
        assert "invalid_auth" in result.detail

    def test_network_error_returns_exit_one(self, env_with_slack_creds: dict[str, str]) -> None:
        """A transport-level failure wraps to exit code 1 with a transport-failure detail."""
        with respx.mock() as router:
            router.post(_SLACK_AUTH_TEST_URL).mock(side_effect=httpx.ConnectError("DNS failed"))
            result = health_module.check_slack(env_with_slack_creds)
        assert result.exit_code == health_module.EXIT_FAIL
        assert "transport failure" in result.detail


class TestRenderTable:
    """`render_table` prints a two-column table with every result field."""

    def test_table_contains_every_field(self, env_with_creds: dict[str, str]) -> None:
        """The rendered table includes integration, status, latency, detail, and timestamp."""
        with respx.mock() as router:
            router.get(_TELEGRAM_GET_ME_URL).mock(
                return_value=httpx.Response(
                    200, json={"ok": True, "result": {"username": "hermes_bot"}}
                )
            )
            result = health_module.check_telegram(env_with_creds)
        stream = io.StringIO()
        health_module.render_table(result, stream)
        output = stream.getvalue()
        assert "integration" in output
        assert "telegram" in output
        assert "status" in output
        assert "OK" in output
        assert "latency_ms" in output
        assert "last_check" in output
        assert "hermes_bot" in output

    def test_table_contains_every_field_for_slack(
        self, env_with_slack_creds: dict[str, str]
    ) -> None:
        """The rendered table renders the slack probe's fields identically."""
        with respx.mock() as router:
            router.post(_SLACK_AUTH_TEST_URL).mock(
                return_value=httpx.Response(
                    200, json={"ok": True, "team": "hermes-ws", "user": "hermes_bot"}
                )
            )
            result = health_module.check_slack(env_with_slack_creds)
        stream = io.StringIO()
        health_module.render_table(result, stream)
        output = stream.getvalue()
        assert "integration" in output
        assert "slack" in output
        assert "status" in output
        assert "OK" in output
        assert "hermes-ws" in output
        assert "hermes_bot" in output
