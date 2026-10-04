"""Unit tests for :mod:`hermes.infrastructure.sentry_integration`.

Every test protects one invariant of the Sentry SDK wrapper:

- ``initialize`` is a silent no-op when the DSN is empty (integration disabled).
- ``initialize`` passes hermes-safe defaults to :func:`sentry_sdk.init`.
- ``initialize`` never raises — SDK failures are caught + logged.
- ``capture`` ships exceptions with optional structured context via a
  short-lived scope; SDK failures are swallowed.
- ``set_correlation_id`` tags the current scope; SDK failures are swallowed.
- Error events carry no credential held in a local variable (#506).

All calls to :mod:`sentry_sdk` are monkeypatched — no real network wire.

Reference: docs/guides/ai-authored-tests.md.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx
import sentry_sdk
import sentry_sdk.envelope
import sentry_sdk.transport

from hermes.adapters.notifier import TelegramAdapter, TelegramConfig, TelegramNotifierError
from hermes.infrastructure import sentry_integration


class TestInitializeDisabled:
    """When DSN is empty, ``initialize`` returns without touching the SDK."""

    def test_empty_dsn_is_silent_noop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Empty DSN → :func:`sentry_sdk.init` is NEVER called."""
        calls: list[dict[str, Any]] = []

        def _spy_init(**kwargs: Any) -> None:
            calls.append(kwargs)

        monkeypatch.setattr(sentry_integration.sentry_sdk, "init", _spy_init)
        sentry_integration.initialize(dsn="", environment="dev")
        assert calls == []


