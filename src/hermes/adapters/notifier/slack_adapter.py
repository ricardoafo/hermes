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
  is NOT sufficient. See ``_validate_response`` below.
- **Rate limits**: Slack rate-limits per method (tier 1 = 1/minute,
  tier 4 = 100+/minute; ``chat.postMessage`` is tier 4 with special
  per-workspace/per-channel limits). This adapter does NOT implement
  client-side back-off yet; it raises on HTTP 429 so the caller sees
  the pressure. Adaptive back-off is a documented follow-up for the
  router layer.
- **Template-driven rendering**: :meth:`notify` is the preferred entry
  point — it takes a :class:`DomainEvent`, delegates all Block Kit
  formatting to :func:`slack_template` (application layer), posts the
  rendered blocks via ``chat.postMessage``, and uploads any
  ``SlackMessage.attachments`` as THREADED REPLIES (``thread_ts`` from
  the parent post). Adapter code stays thin — no formatting decisions
  live here.
- **Legacy wrappers**: :meth:`notify_urgent`, :meth:`notify_report`,
  and :meth:`post_message` are kept as thin wrappers for compatibility
  with PR #92 callers and pre-template code paths. They will be removed
  once the notification-router migration lands. New callers MUST use
  :meth:`notify`. The wrappers preserve every error-handling behaviour
  (``ok=false`` footgun, 401, 429, transport failure) so no callers see
  a behavioural regression during the transition.
- **File attachments (v2 flow)**: uses the Slack v2 external-upload
  flow (:code:`files.getUploadURLExternal` → PUT to returned URL →
  :code:`files.completeUploadExternal`). The legacy
  :code:`files.upload` endpoint was **retired by Slack in Nov 2025**
  and is no longer callable. Three HTTP round-trips per file plus one
  ``chat.postMessage`` when threaded — expected cost.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import httpx
import structlog

