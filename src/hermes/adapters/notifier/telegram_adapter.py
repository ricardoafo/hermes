"""Telegram notifier adapter — urgent/terse alerts channel.

Implements :class:`hermes.ports.notifier.NotifierPort` against the Telegram
Bot HTTP API. Per the 2026-08-23 decisions-journal entry
(`docs/decisions/2026-08-23-slack-alongside-telegram.md`), Telegram carries
push-to-phone urgent signals only — short, actionable, correlation-tail
suffixed. Long-form / rich reports go to Slack, not here.

Design decisions
----------------

- **HTTP library**: raw ``httpx.Client`` against ``https://api.telegram.org``.
  We deliberately avoid the ``python-telegram-bot`` SDK: it drags its own
  event loop, its own retry logic, and its own type surface — none of which
  we need for two short-string emits. A ~40-line httpx wrapper also aligns
  with the code-conventions §5 anyio-on-asyncio policy (we don't spin up a
  competing loop).
- **Sync**: :class:`NotifierPort` methods are sync (no ``async def``). The
  application-layer router calls them from a sync context; we match that.
  If Phase 2 needs async fan-out, port + adapter migrate together.
- **Errors**: raises :class:`TelegramNotifierError` (extends
  :class:`hermes.domain.errors.InfraError`) on any wire, HTTP-status, or
  API-response failure. Never swallows silently — per code-conventions §2
  adapters raise, the application layer converts to :class:`Result`.
- **Rate limits** (issue #859 item 5): the shared transport
  (:mod:`hermes.adapters._shared.telegram_http`, issue #1187) waits out an
  HTTP 429 a bounded number of times within a whole-send cap, then raises;
  this adapter maps that to the same :class:`TelegramNotifierError` as any
  HTTP failure, so callers' ``InfraError`` isolation is unchanged. No queue:
  an undelivered message is not stored here (issue #854). The ``getMe``
  health probe never waits.
- **notify_report**: intentional no-op with a WARNING log line. Long-form
  content does not render well as a phone push (per decisions-journal
  entry); routing a report to Telegram is almost always a misuse.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, Literal

import httpx
import structlog

from hermes.adapters._shared.telegram_http import (
    ERROR_EXCERPT_MAX_CHARS,
    TelegramHttp,
    TelegramHttpError,
    TelegramHttpFailure,
)
from hermes.adapters.endpoint import (
    DEFAULT_TELEGRAM_API_BASE,
    TELEGRAM_API_BASE_ENV,
    api_base_from_env,
)
from hermes.application.messages import correlation_tail
from hermes.application.messages.eur_rate import EurRateLookup, resolve_eur_rate
from hermes.application.messages.telegram import telegram_template
from hermes.application.notification_router import is_event_loud
from hermes.domain.errors import InfraError
from hermes.domain.events.anomaly_detected import AnomalyDetected, AnomalySeverity
from hermes.domain.events.base import DomainEvent

__all__ = ["TelegramAdapter", "TelegramConfig", "TelegramNotifierError"]

_DEFAULT_TIMEOUT_SEC: Final[float] = 10.0

_log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class TelegramNotifierError(InfraError):
    """Telegram Bot API call failed at transport, HTTP, or API-response layer.

    Wraps wire-level (httpx) exceptions, non-2xx HTTP responses, and
    ``ok=false`` responses from the Bot API into one exception surface.

    Args:
        msg: Human-readable context — includes HTTP status, response body
            excerpt, or transport error message. Verbose because a Telegram
            outage during an incident is a second incident; the operator
            needs enough context to diagnose without a debugger.
    """

    msg: str

    def __str__(self) -> str:
        """Return the wrapped human-readable message."""
        return f"Telegram notifier error: {self.msg}"


@dataclass(frozen=True, slots=True)
class TelegramConfig:
    """Credentials for the Telegram Bot API.

    Loaded from environment variables via
    :meth:`TelegramConfig.from_env` — the adapter never accepts raw
    secrets from a YAML file (per AGENTS.md prohibition #6 + ADR-003 §04).

    Attributes:
        bot_token: The bot's HTTP API token issued by ``@BotFather``.
            Format: ``"<int>:<crockford-ish-base64>"``. Treated as opaque.
            Not in ``repr``.
        chat_id: Numeric chat ID or ``@channelname`` string identifying
            where messages go. Stored as ``str`` since the API accepts
            both int and str and pydantic would need a union anyway.
        api_base: The Bot API root. ``HERMES_TELEGRAM__API_BASE`` points a
            lower environment at a fake; unset, it is Telegram's own.
    """

    bot_token: str = field(repr=False)
    chat_id: str
    api_base: str = DEFAULT_TELEGRAM_API_BASE

    @classmethod
    def from_env(cls, env: dict[str, str]) -> TelegramConfig:
        """Build a :class:`TelegramConfig` from the given env-var mapping.

        Args:
            env: Environment snapshot. Pass ``os.environ`` in production;
                pass a controlled dict in tests so the ambient shell never
                leaks in.

        Returns:
            A validated :class:`TelegramConfig` instance.

        Raises:
            TelegramNotifierError: If either required env var is missing
                or empty. Fail-fast — the adapter is useless without
                credentials and we would rather see the error at process
                start than at first alert time.
            EndpointConfigError: ``HERMES_TELEGRAM__API_BASE`` is set but
                malformed or unsafe (see :mod:`hermes.adapters.endpoint`).
        """
        token = env.get("HERMES_TELEGRAM__BOT_TOKEN", "").strip()
        chat = env.get("HERMES_TELEGRAM__CHAT_ID", "").strip()
        if not token:
            raise TelegramNotifierError("HERMES_TELEGRAM__BOT_TOKEN is not set (or empty)")
        if not chat:
            raise TelegramNotifierError("HERMES_TELEGRAM__CHAT_ID is not set (or empty)")
        api_base = api_base_from_env(env, TELEGRAM_API_BASE_ENV, DEFAULT_TELEGRAM_API_BASE)
        return cls(bot_token=token, chat_id=chat, api_base=api_base)


@dataclass(slots=True)
class TelegramAdapter:
    """Concrete :class:`NotifierPort` for the Telegram urgent channel.

    Attributes:
        config: The Bot API credentials.
        client: Injected :class:`httpx.Client`. Lets tests supply a
            :class:`httpx.MockTransport` (or ``respx`` mock) without
            monkey-patching. Production callers construct with the
            default via :meth:`TelegramAdapter.create`.
        timeout_sec: Per-request timeout — Telegram Bot API is typically
            fast; a 10s ceiling catches wedged connections without
            starving the notification path.
        eur_rate: Optional lookup of the EUR rate of an event's quote
            currency. It must answer from memory (no network): with a rate
            the push shows prices in EUR, without one in the quote currency.
        sleep: Waits out a 429. Tests pass a fake so no real time passes.
        monotonic: Clock the 429 time cap is measured on.
    """

    config: TelegramConfig
    client: httpx.Client = field()
    timeout_sec: float = _DEFAULT_TIMEOUT_SEC
    eur_rate: EurRateLookup | None = None
    sleep: Callable[[float], None] = time.sleep
    monotonic: Callable[[], float] = time.monotonic

    @classmethod
    def create(
        cls,
        config: TelegramConfig,
        *,
        timeout_sec: float = _DEFAULT_TIMEOUT_SEC,
        eur_rate: EurRateLookup | None = None,
    ) -> TelegramAdapter:
        """Build an adapter with a default :class:`httpx.Client`.

        Prefer this factory in production wiring; tests should construct
        :class:`TelegramAdapter` directly with a :class:`httpx.MockTransport`
        so no real socket ever opens under pytest.

        Args:
            config: Bot API credentials.
            timeout_sec: Per-request timeout in seconds.
            eur_rate: Optional in-memory EUR rate lookup (see the class).

        Returns:
            A new adapter owning its :class:`httpx.Client`.
        """
        client = httpx.Client(base_url=config.api_base, timeout=timeout_sec)
        return cls(config=config, client=client, timeout_sec=timeout_sec, eur_rate=eur_rate)

    def close(self) -> None:
        """Release the underlying :class:`httpx.Client` resources.

        Safe to call multiple times.
        """
        self.client.close()

    def notify(self, event: DomainEvent) -> None:
        """Render ``event`` through :func:`telegram_template` and post to Telegram.

        This is the preferred entry point for domain-event notifications.
        The template is looked up in the application-layer registry
        (:mod:`hermes.application.messages.telegram`); adapter code stays
        thin — no formatting decisions live here. The message rings the
        phone only when the router says the event is loud (down, P0,
        rejected order); otherwise it is sent with ``disable_notification``.

        Args:
            event: A concrete :class:`DomainEvent` instance.

        Raises:
            TelegramNotifierError: On transport failure, non-2xx HTTP
                status (including 429 rate-limit and 401/403 auth), or
                ``ok=false`` in the API response body.
            ValueError: If no template is registered for ``type(event)``
                (bubbled from :func:`telegram_template`).
        """
        text = telegram_template(event, eur_rate=resolve_eur_rate(self.eur_rate, event))
        payload = {
            "chat_id": self.config.chat_id,
            "text": text,
            "parse_mode": "MarkdownV2",
            "disable_web_page_preview": True,
            "disable_notification": not is_event_loud(event),
        }
        self._call("sendMessage", json=payload)

    def notify_urgent(self, *, event: str, correlation_id: str, message: str) -> None:
        """Post an urgent short message to the configured Telegram chat.

        Prefer :meth:`notify` (which takes a ``DomainEvent``) — this
        wrapper exists for compatibility with PR #92 callers and will be
        removed once the router migration lands. It constructs a synthetic
        :class:`AnomalyDetected` event (CRITICAL severity, ``event`` as
        source, ``message`` as reason) so the same template path renders
        the payload.

        Args:
            event: Dotted-namespace event name (e.g.
                ``"rollback.fired"``). Threaded into the synthetic
                ``AnomalyDetected.source`` field.
            correlation_id: Full ULID; only the last 6 chars appear in
                the ping (per ADR-008 § Correlation ID convention).
            message: Short human-readable body. Threaded into the
                synthetic ``AnomalyDetected.reason`` field.

        Raises:
            TelegramNotifierError: On transport failure, non-2xx HTTP
                status (including 429 rate-limit and 401/403 auth), or
                ``ok=false`` in the API response body.
        """
        synthetic = AnomalyDetected(
            occurred_at=datetime.now(UTC),
            correlation_id=correlation_id,
            source=event,
            severity=AnomalySeverity.CRITICAL,
            reason=message,
        )
        self.notify(synthetic)

    def notify_report(
        self,
        *,
        event: str,
        correlation_id: str,
        title: str,
        body: str,
        attachments: list[Path] | None = None,
    ) -> None:
        """No-op for the Telegram channel — logs a warning and returns.

        Per the 2026-08-23 decisions-journal entry, rich long-form
        content lives on Slack. Routing a report here is almost always
        a misuse (e.g., a router misclassification). We log at WARNING
        so the operator can grep journald and fix the routing table.

        Args:
            event: Dotted-namespace event name (unused).
            correlation_id: Correlation ULID (only tail is logged).
            title: Report title (unused).
            body: Report body (unused).
            attachments: Optional artifact paths (unused).
        """
        _ = title, body, attachments  # explicit unused-arg acknowledgement
        # NOTE: structlog uses the first positional arg AS the ``event`` key,
        # so we must NOT also pass ``event=...`` as a kwarg — that raises
        # "got multiple values for argument 'event'". Pass under a different
        # name (``event_name``) instead.
        _log.warning(
            "telegram.notify_report.noop",
            event_name=event,
            correlation_tail=correlation_tail(correlation_id),
            reason="telegram channel is urgent-only per decisions journal",
        )

    def get_me(self) -> dict[str, Any]:
        """Call the Bot API ``getMe`` endpoint and return the ``result`` object.

        This is a READ-ONLY probe used by ``hermes health telegram`` to
        verify that the bot token is valid AND the API is reachable
        WITHOUT sending anything to any chat. Never call ``sendMessage``
        from a health path.

        Returns:
            The ``result`` object from the Bot API response (typically
            containing ``id``, ``is_bot``, ``username``, ``first_name``,
            etc.).

        Raises:
            TelegramNotifierError: On transport failure, non-2xx HTTP
                status (401 = bad token, 429 = rate-limited), or
                ``ok=false`` in the API response body.
        """
        body = self._call("getMe", http="GET", retry_429=False)
        result = body.get("result")
        if not isinstance(result, dict):
            raise TelegramNotifierError(
                f"getMe response missing 'result' object: {str(body)[:ERROR_EXCERPT_MAX_CHARS]}"
            )
        return result

    def _call(
        self,
        method: str,
        *,
        http: Literal["GET", "POST"] = "POST",
        json: dict[str, Any] | None = None,
        retry_429: bool = True,
    ) -> dict[str, Any]:
        """Call the Bot API ``method`` through the shared transport.

        The URL is relative, so the client's ``base_url`` picks the host (the
        production factory sets it from :attr:`TelegramConfig.api_base`).

        Args:
            method: Bot API method name, appended to ``/bot<TOKEN>/``.
            http: The HTTP verb.
            json: JSON-serialisable request body.
            retry_429: Wait out an HTTP 429 (see the module docstring).

        Returns:
            The reply body, whose ``ok`` is true.

        Raises:
            TelegramNotifierError: On transport failure (chained to the
                ``httpx.HTTPError``), non-2xx HTTP status (includes the
                status code and a body excerpt; a 429 only once its
                retries or time cap are spent), a non-JSON body, or an
                ``ok=false`` response body.
        """
        transport = TelegramHttp(
            token=self.config.bot_token,
            client=self.client,
            timeout_sec=self.timeout_sec,
            sleep=self.sleep,
            monotonic=self.monotonic,
        )
        try:
            return transport.call(method, http=http, json=json, retry_429=retry_429)
        except TelegramHttpError as exc:
            if exc.failure is TelegramHttpFailure.TRANSPORT and exc.__cause__ is not None:
                cause = repr(exc.__cause__).replace(self.config.bot_token, "<token>")
                msg = f"transport failure calling {method}: {cause}"
                raise TelegramNotifierError(msg) from exc.__cause__
            raise TelegramNotifierError(str(exc)) from None
