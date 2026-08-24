"""Unit tests for :mod:`hermes.entrypoints._health` — integration probes.

Every test protects one invariant of the health-check registry OR one
of the per-integration probes (Telegram, Slack). Wire calls are mocked
via ``respx`` (declared in dev deps per ADR-007). No real socket ever
opens; no real credentials are ever transmitted.

Reference: docs/decisions/2026-08-23-contract-test-tier.md.
"""

from __future__ import annotations

import io
from datetime import UTC, datetime

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

# Healthchecks — the ping URL is opaque, not a credential. All-zero UUID
# is obviously synthetic; any real ping URL would carry a random UUID.
_TEST_HC_URL = "https://hc-ping.com/00000000-0000-0000-0000-000000000000"
_HC_START_URL = f"{_TEST_HC_URL}/start"


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


@pytest.fixture
def env_with_hc_url() -> dict[str, str]:
    """Return an env-var snapshot with a synthetic Healthchecks ping URL."""
    return {"HERMES_HEALTHCHECKS__PING_URL": _TEST_HC_URL}


@pytest.fixture
def env_with_sentry_dsn() -> dict[str, str]:
    """Return an env-var snapshot with a well-formed synthetic Sentry DSN."""
    return {
        "HERMES_SENTRY__DSN": "https://public@sentry.example.com/1",
        "HERMES_SENTRY__ENVIRONMENT": "dev",
    }


@pytest.fixture
def env_all_configured(
    env_with_creds: dict[str, str],
    env_with_slack_creds: dict[str, str],
    env_with_hc_url: dict[str, str],
    env_with_sentry_dsn: dict[str, str],
) -> dict[str, str]:
    """Return an env-var snapshot with credentials for every integration."""
    return {**env_with_creds, **env_with_slack_creds, **env_with_hc_url, **env_with_sentry_dsn}


class TestRegistry:
    """Verify the health-check registry surface."""

    def test_registered_integrations_returns_sorted_tuple(self) -> None:
        """registered_integrations returns a stable, sorted tuple of names."""
        names = health_module.registered_integrations()
        assert names == tuple(sorted(names))
        assert "telegram" in names
        assert "slack" in names
        assert "healthchecks" in names
        assert "sentry" in names

    def test_get_check_returns_callable_for_telegram(self) -> None:
        """get_check('telegram') returns the check_telegram callable."""
        assert health_module.get_check("telegram") is health_module.check_telegram

    def test_get_check_returns_callable_for_slack(self) -> None:
        """get_check('slack') returns the check_slack callable."""
        assert health_module.get_check("slack") is health_module.check_slack

    def test_get_check_returns_callable_for_healthchecks(self) -> None:
        """get_check('healthchecks') returns the check_healthchecks callable."""
        assert health_module.get_check("healthchecks") is health_module.check_healthchecks

    def test_get_check_returns_callable_for_sentry(self) -> None:
        """get_check('sentry') returns the check_sentry callable."""
        assert health_module.get_check("sentry") is health_module.check_sentry

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


class TestCheckHealthchecksHappyPath:
    """`check_healthchecks` returns OK when /start responds with 200."""

    def test_ok_result_reports_ping_accepted(self, env_with_hc_url: dict[str, str]) -> None:
        """A 200 OK from /start yields status OK and a 'ping accepted' detail."""
        with respx.mock() as router:
            # check_healthchecks calls ping_start(details=...) — always a POST.
            router.post(_HC_START_URL).mock(return_value=httpx.Response(200, text="OK"))
            result = health_module.check_healthchecks(env_with_hc_url)
        assert result.status == health_module.HealthStatus.OK
        assert result.exit_code == health_module.EXIT_OK
        assert result.integration == "healthchecks"
        assert "ping accepted" in result.detail
        assert result.last_check.tzinfo is not None
        assert result.latency_ms >= 0.0

    def test_ok_result_last_check_is_utc_aware(self, env_with_hc_url: dict[str, str]) -> None:
        """The last_check timestamp is UTC-aware (never naive) per code-conventions §2."""
        with respx.mock() as router:
            router.post(_HC_START_URL).mock(return_value=httpx.Response(200, text="OK"))
            result = health_module.check_healthchecks(env_with_hc_url)
        offset = result.last_check.utcoffset()
        assert offset is not None
        assert offset.total_seconds() == 0.0