from hermes.application.messages.reporting_phase import (
    DEFAULT_REPORTING_PHASE,
    ReportingPhase,
)
from hermes.application.messages.slack import FileAttachment, slack_template
from hermes.domain.errors import InfraError
from hermes.domain.events.anomaly_detected import AnomalyDetected, AnomalySeverity
from hermes.domain.events.base import DomainEvent

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
            ``Authorization: Bearer`` header. Not in ``repr``.
        default_channel: The channel ID (``C0123ABCD``) or name
            (``#alerts``) that :meth:`notify` and the legacy wrappers
            post to when the caller does not override with a
            ``channel=`` kwarg. Stored as ``str`` since the API accepts
            both ID and name.
    """

    bot_token: str = field(repr=False)
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
        reporting_phase: Aggregate-blind phase passed to every render.
            Defaults to pre-certification; building the adapter with
            ``CERTIFIED`` is the explicit unblinding step and logs a
            warning (ADR-012 ruling 1).
    """

    config: SlackConfig
    client: httpx.Client = field()
    timeout_sec: float = _DEFAULT_TIMEOUT_SEC
    reporting_phase: ReportingPhase = DEFAULT_REPORTING_PHASE

    def __post_init__(self) -> None:
        """Log the unblinding step when the adapter may render outcome figures."""
        if self.reporting_phase.shows_outcomes:
            _log.warning("slack_notifier_unblinded", reporting_phase=self.reporting_phase.value)

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
    # Preferred surface — template-driven notify + read-only health probe
    # ------------------------------------------------------------------

    def notify(self, event: DomainEvent) -> None:
        """Render ``event`` through :func:`slack_template` and post to Slack.

        This is the preferred entry point for domain-event notifications.
        Rendering happens in the application layer
        (:mod:`hermes.application.messages.slack`); adapter code stays
        thin — no formatting decisions live here.

        Flow:
          1. Render the event to a :class:`SlackMessage`
             (``text`` fallback + Block Kit ``blocks`` + optional
             ``attachments`` tuple).
          2. POST to ``chat.postMessage`` with ``text`` + ``blocks``.
             The response body carries the parent message ``ts``.
          3. For each ``FileAttachment``, upload via the Slack v2
             external-upload flow AND thread the completed file to the
             parent message via ``thread_ts``. Threading keeps the
             channel view clean — a Daily Trade Digest posts one parent
             card, the CSV lands as a reply.

        Args:
            event: A concrete :class:`DomainEvent` instance.

        Raises:
            SlackNotifierError: On transport failure, non-2xx HTTP
                status (including 429 rate-limit and 401 auth), or
                ``ok=false`` in the API response body from any of the
                (1 + 3*len(attachments)) HTTP calls this method makes.
            ValueError: If no template is registered for ``type(event)``
                (bubbled from :func:`slack_template`).
        """
        rendered = slack_template(event, phase=self.reporting_phase)

        payload: dict[str, Any] = {
            "channel": self.config.default_channel,
            "text": rendered.text,
            "blocks": rendered.blocks,
        }
        response_body = self._post_json("/api/chat.postMessage", payload)
        parent_ts = response_body.get("ts")

        for attachment in rendered.attachments:
            # Thread each attachment under the parent post so the channel
            # timeline stays a single card per event. Fall back to no
            # thread if Slack somehow omitted ``ts`` (defensive — the
            # docs guarantee it, but the field is not typed at the wire).
            thread_ts = parent_ts if isinstance(parent_ts, str) and parent_ts else None
            self._upload_attachment(attachment, thread_ts=thread_ts)

    def auth_test(self) -> dict[str, Any]:
        """Call the Web API ``auth.test`` endpoint and return the response body.

        This is a READ-ONLY probe used by ``hermes health slack`` to
        verify that the bot token is valid AND the API is reachable
        WITHOUT sending anything to any channel. Never call
        ``chat.postMessage`` from a health path — a watchdog script
        would spam the channel every tick.

        Returns:
            The full parsed response body (a dict guaranteed to contain
            ``ok=True``; typically also ``team``, ``user``, ``team_id``,
            ``user_id``, ``url`` fields per
            https://api.slack.com/methods/auth.test).

        Raises:
            SlackNotifierError: On transport failure, non-2xx HTTP
                status (401 = invalid token, 429 = rate-limited), or
                ``ok=false`` in the API response body (Slack's
                HTTP-200 footgun).
        """
        # auth.test accepts an empty body; the bearer token in the
        # Authorization header is the entire request. Sending an empty
        # JSON object keeps the request shape uniform with the other
        # chat/files endpoints and avoids an httpx warning about a POST
        # with no body.
        return self._post_json("/api/auth.test", {})

    # ------------------------------------------------------------------
    # Legacy NotifierPort surface — deprecated thin wrappers.
    #
    # Every wrapper below is scheduled for removal once the notification
    # router migration (PR F) lands. New callers MUST use ``notify()``.
    # ------------------------------------------------------------------

    def notify_urgent(self, *, event: str, correlation_id: str, message: str) -> None:
        """Post a short urgent message to the configured Slack channel.

        DEPRECATED — prefer :meth:`notify` (which takes a
        :class:`DomainEvent`). This wrapper exists for compatibility
        with pre-template callers and will be removed once the
        notification-router migration lands. It constructs a synthetic
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
            SlackNotifierError: On transport failure, non-2xx HTTP
                status (including 429 rate-limit and 401 auth), or
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
        """Post a rich long-form report to the configured Slack channel.

        DEPRECATED — prefer :meth:`notify` with a concrete report event
        (e.g. :class:`DailyTradeDigest`). This wrapper preserves the
        original inline Block Kit rendering + ``Path``-based attachment
        upload so existing pre-template callers keep working during the
        router migration. It will be removed once every caller has
        migrated.

        Renders ``title`` as a Block Kit ``header`` block and ``body``
        as a ``section`` block with mrkdwn text. Then uploads each
        attachment via the Slack v2 external-upload flow (three HTTP
        calls per file) as a threaded reply to the parent message.

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
                share as threaded replies. ``None`` or ``[]`` means
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
        response_body = self._post_json("/api/chat.postMessage", payload)
        parent_ts = response_body.get("ts")
        thread_ts = parent_ts if isinstance(parent_ts, str) and parent_ts else None

        for path in attachments or ():
            self._upload_file(path, thread_ts=thread_ts)

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

        DEPRECATED — kept for compatibility with pre-template callers.
        New callers should render through :func:`slack_template` and
        call :meth:`notify` instead so no formatting decisions live in
        adapter callers. Will be removed once the router migration
        lands.

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

    def _upload_attachment(self, attachment: FileAttachment, *, thread_ts: str | None) -> None:
        """Upload an in-memory :class:`FileAttachment` via the v2 flow.

        Companion to :meth:`_upload_file` for attachments generated in
        memory by the template layer (bytes, filename, MIME already
        known). Threads under ``thread_ts`` when provided.

        Args:
            attachment: The rendered attachment (name + MIME + bytes).
            thread_ts: Parent message ``ts`` — the upload lands as a
                reply in that thread. ``None`` posts as a top-level
                message (defensive fallback if the parent post did not
                return a ``ts``).

        Raises:
            SlackNotifierError: On any of the three network calls
                failing (transport, HTTP status, or ``ok=false`` body).
        """
        self._external_upload(
            filename=attachment.name,
            content=attachment.content,
            thread_ts=thread_ts,
        )

    def _upload_file(self, path: Path, *, thread_ts: str | None = None) -> None:
        """Upload the file at ``path`` via the Slack v2 external-upload flow.

        Kept for the deprecated :meth:`notify_report` wrapper. Reads the
        file into memory, delegates the three-step wire dance to
        :meth:`_external_upload`. Threads under ``thread_ts`` when
        provided.

        Args:
            path: Local file path. Must be readable at call time.
            thread_ts: Parent message ``ts`` — the upload lands as a
                reply in that thread. ``None`` posts as a top-level
                channel message (preserves original pre-thread
                behaviour).

        Raises:
            SlackNotifierError: On a local read error against ``path``
                or on any of the three network calls failing.
        """
        try:
            content = path.read_bytes()
        except OSError as exc:
            raise SlackNotifierError(f"cannot read attachment {path}: {exc!r}") from exc
        self._external_upload(filename=path.name, content=content, thread_ts=thread_ts)

    def _external_upload(self, *, filename: str, content: bytes, thread_ts: str | None) -> None:
        """Run the Slack v2 external-upload three-step dance.

        Steps (mandated by Slack after the Nov 2025 sunset of the
        legacy ``files.upload`` endpoint):

        1. ``files.getUploadURLExternal`` — Slack returns a signed PUT
           URL and a ``file_id``.
        2. ``PUT`` the raw bytes to the returned URL (NOT
           ``slack.com``; the URL is signed, no auth header needed).
        3. ``files.completeUploadExternal`` — associates the uploaded
           bytes with a Slack file record and shares it into the
           configured channel (optionally threaded).

        Args:
            filename: Filename Slack shows in the file card.
            content: Raw file bytes.
            thread_ts: Parent message ``ts`` — the completed file lands
                as a threaded reply. ``None`` posts to the channel as a
                top-level file share.

        Raises:
            SlackNotifierError: On any of the three network calls
                failing (transport, HTTP status, or ``ok=false`` body),
                or when the step-1 body is malformed (missing
                ``upload_url`` / ``file_id``).
        """
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

        # Step 3: complete the upload and share into the channel
        # (threaded under ``thread_ts`` when supplied).
        complete_payload: dict[str, Any] = {
            "files": [{"id": file_id, "title": filename}],
            "channel_id": self.config.default_channel,
        }
        if thread_ts is not None:
            complete_payload["thread_ts"] = thread_ts
        self._post_json("/api/files.completeUploadExternal", complete_payload)

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
