"""Unit tests for :class:`hermes.adapters.healthchecks.HealthchecksAdapter`.

Every test protects one invariant of the Healthchecks adapter contract.
Wire calls are mocked via ``respx`` (declared in dev deps per ADR-007).
No real socket ever opens; no real ping URLs are ever transmitted.

Test-only ping URLs use the all-zero UUID
(``00000000-0000-0000-0000-000000000000``) — obviously fake, no
low-entropy shape that a real ping monitor would issue. The URL is not
a credential (see task spec Part 6) but keeping it plainly synthetic
avoids any operator confusion.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from hermes.adapters._shared.rate_limiter import RateLimitConfig
from hermes.adapters.healthchecks import (
    HealthchecksAdapter,
    HealthchecksAdapterConfig,
    HealthchecksError,
)
from hermes.domain.errors import HermesError, InfraError

_TEST_UUID = "00000000-0000-0000-0000-000000000000"
_TEST_PING_URL = f"https://hc-ping.com/{_TEST_UUID}"
_FAIL_URL = f"{_TEST_PING_URL}/fail"
_START_URL = f"{_TEST_PING_URL}/start"


@pytest.fixture
def config() -> HealthchecksAdapterConfig:
    """Return a fixed :class:`HealthchecksAdapterConfig` for adapter construction."""
    return HealthchecksAdapterConfig(ping_url=_TEST_PING_URL)


@pytest.fixture
def adapter(config: HealthchecksAdapterConfig) -> HealthchecksAdapter:
    """Return a :class:`HealthchecksAdapter` wired with a plain httpx.Client.

    ``respx`` patches transport globally in the ``respx_mock`` context
    manager per test, so the client here does NOT need MockTransport.
    """
    return HealthchecksAdapter.create(config)


class TestHealthchecksAdapterConfig:
    """Verify wire-config invariants for :class:`HealthchecksAdapterConfig`."""

    def test_construction_with_valid_https_url_succeeds(self) -> None:
        """A well-formed https ping URL constructs without raising."""
        cfg = HealthchecksAdapterConfig(ping_url=_TEST_PING_URL)
        assert cfg.ping_url == _TEST_PING_URL
        assert cfg.timeout_sec == 5.0

    def test_empty_ping_url_rejected_with_pointer_to_enabled_guard(self) -> None:
        """An empty ping_url raises ValueError referencing the enabled guard."""
        with pytest.raises(ValueError, match="enabled"):
            HealthchecksAdapterConfig(ping_url="")

    def test_non_http_scheme_rejected(self) -> None:
        """A ping URL without http(s):// scheme is rejected at construction."""
        with pytest.raises(ValueError, match="must start with"):
            HealthchecksAdapterConfig(ping_url="ftp://hc-ping.com/deadbeef")

    def test_non_positive_timeout_rejected(self) -> None:
        """A timeout <= 0 is rejected — impossible to satisfy."""
        with pytest.raises(ValueError, match="timeout_sec"):
            HealthchecksAdapterConfig(ping_url=_TEST_PING_URL, timeout_sec=0)

    def test_from_env_loads_ping_url(self) -> None:
        """from_env returns config populated from HERMES_HEALTHCHECKS__PING_URL."""
        env = {"HERMES_HEALTHCHECKS__PING_URL": _TEST_PING_URL}
        cfg = HealthchecksAdapterConfig.from_env(env)
        assert cfg.ping_url == _TEST_PING_URL

    def test_from_env_raises_when_ping_url_missing(self) -> None:
        """Missing HERMES_HEALTHCHECKS__PING_URL raises HealthchecksError at load time."""
        with pytest.raises(HealthchecksError, match="PING_URL"):
            HealthchecksAdapterConfig.from_env({})

    def test_from_env_treats_whitespace_as_missing(self) -> None:
        """A whitespace-only env var is treated as missing (fail-fast, no silent success)."""
        env = {"HERMES_HEALTHCHECKS__PING_URL": "   "}
        with pytest.raises(HealthchecksError, match="PING_URL"):
            HealthchecksAdapterConfig.from_env(env)


class TestPingSuccessHappyPath:
    """Verify successful ``ping_success`` behaviour of the adapter."""

    def test_ping_success_without_details_uses_get(self, adapter: HealthchecksAdapter) -> None:
        """ping_success(None) sends exactly one bodiless GET to the raw ping URL."""
        with respx.mock(assert_all_called=True) as router:
            route = router.route().mock(return_value=httpx.Response(200, text="OK"))
            adapter.ping_success()
        assert [(c.request.method, str(c.request.url), c.request.content) for c in route.calls] == [
            ("GET", _TEST_PING_URL, b"")
        ]

    def test_ping_success_with_details_uses_post(self, adapter: HealthchecksAdapter) -> None:
        """ping_success('body') fires POST with the body encoded as UTF-8 text/plain."""
        with respx.mock(assert_all_called=True) as router:
            route = router.post(_TEST_PING_URL).mock(return_value=httpx.Response(200, text="OK"))
            adapter.ping_success(details="processed 42 signals")
        assert route.called
        sent = route.calls.last.request
        assert sent.content == b"processed 42 signals"
        assert sent.headers.get("content-type", "").startswith("text/plain")


