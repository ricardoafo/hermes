"""Slack notifier adapter — rich/long-form reports channel.

Implements :class:`hermes.ports.notifier.NotifierPort` against the Slack
Web API (``chat.postMessage`` + external-upload file endpoints). Per the
2026-08-23 decisions-journal entry
(``docs/decisions/2026-08-23-slack-alongside-telegram.md``), Slack carries
rich long-form content — weekly reports, threaded postmortems, CSV/PDF
attachments — plus urgent pings that also benefit from a durable record
(``ROLLBACK_FIRED`` / ``CVE_DETECTED`` fan out to both channels). Terse
phone alerts go to Telegram, not here.

Design decisions
----------------

- **HTTP library**: raw ``httpx.Client`` against ``https://slack.com``.
  Same reasoning as the Telegram adapter (see
  ``telegram_adapter.py`` header): avoid the ``slack_sdk`` package —
  it drags its own retry logic, session model, and async surface, none
  of which we need for our small emit surface. A thin wrapper aligns
  with the code-conventions §5 anyio-on-asyncio policy (no competing
  event loops).
- **Sync**: :class:`NotifierPort` methods are sync (no ``async def``).
  The application-layer router calls them from a sync context; we match
  that. If Phase 2 needs async fan-out, port + adapter migrate together.
- **Auth**: Slack Bot Token in an ``Authorization: Bearer xoxb-...``
  header. Loaded from ``HERMES_SLACK__BOT_TOKEN`` env var; the default
  channel from ``HERMES_SLACK__DEFAULT_CHANNEL``. No YAML — secrets stay
  off disk per AGENTS.md prohibition #6 + ADR-003 §04 (same call the
  Telegram adapter made).
- **Errors**: raises :class:`SlackNotifierError` (extends
  :class:`hermes.domain.errors.InfraError`) on any wire, HTTP-status, or
  API-response failure. Never swallows silently — per code-conventions
  §2 adapters raise, the application layer converts to :class:`Result`.
- **Slack HTTP-200 footgun**: Slack's Web API frequently returns HTTP
  200 even when the request failed, with ``{"ok": false, "error":
  "..."}`` in the JSON body. Every response MUST inspect ``body["ok"]``
  in addition to the status code. A raw ``response.raise_for_status()``
  is NOT sufficient. See ``_post`` below.
- **Rate limits**: Slack rate-limits per method (tier 1 = 1/minute,
  tier 4 = 100+/minute; ``chat.postMessage`` is tier 4 with special
  per-workspace/per-channel limits). This adapter does NOT implement
  client-side back-off yet; it raises on HTTP 429 so the caller sees
  the pressure. Adaptive back-off is a documented follow-up for the
  router layer.
- **Block Kit**: :meth:`notify_urgent` posts plain text.
  :meth:`notify_report` accepts a rendered body plus optional
  attachments. Callers wanting explicit Block Kit blocks (formatted
  tables, sections, dividers) use the extension-shaped
  :meth:`post_message` method, which accepts a ``blocks`` list — this
  keeps the port surface uncluttered while giving the router / caller
  full Block Kit access when it wants it.
- **File attachments**: uses the Slack v2 external-upload flow
  (:code:`files.getUploadURLExternal` → PUT to returned URL →
  :code:`files.completeUploadExternal`). The legacy :code:`files.upload`
  endpoint was retired by Slack in Nov 2025 and is no longer callable.
  Three HTTP round-trips per file — expected cost.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

import httpx
import structlog

from hermes.domain.errors import InfraError

__all__ = ["SlackAdapter", "SlackConfig", "SlackNotifierError"]

_SLACK_API_BASE: Final[str] = "https://slack.com"
_DEFAULT_TIMEOUT_SEC: Final[float] = 10.0
_CORRELATION_TAIL_LEN: Final[int] = 6
# HTTP status boundary — anything at or above signals an error response
# regardless of what Slack sets in the body.
_HTTP_ERROR_THRESHOLD: Final[int] = 400
# Truncate response-body excerpts included in raised errors — keeps the
# exception message readable even when the upstream returns a huge blob.
_ERROR_EXCERPT_MAX_CHARS: Final[int] = 200

_log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class SlackNotifierError(InfraError):
    """Slack Web API call failed at transport, HTTP, or API-response layer.

    Wraps wire-level (httpx) exceptions, non-2xx HTTP responses, and
    ``ok=false`` responses from the Web API into one exception surface.
    The ``ok=false`` case is why this exception exists as its own class:
    Slack returns HTTP 200 for many API failures (auth errors,
    channel-not-found, missing-scope, invalid-blocks), and a naive
    ``response.raise_for_status()`` would silently swallow them.

    Args:
        msg: Human-readable context — includes HTTP status, response body
            excerpt, or transport error message. Verbose because a Slack
            outage during a report cycle is a second incident; the
            operator needs enough context to diagnose without a debugger.
    """

    msg: str

    def __str__(self) -> str:
        """Return the wrapped human-readable message."""
        return f"Slack notifier error: {self.msg}"


@dataclass(frozen=True, slots=True)
class SlackConfig:
    """Credentials + default channel for the Slack Web API.

    Loaded from environment variables via :meth:`SlackConfig.from_env` —
    the adapter never accepts raw secrets from a YAML file (per
    AGENTS.md prohibition #6 + ADR-003 §04).

    Attributes:
        bot_token: The Slack bot user OAuth token issued by the workspace
            admin. Format: ``xoxb-<numbers>-<numbers>-<base64>``. Treated
            as opaque; the adapter only puts it in an
            ``Authorization: Bearer`` header.
        default_channel: The channel ID (``C0123ABCD``) or name
            (``#alerts``) that :meth:`notify_urgent` /
            :meth:`notify_report` post to when the caller does not
            override with a ``channel=`` kwarg. Stored as ``str`` since
            the API accepts both ID and name.
    """

    bot_token: str
    default_channel: str

    @classmethod
    def from_env(cls, env: dict[str, str]) -> SlackConfig:
        """Build a :class:`SlackConfig` from the given env-var mapping.

        Args:
            env: Environment snapshot. Pass ``os.environ`` in production;
                pass a controlled dict in tests so the ambient shell
                never leaks in.

        Returns:
            A validated :class:`SlackConfig` instance.

        Raises:
            SlackNotifierError: If either required env var is missing or
                empty. Fail-fast — the adapter is useless without
                credentials and we would rather see the error at process
                start than at first report time.
        """
        token = env.get("HERMES_SLACK__BOT_TOKEN", "").strip()
        channel = env.get("HERMES_SLACK__DEFAULT_CHANNEL", "").strip()
        if not token:
            raise SlackNotifierError("HERMES_SLACK__BOT_TOKEN is not set (or empty)")
        if not channel:
            raise SlackNotifierError("HERMES_SLACK__DEFAULT_CHANNEL is not set (or empty)")
        return cls(bot_token=token, default_channel=channel)


def _correlation_tail(correlation_id: str) -> str:
    """Return the last :data:`_CORRELATION_TAIL_LEN` chars of ``correlation_id``.

    Per ADR-008 § Correlation ID convention, notifications carry the
    correlation ID's tail so an operator reading a Slack ping can
    immediately grep journald for the full log context.

    Args:
        correlation_id: The full ULID or short identifier.

    Returns:
        The tail (possibly the entire string if it is shorter than the
        tail length). Never raises.
    """
    return correlation_id[-_CORRELATION_TAIL_LEN:]


@dataclass(slots=True)
class SlackAdapter:
    """Concrete :class:`NotifierPort` for the Slack rich/reports channel.

    Attributes:
        config: The Web API credentials + default channel.
        client: Injected :class:`httpx.Client`. Lets tests supply a
            :class:`httpx.MockTransport` (or ``respx`` mock) without
            monkey-patching. Production callers construct with the
            default via :meth:`SlackAdapter.create`.
        timeout_sec: Per-request timeout — Slack's Web API is typically
            fast; a 10s ceiling catches wedged connections without
            starving the notification path.
    """

    config: SlackConfig
    client: httpx.Client = field()
    timeout_sec: float = _DEFAULT_TIMEOUT_SEC

    @classmethod
    def create(
        cls, config: SlackConfig, *, timeout_sec: float = _DEFAULT_TIMEOUT_SEC
    ) -> SlackAdapter:
        """Build an adapter with a default :class:`httpx.Client`.

        Prefer this factory in production wiring; tests should construct
        :class:`SlackAdapter` directly with a :class:`httpx.MockTransport`
        (or ``respx``) so no real socket ever opens under pytest.

        Args:
            config: Web API credentials + default channel.
            timeout_sec: Per-request timeout in seconds.

        Returns:
            A new adapter owning its :class:`httpx.Client`.
        """
        client = httpx.Client(base_url=_SLACK_API_BASE, timeout=timeout_sec)
        return cls(config=config, client=client, timeout_sec=timeout_sec)

    def close(self) -> None:
        """Release the underlying :class:`httpx.Client` resources.

        Safe to call multiple times.
        """
        self.client.close()

    # ------------------------------------------------------------------
    # NotifierPort surface
    # ------------------------------------------------------------------

    def notify_urgent(self, *, event: str, correlation_id: str, message: str) -> None:
        """Post a short urgent message to the configured Slack channel.

        Format (plain text — Slack renders newlines and mrkdwn inline):

            *<event>* [<cid-tail>]
            <message>

        Args:
            event: Dotted-namespace event name (e.g.
                ``"rollback.fired"``). Rendered bold in the message header.
            correlation_id: Full ULID; only the last 6 chars appear in
                the ping (per ADR-008 § Correlation ID convention).
            message: Short human-readable body. Longer, formatted content
                belongs on :meth:`notify_report`.

        Raises:
            SlackNotifierError: On transport failure, non-2xx HTTP
                status (including 429 rate-limit and 401 auth), or
                ``ok=false`` in the API response body.
        """
        tail = _correlation_tail(correlation_id)
        text = f"*{event}* [{tail}]\n{message}"
        payload: dict[str, Any] = {
            "channel": self.config.default_channel,
            "text": text,
        }
        self._post_json("/api/chat.postMessage", payload)

    def notify_report(
        self,
        *,
        event: str,
        correlation_id: str,
        title: str,
        body: str,
        attachments: list[Path] | None = None,
    ) -> None:
        """Post a rich long-form report to the configured Slack channel.

        Renders ``title`` as a Block Kit ``header`` block and ``body``
        as a ``section`` block with mrkdwn text. Then uploads each
        attachment via the Slack v2 external-upload flow (three HTTP
        calls per file) and shares it into the same channel.

        Args:
            event: Dotted-namespace event name (e.g.
                ``"weekly.rebalance.report"``). Included in the payload
                text fallback so notification previews render meaningfully
                even for clients that do not render Block Kit.
            correlation_id: Full ULID; the last 6 chars appear as a
                subheader for grep-to-journald triage.
            title: Report title — rendered as a Block Kit ``header``.
            body: Report body (Slack mrkdwn accepted) — rendered as a
                ``section`` block.
            attachments: Optional list of local file paths to upload and
                share into the same channel. ``None`` or ``[]`` means
                no attachments. Files must be readable at the moment of
                the call.

        Raises:
            SlackNotifierError: On any transport failure, non-2xx HTTP
                response, or ``ok=false`` body from any of the (1 +
                3*len(attachments)) HTTP calls this method makes.
        """
        tail = _correlation_tail(correlation_id)
        blocks: list[dict[str, Any]] = [
            {"type": "header", "text": {"type": "plain_text", "text": title, "emoji": True}},
            {
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": f"*{event}* · `{tail}`"}],
            },
            {"type": "section", "text": {"type": "mrkdwn", "text": body}},
        ]
        payload: dict[str, Any] = {
            "channel": self.config.default_channel,
            "text": f"{title} — {event} [{tail}]",  # fallback for no-blocks clients
            "blocks": blocks,
        }
        self._post_json("/api/chat.postMessage", payload)

        for path in attachments or ():
            self._upload_file(path)

    # ------------------------------------------------------------------
    # Extension surface (beyond NotifierPort) — accepts explicit blocks
    # for callers that want fine-grained Block Kit control.
    # ------------------------------------------------------------------

    def post_message(
        self,
        *,
        message: str,
        blocks: list[dict[str, Any]] | None = None,
        channel: str | None = None,
    ) -> None:
        """Post a message with optional explicit Block Kit blocks.

        Wider surface than :class:`NotifierPort` intentionally — the
        router / caller may want full Block Kit control (tables, action
        buttons, dividers) without cramming everything through
        :meth:`notify_report`'s fixed title/body/attachments shape.

        Args:
            message: Plain-text body. Also serves as the fallback for
                notification previews when ``blocks`` are supplied.
            blocks: Optional Block Kit blocks list per Slack's schema
                (https://api.slack.com/reference/block-kit/blocks).
                ``None`` means text-only.
            channel: Target channel ID or name. ``None`` posts to
                :attr:`SlackConfig.default_channel`.

        Raises:
            SlackNotifierError: On any transport failure, non-2xx HTTP
                response, or ``ok=false`` body.
        """
        payload: dict[str, Any] = {
            "channel": channel or self.config.default_channel,
            "text": message,
        }
        if blocks is not None:
            payload["blocks"] = blocks
        self._post_json("/api/chat.postMessage", payload)

    # ------------------------------------------------------------------
    # Internal HTTP helpers
    # ------------------------------------------------------------------

    def _auth_headers(self) -> dict[str, str]:
        """Return the ``Authorization`` header for a Bearer-token call."""
        return {"Authorization": f"Bearer {self.config.bot_token}"}

    def _post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        """POST ``payload`` as JSON to ``path`` and validate the Slack response.

        Args:
            path: Slack API path, e.g. ``"/api/chat.postMessage"``.
            payload: JSON-serialisable request body.

        Returns:
            The parsed response body (a dict with at minimum an
            ``"ok": True`` entry — verified before return).

        Raises:
            SlackNotifierError: On transport failure (wraps
                ``httpx.HTTPError``), non-2xx HTTP status (includes the
                status code and a body excerpt), non-JSON response body,
                or an ``ok=false`` body (Slack's HTTP-200 footgun).
        """
        try:
            response = self.client.post(path, json=payload, headers=self._auth_headers())
        except httpx.HTTPError as exc:
            raise SlackNotifierError(f"transport failure calling {path}: {exc!r}") from exc

        return self._validate_response(path, response)

    def _validate_response(self, path: str, response: httpx.Response) -> dict[str, Any]:
        """Convert an :class:`httpx.Response` into a validated Slack body dict.

        Centralises the two-layer check every Slack call needs:
        HTTP status THEN ``body["ok"]``. Raises on either failure with
        an operator-readable message.

        Args:
            path: The Slack API path that was called (for error context).
            response: The response from httpx.

        Returns:
            The parsed body dict (guaranteed to contain ``ok=True``).

        Raises:
            SlackNotifierError: On non-2xx status, non-JSON body, or
                ``ok=false`` body.
        """
        if response.status_code >= _HTTP_ERROR_THRESHOLD:
            excerpt = response.text[:_ERROR_EXCERPT_MAX_CHARS]
            raise SlackNotifierError(f"HTTP {response.status_code} from {path}: {excerpt}")

        try:
            data = response.json()
        except ValueError as exc:
            raise SlackNotifierError(
                f"non-JSON response from {path}: {response.text[:_ERROR_EXCERPT_MAX_CHARS]}"
            ) from exc

        if not isinstance(data, dict) or not data.get("ok", False):
            excerpt = str(data)[:_ERROR_EXCERPT_MAX_CHARS]
            raise SlackNotifierError(f"Slack API rejected {path}: {excerpt}")

        return data

    def _upload_file(self, path: Path) -> None:
        """Upload ``path`` to Slack via the v2 external-upload flow.

        Three-step dance mandated by Slack after the Nov 2025 sunset of
        the legacy ``files.upload`` endpoint:

        1. ``files.getUploadURLExternal`` — Slack returns a signed PUT URL
           and a ``file_id``.
        2. ``PUT`` the raw bytes to the returned URL (NOT ``slack.com``).
        3. ``files.completeUploadExternal`` — associates the uploaded
           bytes with a Slack file record and shares it into the
           configured channel.

        Args:
            path: Local file path. Must be readable at call time.

        Raises:
            SlackNotifierError: On any of the three network calls
                failing (transport, HTTP status, or ``ok=false`` body),
                or on a local read error against ``path``.
        """
        try:
            content = path.read_bytes()
        except OSError as exc:
            raise SlackNotifierError(f"cannot read attachment {path}: {exc!r}") from exc
        filename = path.name
        length = len(content)

        # Step 1: ask Slack for an upload URL + file_id.
        step1 = self._post_form(
            "/api/files.getUploadURLExternal",
            {"filename": filename, "length": str(length)},
        )
        upload_url = step1.get("upload_url")
        file_id = step1.get("file_id")
        if not isinstance(upload_url, str) or not isinstance(file_id, str):
            raise SlackNotifierError(
                f"files.getUploadURLExternal returned malformed body: {step1!s:.200}"
            )

        # Step 2: PUT the bytes to the returned URL (no auth header — the URL is signed).
        try:
            put_response = self.client.put(upload_url, content=content)
        except httpx.HTTPError as exc:
            raise SlackNotifierError(f"transport failure uploading {filename}: {exc!r}") from exc
        if put_response.status_code >= _HTTP_ERROR_THRESHOLD:
            excerpt = put_response.text[:_ERROR_EXCERPT_MAX_CHARS]
            raise SlackNotifierError(
                f"HTTP {put_response.status_code} uploading {filename}: {excerpt}"
            )

        # Step 3: complete the upload and share into the channel.
        self._post_json(
            "/api/files.completeUploadExternal",
            {
                "files": [{"id": file_id, "title": filename}],
                "channel_id": self.config.default_channel,
            },
        )

    def _post_form(self, path: str, form: dict[str, str]) -> dict[str, Any]:
        """POST ``form`` as ``application/x-www-form-urlencoded`` and validate.

        Slack's file-upload endpoints (``files.getUploadURLExternal``,
        legacy ``files.upload``) accept form-encoded bodies rather than
        JSON. Same auth + response-validation contract as
        :meth:`_post_json`.

        Args:
            path: Slack API path.
            form: Form fields (all values must be strings).

        Returns:
            The parsed and ``ok``-validated response body dict.

        Raises:
            SlackNotifierError: On the same failure modes as
                :meth:`_post_json`.
        """
        try:
            response = self.client.post(path, data=form, headers=self._auth_headers())
        except httpx.HTTPError as exc:
            raise SlackNotifierError(f"transport failure calling {path}: {exc!r}") from exc
        return self._validate_response(path, response)