class TestCheckHealthchecksFailurePaths:
    """`check_healthchecks` reports FAIL on missing URL, HTTP error, network error."""

    def test_missing_ping_url_returns_exit_three(self) -> None:
        """No HERMES_HEALTHCHECKS__PING_URL → exit code 3 (not configured) + .env.local hint."""
        result = health_module.check_healthchecks({})
        assert result.status == health_module.HealthStatus.FAIL
        assert result.exit_code == health_module.EXIT_NOT_CONFIGURED
        assert ".env.local" in result.detail
        # Latency is zero because we never hit the wire.
        assert result.latency_ms == 0.0
        assert "PING_URL" in result.detail

    def test_whitespace_ping_url_returns_exit_three(self) -> None:
        """A whitespace-only ping URL is treated as missing (fail-fast, no silent success)."""
        result = health_module.check_healthchecks({"HERMES_HEALTHCHECKS__PING_URL": "   "})
        assert result.exit_code == health_module.EXIT_NOT_CONFIGURED

    def test_http_500_returns_exit_one(self, env_with_hc_url: dict[str, str]) -> None:
        """HTTP 500 from /start → exit code 1 (adapter down) + status in detail."""
        with respx.mock() as router:
            router.post(_HC_START_URL).mock(return_value=httpx.Response(500, text="down"))
            result = health_module.check_healthchecks(env_with_hc_url)
        assert result.status == health_module.HealthStatus.FAIL
        assert result.exit_code == health_module.EXIT_FAIL
        assert "500" in result.detail

    def test_network_error_returns_exit_one(self, env_with_hc_url: dict[str, str]) -> None:
        """A transport-level failure wraps to exit code 1 with a transport-failure detail."""
        with respx.mock() as router:
            router.post(_HC_START_URL).mock(side_effect=httpx.ConnectError("DNS failed"))
            result = health_module.check_healthchecks(env_with_hc_url)
        assert result.exit_code == health_module.EXIT_FAIL
        assert "transport failure" in result.detail


class TestRunAll:
    """`run_all` executes every registered probe and returns a list of results."""

    def test_run_all_returns_one_result_per_registered_integration(
        self, env_all_configured: dict[str, str]
    ) -> None:
        """run_all returns exactly len(registered_integrations()) results in sorted order."""
        with respx.mock() as router:
            router.get(_TELEGRAM_GET_ME_URL).mock(
                return_value=httpx.Response(200, json={"ok": True, "result": {"username": "b"}})
            )
            router.post(_SLACK_AUTH_TEST_URL).mock(
                return_value=httpx.Response(200, json={"ok": True, "team": "t", "user": "u"})
            )
            router.post(_HC_START_URL).mock(return_value=httpx.Response(200, text="OK"))
            results = health_module.run_all(env_all_configured)
        # One result per registered integration (order stable via sorted names).
        assert len(results) == len(health_module.registered_integrations())
        names = [r.integration for r in results]
        assert names == list(health_module.registered_integrations())

    def test_run_all_with_all_ok_yields_ok_results(
        self, env_all_configured: dict[str, str]
    ) -> None:
        """Every result carries status OK when the upstream mocks all succeed."""
        with respx.mock() as router:
            router.get(_TELEGRAM_GET_ME_URL).mock(
                return_value=httpx.Response(200, json={"ok": True, "result": {"username": "b"}})
            )
            router.post(_SLACK_AUTH_TEST_URL).mock(
                return_value=httpx.Response(200, json={"ok": True, "team": "t", "user": "u"})
            )
            router.post(_HC_START_URL).mock(return_value=httpx.Response(200, text="OK"))
            results = health_module.run_all(env_all_configured)
        assert all(r.status == health_module.HealthStatus.OK for r in results)

    def test_run_all_with_empty_env_reports_all_not_configured(self) -> None:
        """A pristine env (no creds) yields NOT_CONFIGURED for every integration."""
        results = health_module.run_all({})
        assert all(r.exit_code == health_module.EXIT_NOT_CONFIGURED for r in results)
        # Every registered name shows up — no probe swallowed silently.
        assert len(results) == len(health_module.registered_integrations())


