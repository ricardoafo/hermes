"""Unit tests for :mod:`hermes.infrastructure.sentry_integration`.

Every test protects one rule of the Sentry SDK wrapper, as documented in the
module docstring:

- ``initialize`` is a silent no-op when the DSN is empty (integration disabled).
- ``initialize`` passes hermes-safe defaults to :func:`sentry_sdk.init`.
- ``initialize``, ``capture`` and ``set_correlation_id`` never raise: an SDK
  failure is logged as a structured warning instead.
- ``capture`` ships the exception, with ``extra`` under a per-capture
  ``structured_context`` that does not leak into the next event.
- ``set_correlation_id`` tags later events with the correlation ID.
- Error events carry no credential held in a local variable (#506).

Where the rule is about what reaches Sentry, the real SDK runs and its events
are kept in memory by a recording transport, so no test touches the network.
Where the rule is about the options hermes hands the SDK, or about an SDK
crash, the SDK function is replaced at the module boundary.

Reference: docs/guides/ai-authored-tests.md.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx
import pytest
import respx
import sentry_sdk
import sentry_sdk.envelope
import sentry_sdk.transport
from structlog.testing import capture_logs

from hermes.adapters.notifier import TelegramAdapter, TelegramConfig, TelegramNotifierError
from hermes.infrastructure import sentry_integration

_DSN = "https://public@sentry.example.com/1"


class _RecordingTransport(sentry_sdk.transport.Transport):
    """Keeps every envelope the SDK would send, in memory."""

    def __init__(self, options: dict[str, Any] | None = None) -> None:
        super().__init__(options)
        self.envelopes: list[sentry_sdk.envelope.Envelope] = []

    def capture_envelope(self, envelope: sentry_sdk.envelope.Envelope) -> None:
        self.envelopes.append(envelope)

    def events(self) -> list[dict[str, Any]]:
        """Return the error events received so far."""
        return [event for env in self.envelopes if (event := env.get_event()) is not None]


@pytest.fixture
def recording_sentry(monkeypatch: pytest.MonkeyPatch) -> Iterator[_RecordingTransport]:
    """Initialise the real SDK through ``initialize`` with an in-memory transport."""
    transport = _RecordingTransport()
    real_init = sentry_sdk.init

    def _init_with_transport(**kwargs: Any) -> Any:
        return real_init(transport=transport, **kwargs)

    monkeypatch.setattr(sentry_integration.sentry_sdk, "init", _init_with_transport)
    with sentry_sdk.isolation_scope():
        sentry_integration.initialize(dsn=_DSN, environment="dev")
        try:
            yield transport
        finally:
            # A bare init(dsn=None) turns on Sentry's default integrations,
            # and their patches outlive the client: the threading one then
            # assigns __traceback__ on frozen-dataclass errors in every later
            # test of this worker (test_notifier_outage_does_not_stop_the_loop).
            real_init(dsn=None, default_integrations=False)
    assert "threading" not in sentry_sdk.integrations._installed_integrations


def _spy_init(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def _spy(**kwargs: Any) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(sentry_integration.sentry_sdk, "init", _spy)
    return calls


class TestInitialize:
    """``initialize`` configures the SDK with hermes-safe options, or not at all."""

    def test_empty_dsn_never_initialises_the_sdk(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An empty DSN disables Sentry: :func:`sentry_sdk.init` is never called."""
        calls = _spy_init(monkeypatch)

        sentry_integration.initialize(dsn="", environment="dev")

        assert calls == []

    def test_empty_dsn_logs_nothing(self) -> None:
        """A disabled Sentry stays silent, so a fresh clone does not log on every command."""
        with capture_logs() as logs:
            sentry_integration.initialize(dsn="", environment="dev")

        assert logs == []

    def test_dsn_initialises_the_sdk_with_exactly_the_safe_options(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A DSN initialises the SDK once, with no PII, no locals, no traces, no integrations."""
        calls = _spy_init(monkeypatch)

        sentry_integration.initialize(dsn=_DSN, environment="sandbox")

        assert calls == [
            {
                "dsn": _DSN,
                "environment": "sandbox",
                "send_default_pii": False,
                "include_local_variables": False,
                "traces_sample_rate": 0.0,
                "default_integrations": False,
                "integrations": [],
            }
        ]

    def test_sdk_init_failure_is_logged_not_raised(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An SDK init crash becomes one ``sentry.init.failed`` warning naming the error."""

        def _boom_init(**_kwargs: Any) -> None:
            raise RuntimeError("simulated SDK init failure")

        monkeypatch.setattr(sentry_integration.sentry_sdk, "init", _boom_init)

        with capture_logs() as logs:
            sentry_integration.initialize(dsn=_DSN, environment="prod")

        assert logs == [
            {
                "event": "sentry.init.failed",
                "log_level": "warning",
                "error": "simulated SDK init failure",
                "error_type": "RuntimeError",
                "environment": "prod",
            }
        ]


class TestCapture:
    """``capture`` ships the exception and its structured context to Sentry."""

    def test_capture_ships_the_exception(self, recording_sentry: _RecordingTransport) -> None:
        """A captured exception reaches Sentry as one event with its type and message."""
        sentry_integration.capture(ValueError("price feed stale"))
        sentry_sdk.flush()

        [event] = recording_sentry.events()
        [exc] = event["exception"]["values"]
        assert (exc["type"], exc["value"]) == ("ValueError", "price feed stale")

    def test_capture_tags_the_event_with_the_environment(
        self, recording_sentry: _RecordingTransport
    ) -> None:
        """Events carry the environment passed to ``initialize`` so the UI can filter."""
        sentry_integration.capture(ValueError("boom"))
        sentry_sdk.flush()

        [event] = recording_sentry.events()
        assert event["environment"] == "dev"

    def test_extra_lands_under_structured_context(
        self, recording_sentry: _RecordingTransport
    ) -> None:
        """``extra`` is attached to the event under the ``structured_context`` key."""
        sentry_integration.capture(
            RuntimeError("kaboom"), extra={"adapter": "telegram", "retry": 2}
        )
        sentry_sdk.flush()

        [event] = recording_sentry.events()
        assert event["contexts"]["structured_context"] == {"adapter": "telegram", "retry": 2}

    def test_extra_does_not_leak_into_the_next_event(
        self, recording_sentry: _RecordingTransport
    ) -> None:
        """Context from one capture is gone from the next capture without ``extra``."""
        sentry_integration.capture(RuntimeError("first"), extra={"adapter": "telegram"})
        sentry_integration.capture(RuntimeError("second"))
        sentry_sdk.flush()

        first, second = recording_sentry.events()
        assert "structured_context" in first["contexts"]
        assert "structured_context" not in second.get("contexts", {})

    def test_sdk_capture_failure_is_logged_not_raised(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An SDK crash during capture becomes a warning naming both errors."""

        def _boom_capture(_exc: Exception) -> None:
            raise RuntimeError("SDK crashed during capture")

        monkeypatch.setattr(sentry_integration.sentry_sdk, "capture_exception", _boom_capture)

        with capture_logs() as logs:
            sentry_integration.capture(ValueError("original error"))

        assert logs == [
            {
                "event": "sentry.capture.failed",
                "log_level": "warning",
                "error": "SDK crashed during capture",
                "error_type": "RuntimeError",
                "original_error": "ValueError",
            }
        ]


class TestSetCorrelationId:
    """``set_correlation_id`` tags later events; SDK failures are logged."""

    def test_later_events_carry_the_correlation_id_tag(
        self, recording_sentry: _RecordingTransport
    ) -> None:
        """An event captured after the call carries ``correlation_id=<value>`` as a tag."""
        sentry_integration.set_correlation_id("01HKQMZXYZ0123456789ABCDEF")
        sentry_integration.capture(ValueError("boom"))
        sentry_sdk.flush()

        [event] = recording_sentry.events()
        assert event["tags"]["correlation_id"] == "01HKQMZXYZ0123456789ABCDEF"

    def test_sdk_set_tag_failure_is_logged_not_raised(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An SDK crash while tagging becomes one ``sentry.set_correlation_id.failed`` warning."""

        def _boom_set_tag(_key: str, _value: str) -> None:
            raise RuntimeError("SDK crashed setting tag")

        monkeypatch.setattr(sentry_integration.sentry_sdk, "set_tag", _boom_set_tag)

        with capture_logs() as logs:
            sentry_integration.set_correlation_id("XYZ789")

        assert logs == [
            {
                "event": "sentry.set_correlation_id.failed",
                "log_level": "warning",
                "error": "SDK crashed setting tag",
                "error_type": "RuntimeError",
            }
        ]


class TestEventsCarryNoCredentials:
    """An error event never carries a credential held in a local variable (#506)."""

    def test_telegram_failure_event_omits_the_bot_token(
        self, recording_sentry: _RecordingTransport
    ) -> None:
        """A failed Telegram send, captured to Sentry, ships no bot token.

        The adapter builds ``url = f"/bot{token}/sendMessage"`` as a local.
        The real SDK serialises the event through the recording transport
        (no network); the token must not appear anywhere in it.
        """
        token = "987654:SECRET-TOKEN-VALUE"  # noqa: S105  # gitleaks:allow
        adapter = TelegramAdapter(
            config=TelegramConfig(bot_token=token, chat_id="-100123"),
            client=httpx.Client(base_url="https://api.telegram.org", timeout=5.0),
        )
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

        [envelope] = recording_sentry.envelopes
        payload = envelope.serialize().decode("utf-8", errors="replace")
        assert "TelegramNotifierError" in payload
        assert token not in payload