class TestPingFail:
    """Verify ``ping_fail`` posts to /fail with the reason body."""

    def test_ping_fail_posts_reason_to_fail_endpoint(self, adapter: HealthchecksAdapter) -> None:
        """ping_fail(reason) POSTs the reason body to <ping-url>/fail."""
        with respx.mock(assert_all_called=True) as router:
            route = router.post(_FAIL_URL).mock(return_value=httpx.Response(200, text="OK"))
            adapter.ping_fail(reason="rollback fired")
        assert route.called
        assert route.calls.last.request.content == b"rollback fired"


class TestPingStart:
    """Verify ``ping_start`` posts to /start — the informational heartbeat used by health probes."""

    def test_ping_start_without_details_uses_get(self, adapter: HealthchecksAdapter) -> None:
        """ping_start(None) sends exactly one bodiless GET to <ping-url>/start."""
        with respx.mock(assert_all_called=True) as router:
            route = router.route().mock(return_value=httpx.Response(200, text="OK"))
            adapter.ping_start()
        assert [(c.request.method, str(c.request.url), c.request.content) for c in route.calls] == [
            ("GET", _START_URL, b"")
        ]

    def test_ping_start_with_details_uses_post(self, adapter: HealthchecksAdapter) -> None:
        """ping_start('body') fires POST to <ping-url>/start with the body payload."""
        with respx.mock(assert_all_called=True) as router:
            route = router.post(_START_URL).mock(return_value=httpx.Response(200, text="OK"))
            adapter.ping_start(details="hermes health probe")
        assert route.called
        assert route.calls.last.request.content == b"hermes health probe"


class TestPingFailurePaths:
    """The adapter raises :class:`HealthchecksError` on wire + HTTP failures."""

    def test_http_500_raises_with_status_and_excerpt(self, adapter: HealthchecksAdapter) -> None:
        """A 5xx response raises HealthchecksError carrying the status code + body excerpt."""
        with respx.mock() as router:
            router.get(_TEST_PING_URL).mock(
                return_value=httpx.Response(500, text="server exploded"),
            )
            with pytest.raises(HealthchecksError, match="500") as excinfo:
                adapter.ping_success()
        assert "server exploded" in excinfo.value.msg

    def test_http_400_on_start_raises_with_endpoint_label(
        self, adapter: HealthchecksAdapter
    ) -> None:
        """A 4xx from the /start endpoint mentions ping_start in the raised error."""
        with respx.mock() as router:
            router.get(_START_URL).mock(return_value=httpx.Response(400, text="bad ping"))
            with pytest.raises(HealthchecksError, match="ping_start"):
                adapter.ping_start()

    def test_transport_failure_raises_with_endpoint_label(
        self, adapter: HealthchecksAdapter
    ) -> None:
        """A ConnectError on ping_fail wraps to HealthchecksError with 'ping_fail' in the msg."""
        with respx.mock() as router:
            router.post(_FAIL_URL).mock(side_effect=httpx.ConnectError("DNS failed"))
            with pytest.raises(HealthchecksError, match="ping_fail"):
                adapter.ping_fail(reason="down")


class TestErrorHierarchy:
    """:class:`HealthchecksError` extends :class:`InfraError` → :class:`HermesError`."""

    def test_adapter_failure_is_caught_by_an_infra_error_handler(
        self, adapter: HealthchecksAdapter
    ) -> None:
        """A failed ping is caught by ``except InfraError`` and keeps its message."""
        with respx.mock() as router:
            router.get(_TEST_PING_URL).mock(return_value=httpx.Response(503, text="down"))
            with pytest.raises(InfraError) as excinfo:
                adapter.ping_success()
        assert excinfo.value.msg == "HTTP 503 from ping_success: down"

    def test_adapter_failure_is_caught_by_a_hermes_error_handler(
        self, adapter: HealthchecksAdapter
    ) -> None:
        """A transport failure is caught by ``except HermesError`` (the single root)."""
        with respx.mock() as router:
            router.get(_START_URL).mock(side_effect=httpx.ConnectError("DNS failed"))
            with pytest.raises(HermesError) as excinfo:
                adapter.ping_start()
        assert type(excinfo.value.__cause__) is httpx.ConnectError

    def test_str_includes_msg(self) -> None:
        """__str__ prefixes the message with 'Healthchecks error:' for grep-friendly logs."""
        err = HealthchecksError(msg="HTTP 500 from ping_success: server exploded")
        rendered = str(err)
        assert rendered.startswith("Healthchecks error:")
        assert "server exploded" in rendered


class TestAdapterLifecycle:
    """Adapter honours the close() contract and rate-limits pings."""

    def test_close_is_idempotent(self, adapter: HealthchecksAdapter) -> None:
        """Closing twice does not raise and leaves the client closed, so finally blocks are safe."""
        adapter.close()
        adapter.close()

        assert adapter.client.is_closed is True

    def test_rate_limiter_size_derived_from_config(self) -> None:
        """The internal rate-limit config carries the requested requests_per_minute."""
        cfg = HealthchecksAdapterConfig(
            ping_url=_TEST_PING_URL,
            rate_limit=RateLimitConfig(max_weight=5, window_seconds=60),
        )
        adapter = HealthchecksAdapter.create(cfg)
        try:
            assert adapter.limiter.config.max_weight == 5
        finally:
            adapter.close()