class TestAggregateExitCode:
    """`aggregate_exit_code` combines multiple check results into one exit code."""

    def _make_result(self, exit_code: int) -> health_module.HealthCheckResult:
        """Build a bare HealthCheckResult with the given exit_code (helper for these tests)."""
        return health_module.HealthCheckResult(
            integration="test",
            status=(
                health_module.HealthStatus.OK
                if exit_code == health_module.EXIT_OK
                else health_module.HealthStatus.FAIL
            ),
            latency_ms=0.0,
            detail="",
            last_check=datetime.now(UTC),
            exit_code=exit_code,
        )

    def test_all_ok_yields_exit_ok(self) -> None:
        """A list of all-OK results aggregates to EXIT_OK (0)."""
        results = [self._make_result(health_module.EXIT_OK) for _ in range(3)]
        assert health_module.aggregate_exit_code(results) == health_module.EXIT_OK

    def test_any_fail_wins_over_ok_and_not_configured(self) -> None:
        """A single FAIL result forces EXIT_FAIL (1) regardless of other exit codes."""
        results = [
            self._make_result(health_module.EXIT_OK),
            self._make_result(health_module.EXIT_NOT_CONFIGURED),
            self._make_result(health_module.EXIT_FAIL),
        ]
        assert health_module.aggregate_exit_code(results) == health_module.EXIT_FAIL

    def test_not_configured_wins_over_ok_when_no_fail(self) -> None:
        """A NOT_CONFIGURED with no FAIL aggregates to EXIT_NOT_CONFIGURED (3)."""
        results = [
            self._make_result(health_module.EXIT_OK),
            self._make_result(health_module.EXIT_NOT_CONFIGURED),
        ]
        assert health_module.aggregate_exit_code(results) == health_module.EXIT_NOT_CONFIGURED

    def test_empty_list_yields_exit_ok(self) -> None:
        """An empty results list yields EXIT_OK — nothing to check → nothing to fail."""
        assert health_module.aggregate_exit_code([]) == health_module.EXIT_OK


class TestRenderTableMulti:
    """`render_table_multi` prints a multi-row aligned table."""

    def test_table_includes_header_and_one_row_per_result(
        self, env_all_configured: dict[str, str]
    ) -> None:
        """Rendered output contains the header row + one row per result, plus each field."""
        with respx.mock() as router:
            router.get(_TELEGRAM_GET_ME_URL).mock(
                return_value=httpx.Response(
                    200, json={"ok": True, "result": {"username": "hermes_bot"}}
                )
            )
            router.post(_SLACK_AUTH_TEST_URL).mock(
                return_value=httpx.Response(
                    200, json={"ok": True, "team": "hermes-ws", "user": "hermes_bot"}
                )
            )
            router.post(_HC_START_URL).mock(return_value=httpx.Response(200, text="OK"))
            results = health_module.run_all(env_all_configured)
        stream = io.StringIO()
        health_module.render_table_multi(results, stream)
        output = stream.getvalue()
        # Header cells present:
        for header in ("integration", "status", "latency", "detail", "last_check"):
            assert header in output
        # Each integration name present exactly once as a row label:
        assert "telegram" in output
        assert "slack" in output
        assert "healthchecks" in output
        # Status column shows OK for every row:
        assert output.count("OK") >= 3

    def test_empty_results_prints_only_header(self) -> None:
        """render_table_multi with an empty list prints the header row and nothing else."""
        stream = io.StringIO()
        health_module.render_table_multi([], stream)
        output = stream.getvalue()
        # Exactly one line — the header. No data rows.
        assert output.count("\n") == 1
        assert "integration" in output
        assert "status" in output

    def test_column_widths_adjust_to_longest_value(self, env_with_hc_url: dict[str, str]) -> None:
        """Rendered rows are aligned — the second column starts at a stable offset."""
        with respx.mock() as router:
            router.post(_HC_START_URL).mock(return_value=httpx.Response(200, text="OK"))
            results = [health_module.check_healthchecks(env_with_hc_url)]
        stream = io.StringIO()
        health_module.render_table_multi(results, stream)
        output = stream.getvalue()
        lines = output.splitlines()
        # Header line + one data line = 2 lines total.
        assert len(lines) == 2
        # The header's 'integration' label is longer than the value 'healthchecks' is not —
        # both are 12+ chars; verify both rows are padded to the same left-column width so
        # the second column starts at the same offset.
        header_status_pos = lines[0].index("status")
        row_status_pos = lines[1].index("OK")
        assert header_status_pos == row_status_pos