class TestInitializeEnabled:
    """When DSN is present, ``initialize`` calls the SDK with hermes defaults."""

    def test_valid_dsn_invokes_init_with_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Non-empty DSN → :func:`sentry_sdk.init` invoked with expected kwargs."""
        captured: dict[str, Any] = {}

        def _spy_init(**kwargs: Any) -> None:
            captured.update(kwargs)

        monkeypatch.setattr(sentry_integration.sentry_sdk, "init", _spy_init)
        sentry_integration.initialize(
            dsn="https://public@sentry.example.com/1",
            environment="sandbox",
        )
        assert captured["dsn"] == "https://public@sentry.example.com/1"
        assert captured["environment"] == "sandbox"
        assert captured["send_default_pii"] is False
        assert captured["traces_sample_rate"] == 0.0
        assert captured["default_integrations"] is False
        assert captured["integrations"] == []

    def test_init_failure_is_swallowed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """SDK init raising → ``initialize`` catches + does not re-raise."""

        def _boom_init(**_kwargs: Any) -> None:
            raise RuntimeError("simulated SDK init failure")

        monkeypatch.setattr(sentry_integration.sentry_sdk, "init", _boom_init)
        sentry_integration.initialize(
            dsn="https://public@sentry.example.com/1",
            environment="dev",
        )


class TestCaptureHappyPath:
    """Successful ``capture`` invokes :func:`sentry_sdk.capture_exception`."""

    def test_capture_without_extra_calls_capture_exception(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``extra=None`` path calls capture_exception directly (no scope push)."""
        captured: list[Exception] = []

        def _spy_capture(exc: Exception) -> None:
            captured.append(exc)

        monkeypatch.setattr(sentry_integration.sentry_sdk, "capture_exception", _spy_capture)
        error = ValueError("boom")
        sentry_integration.capture(error)
        assert captured == [error]

    def test_capture_with_extra_pushes_scope_with_context(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``extra`` payload is set via a per-capture scope, not the global one."""
        captured: list[Exception] = []
        context_calls: list[tuple[str, dict[str, Any]]] = []

        class _StubScope:
            def set_context(self, key: str, value: dict[str, Any]) -> None:
                context_calls.append((key, value))

        class _StubScopeCM:
            def __enter__(self) -> _StubScope:
                return _StubScope()

            def __exit__(self, *_args: object) -> None:
                return None

        def _spy_capture(exc: Exception) -> None:
            captured.append(exc)

        monkeypatch.setattr(sentry_integration.sentry_sdk, "capture_exception", _spy_capture)

        def _push_scope() -> _StubScopeCM:
            return _StubScopeCM()

        monkeypatch.setattr(sentry_integration.sentry_sdk, "push_scope", _push_scope)

        error = RuntimeError("kaboom")
        sentry_integration.capture(error, extra={"adapter": "telegram", "retries": 2})

        assert captured == [error]
        assert context_calls == [("structured_context", {"adapter": "telegram", "retries": 2})]


class TestCaptureSwallowsSdkFailures:
    """SDK errors inside ``capture`` never propagate to the caller."""

    def test_capture_exception_error_is_swallowed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """capture_exception raising → caller sees no exception."""

        def _boom_capture(_exc: Exception) -> None:
            raise RuntimeError("SDK crashed during capture")

        monkeypatch.setattr(sentry_integration.sentry_sdk, "capture_exception", _boom_capture)
        sentry_integration.capture(ValueError("original error"))


class TestSetCorrelationId:
    """``set_correlation_id`` tags the current scope; errors swallowed."""

    def test_set_correlation_id_calls_set_tag(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The correlation_id lands as a tag via :func:`sentry_sdk.set_tag`."""
        tags: list[tuple[str, str]] = []

        def _spy_set_tag(key: str, value: str) -> None:
            tags.append((key, value))

        monkeypatch.setattr(sentry_integration.sentry_sdk, "set_tag", _spy_set_tag)
        sentry_integration.set_correlation_id("ABC123XYZ")
        assert tags == [("correlation_id", "ABC123XYZ")]

    def test_set_correlation_id_swallows_sdk_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """set_tag raising → caller sees no exception."""

        def _boom_set_tag(_key: str, _value: str) -> None:
            raise RuntimeError("SDK crashed setting tag")

        monkeypatch.setattr(sentry_integration.sentry_sdk, "set_tag", _boom_set_tag)
        sentry_integration.set_correlation_id("XYZ789")


class _RecordingTransport(sentry_sdk.transport.Transport):
    """Keeps every envelope the SDK would send, serialised, in memory."""

    def __init__(self, options: dict[str, Any] | None = None) -> None:
        super().__init__(options)
        self.payloads: list[str] = []

    def capture_envelope(self, envelope: sentry_sdk.envelope.Envelope) -> None:
        self.payloads.append(envelope.serialize().decode("utf-8", errors="replace"))


class TestEventsCarryNoCredentials:
    """An error event never carries a credential held in a local variable (#506)."""

    def test_initialize_turns_off_local_variables(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``initialize`` passes ``include_local_variables=False`` to the SDK."""
        captured: dict[str, Any] = {}

        def _spy_init(**kwargs: Any) -> None:
            captured.update(kwargs)

        monkeypatch.setattr(sentry_integration.sentry_sdk, "init", _spy_init)
        sentry_integration.initialize(dsn="https://public@sentry.example.com/1", environment="dev")
        assert captured["include_local_variables"] is False

    def test_telegram_failure_event_omits_the_bot_token(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A failed Telegram send, captured to Sentry, ships no bot token.

        The adapter builds ``url = f"/bot{token}/sendMessage"`` as a local.
        The real SDK serialises the event through a recording transport
        (no network); the token must not appear anywhere in it.
        """
        token = "987654:SECRET-TOKEN-VALUE"  # noqa: S105  # gitleaks:allow
        transport = _RecordingTransport()
        real_init = sentry_sdk.init

        def _init_with_transport(**kwargs: Any) -> Any:
            return real_init(transport=transport, **kwargs)

        monkeypatch.setattr(sentry_integration.sentry_sdk, "init", _init_with_transport)
        sentry_integration.initialize(dsn="https://public@sentry.example.com/1", environment="dev")
        adapter = TelegramAdapter(
            config=TelegramConfig(bot_token=token, chat_id="-100123"),
            client=httpx.Client(base_url="https://api.telegram.org", timeout=5.0),
        )
        try:
            with respx.mock() as router:
                router.post(f"https://api.telegram.org/bot{token}/sendMessage").mock(
                    return_value=httpx.Response(500, json={"ok": False})
                )
                with pytest.raises(TelegramNotifierError) as raised:
                    adapter.notify_urgent(
                        event="deploy.done",
                        correlation_id="01HKQMZXYZ0123456789ABCDEF",
                        message="deploy shipped",
                    )
            sentry_integration.capture(raised.value)
            sentry_sdk.flush()
        finally:
            real_init(dsn=None)

        assert len(transport.payloads) == 1
        assert token not in transport.payloads[0]
