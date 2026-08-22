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
- **Rate limits**: Telegram's Bot API rate-limits at ~30 messages/second
  globally. This adapter does NOT implement client-side back-off yet; it
  raises on HTTP 429 so the caller sees the pressure. Follow-up: adaptive
  back-off via a token-bucket at the router layer.
- **notify_report**: intentional no-op with a WARNING log line. Long-form
  content does not render well as a phone push (per decisions-journal
  entry); routing a report to Telegram is almost always a misuse.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

import httpx
import structlog

from hermes.domain.errors import InfraError

__all__ = ["TelegramAdapter", "TelegramConfig", "TelegramNotifierError"]

_TELEGRAM_API_BASE: Final[str] = "https://api.telegram.org"
_DEFAULT_TIMEOUT_SEC: Final[float] = 10.0
_CORRELATION_TAIL_LEN: Final[int] = 6
# HTTP status boundary — anything at or above signals an error response
# regardless of whether Telegram Bot API sets ``ok=false`` in the body.
_HTTP_ERROR_THRESHOLD: Final[int] = 400
# Truncate response-body excerpts included in raised errors — keeps the
# exception message readable even when the upstream returns a huge blob.
_ERROR_EXCERPT_MAX_CHARS: Final[int] = 200

# MarkdownV2 reserved characters that MUST be escaped to render literally
# in a Telegram message. Per Bot API spec:
# https://core.telegram.org/bots/api#markdownv2-style
_MARKDOWN_V2_RESERVED: Final[frozenset[str]] = frozenset(
    "_*[]()~`>#+-=|{}.!",
)

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
        chat_id: Numeric chat ID or ``@channelname`` string identifying
            where messages go. Stored as ``str`` since the API accepts
            both int and str and pydantic would need a union anyway.
    """

    bot_token: str
    chat_id: str

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
        """
        token = env.get("HERMES_TELEGRAM__BOT_TOKEN", "").strip()
        chat = env.get("HERMES_TELEGRAM__CHAT_ID", "").strip()
        if not token:
            raise TelegramNotifierError("HERMES_TELEGRAM__BOT_TOKEN is not set (or empty)")
        if not chat:
            raise TelegramNotifierError("HERMES_TELEGRAM__CHAT_ID is not set (or empty)")
        return cls(bot_token=token, chat_id=chat)


def _escape_markdown_v2(text: str) -> str:
    """Escape MarkdownV2 reserved characters so ``text`` renders literally.

    Telegram's MarkdownV2 mode is strict: any unescaped reserved char in a
    payload silently corrupts the message rendering. Since our urgent
    alerts embed event names (dotted namespaces) and correlation tails
    that contain characters like ``.`` and ``_``, we escape unconditionally.

    Args:
        text: The raw text to escape.

    Returns:
        The text with every reserved character prefixed by a backslash.
    """
    return "".join(f"\\{c}" if c in _MARKDOWN_V2_RESERVED else c for c in text)


def _correlation_tail(correlation_id: str) -> str:
    """Return the last :data:`_CORRELATION_TAIL_LEN` chars of ``correlation_id``.

    Per ADR-008 § Correlation ID convention, urgent notifications carry
    the correlation ID's tail so an operator reading a phone ping can
    immediately grep journald for the full log context.

    Args:
        correlation_id: The full ULID or short identifier.

    Returns:
        The tail (possibly the entire string if it is shorter than the
        tail length). Never raises.
    """
    return correlation_id[-_CORRELATION_TAIL_LEN:]


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
    """

    config: TelegramConfig
    client: httpx.Client = field()
    timeout_sec: float = _DEFAULT_TIMEOUT_SEC

    @classmethod
    def create(
        cls, config: TelegramConfig, *, timeout_sec: float = _DEFAULT_TIMEOUT_SEC
    ) -> TelegramAdapter:
        """Build an adapter with a default :class:`httpx.Client`.

        Prefer this factory in production wiring; tests should construct
        :class:`TelegramAdapter` directly with a :class:`httpx.MockTransport`
        so no real socket ever opens under pytest.

        Args:
            config: Bot API credentials.
            timeout_sec: Per-request timeout in seconds.

        Returns:
            A new adapter owning its :class:`httpx.Client`.
        """
        client = httpx.Client(base_url=_TELEGRAM_API_BASE, timeout=timeout_sec)
        return cls(config=config, client=client, timeout_sec=timeout_sec)

    def close(self) -> None:
        """Release the underlying :class:`httpx.Client` resources.

        Safe to call multiple times.
        """
        self.client.close()

    def notify_urgent(self, *, event: str, correlation_id: str, message: str) -> None:
        """Post an urgent short message to the configured Telegram chat.

        Format (MarkdownV2, all fields escaped):

            *<event>* [<cid-tail>]
            <message>

        Args:
            event: Dotted-namespace event name (e.g.
                ``"rollback.fired"``). Rendered bold in the message header.
            correlation_id: Full ULID; only the last 6 chars appear in
                the ping (per ADR-008 § Correlation ID convention).
            message: Short human-readable body. Kept terse — long-form
                reports go to :meth:`notify_report` on the Slack adapter.

        Raises:
            TelegramNotifierError: On transport failure, non-2xx HTTP
                status (including 429 rate-limit and 401/403 auth), or
                ``ok=false`` in the API response body.
        """
        tail = _correlation_tail(correlation_id)
        header = f"*{_escape_markdown_v2(event)}* \\[{_escape_markdown_v2(tail)}\\]"
        body = _escape_markdown_v2(message)
        text = f"{header}\n{body}"

        payload = {
            "chat_id": self.config.chat_id,
            "text": text,
            "parse_mode": "MarkdownV2",
            "disable_web_page_preview": True,
        }
        self._post("sendMessage", payload)

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
            correlation_tail=_correlation_tail(correlation_id),
            reason="telegram channel is urgent-only per decisions journal",
        )

    # ------------------------------------------------------------------
    # Internal HTTP helper
    # ------------------------------------------------------------------

    def _post(self, method: str, payload: dict[str, Any]) -> None:
        """POST ``payload`` to the Bot API ``method`` and validate the response.

        Args:
            method: Bot API method name, appended to
                ``/bot<TOKEN>/`` (e.g. ``"sendMessage"``).
            payload: JSON-serialisable request body.

        Raises:
            TelegramNotifierError: On transport failure (wraps
                ``httpx.HTTPError``), non-2xx HTTP status (includes the
                status code and a body excerpt), or an ``ok=false``
                response body.
        """
        url = f"/bot{self.config.bot_token}/{method}"
        try:
            response = self.client.post(url, json=payload)
        except httpx.HTTPError as exc:
            raise TelegramNotifierError(f"transport failure calling {method}: {exc!r}") from exc

        if response.status_code >= _HTTP_ERROR_THRESHOLD:
            excerpt = response.text[:_ERROR_EXCERPT_MAX_CHARS]
            raise TelegramNotifierError(f"HTTP {response.status_code} from {method}: {excerpt}")

        try:
            data = response.json()
        except ValueError as exc:
            raise TelegramNotifierError(
                f"non-JSON response from {method}: {response.text[:_ERROR_EXCERPT_MAX_CHARS]}"
            ) from exc

        if not isinstance(data, dict) or not data.get("ok", False):
            excerpt = str(data)[:_ERROR_EXCERPT_MAX_CHARS]
            raise TelegramNotifierError(f"Bot API rejected {method}: {excerpt}")