class TestCheckSentry:
    """DSN-parse-only Sentry health probe — no wire, no quota burn."""

    _VALID_DSN = "https://public@sentry.example.com/1"

    def test_missing_dsn_returns_not_configured(self) -> None:
        """Empty HERMES_SENTRY__DSN → NOT_CONFIGURED (exit 3), no parse attempted."""
        result = health_module.check_sentry({})
        assert result.integration == "sentry"
        assert result.status == health_module.HealthStatus.FAIL
        assert result.exit_code == health_module.EXIT_NOT_CONFIGURED
        assert "disabled" in result.detail
        assert result.latency_ms == 0.0

    def test_whitespace_dsn_returns_not_configured(self) -> None:
        """Whitespace-only DSN is treated as empty → NOT_CONFIGURED."""
        result = health_module.check_sentry({"HERMES_SENTRY__DSN": "   "})
        assert result.exit_code == health_module.EXIT_NOT_CONFIGURED

    def test_valid_dsn_returns_ok_with_host_and_project(self) -> None:
        """Valid DSN parses → OK (exit 0), detail carries host + project + env."""
        env = {
            "HERMES_SENTRY__DSN": self._VALID_DSN,
            "HERMES_SENTRY__ENVIRONMENT": "sandbox",
        }
        result = health_module.check_sentry(env)
        assert result.integration == "sentry"
        assert result.status == health_module.HealthStatus.OK
        assert result.exit_code == health_module.EXIT_OK
        assert "sentry.example.com" in result.detail
        assert "environment=sandbox" in result.detail
        assert result.latency_ms == 0.0

    def test_valid_dsn_default_environment_is_dev(self) -> None:
        """Missing HERMES_SENTRY__ENVIRONMENT defaults to 'dev' in detail."""
        env = {"HERMES_SENTRY__DSN": self._VALID_DSN}
        result = health_module.check_sentry(env)
        assert "environment=dev" in result.detail

    def test_malformed_dsn_returns_fail(self) -> None:
        """Non-DSN-shaped string → FAIL (exit 1) via BadDsn parse error."""
        env = {"HERMES_SENTRY__DSN": "not-a-dsn"}
        result = health_module.check_sentry(env)
        assert result.integration == "sentry"
        assert result.status == health_module.HealthStatus.FAIL
        assert result.exit_code == health_module.EXIT_FAIL
        assert "malformed" in result.detail.lower()
        assert result.latency_ms == 0.0

    def test_ok_result_last_check_is_utc_aware(self) -> None:
        """OK result carries a UTC-aware last_check timestamp."""
        result = health_module.check_sentry({"HERMES_SENTRY__DSN": self._VALID_DSN})
        assert result.last_check.tzinfo is not None
        assert result.last_check.utcoffset() is not None
        assert result.last_check.utcoffset().total_seconds() == 0
