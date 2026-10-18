"""Healthchecks.io adapter — dead-man's-switch heartbeat pings.

Healthchecks.io is a heartbeat monitor: hermes GETs (or POSTs) a unique
ping URL periodically, and if the pings stop, Healthchecks alerts the
operator via its own configured channels (email, Slack, Telegram, SMS,
Pushover, etc.). This adapter owns the small HTTP surface required to
ping in.

Three ping endpoints ship in Phase 1:

- ``ping_success(details)`` — the primary heartbeat. GET (or POST if
  ``details`` is supplied) to the raw ping URL. Resets the timeout on
  the Healthchecks side and clears any pending alert.
- ``ping_fail(reason)`` — POST to ``<ping-url>/fail`` with ``reason``
  as the body. Fires the alert channels IMMEDIATELY (does not wait for
  the timeout). Reserve for genuine failures — a routine health probe
  MUST NOT call this or the operator gets paged every tick.
- ``ping_start(details)`` — POST to ``<ping-url>/start`` to signal a
  long job began. Healthchecks uses this to compute duration + display
  a spinner in the UI. Idempotent, informational, does NOT fire the
  dead-man's-switch alert on its own. This is what
  ``hermes health healthchecks`` calls (see :func:`check_healthchecks`
  in :mod:`hermes.entrypoints._health`).

Design decisions
----------------

- **HTTP library**: raw :class:`httpx.Client` — same pattern as the
  Telegram, Slack, and Frankfurter adapters. No SDK; the wire surface
  is three URLs and one body field.
- **Rate limiter**: token-bucket private to the adapter, sized to a
  polite 30 req/min. Healthchecks does not publish a hard limit, but
  the adapter typically pings ~1/minute; 30/min is generous headroom
  for occasional ``ping_start`` + ``ping_success`` pairs on long jobs.
- **Timeout**: aggressive default (5s) — the ping is informational and
  MUST NEVER block the trading path. A slow ping is silently better
  than a stalled trader.
- **Sync interface**: matches every other hermes adapter (anyio-on-asyncio
  policy per ADR-003).
- **Errors**: any wire, HTTP-status failure raises
  :class:`~hermes.domain.errors.HealthchecksError` — an
  :class:`~hermes.domain.errors.InfraError` subclass so callers can use
  one ``except InfraError`` if they want to. No ``ok=false`` body
  footgun (unlike Slack) — Healthchecks returns ``"OK"`` in the body
  on success and its status codes are already reliable.
- **Content-Type**: ``text/plain`` on POST — Healthchecks stores the
  body verbatim (up to ~10KB) and displays it in the UI. Sending
  ``application/json`` would just look mangled in the UI.

Reference: https://healthchecks.io/docs/http_api/.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

import httpx
import structlog

from hermes.adapters._shared.rate_limiter import RateLimitConfig, RateLimiter
from hermes.domain.errors import HealthchecksError

__all__ = ["HealthchecksAdapter", "HealthchecksAdapterConfig"]


_DEFAULT_TIMEOUT_SEC: Final[float] = 5.0
_HTTP_ERROR_THRESHOLD: Final[int] = 400
# Truncate response-body excerpts included in raised errors — keeps the
# exception message readable even when the upstream returns an HTML
# error page (Healthchecks itself returns short "OK" bodies on success,
# but transparent proxies may inject longer error pages).
_ERROR_EXCERPT_MAX_CHARS: Final[int] = 200
# Weight-per-request — Healthchecks is not weight-graded, every call is 1.
_PING_REQUEST_WEIGHT: Final[int] = 1
# Polite default: 30 req/min. Healthchecks publishes no cap; this leaves
# plenty of headroom for a long job that calls ``ping_start`` +
# ``ping_success`` in the same minute without ever hammering the free
# tier.
_DEFAULT_RATE_LIMIT: Final[RateLimitConfig] = RateLimitConfig(max_weight=30, window_seconds=60)
# Body content-type for POST — Healthchecks stores the body verbatim
# and displays it in the UI, so text/plain renders cleanly.
_CONTENT_TYPE_PLAIN: Final[dict[str, str]] = {"Content-Type": "text/plain; charset=utf-8"}

_log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class HealthchecksAdapterConfig:
    """Wire-level configuration for :class:`HealthchecksAdapter`.

    Deliberately named ``HealthchecksAdapterConfig`` (not
    ``HealthchecksConfig``) to keep it distinct from the pydantic
    :class:`hermes.infrastructure.config.models.HealthchecksConfig`
    that lives in the config loader. Wiring code either builds one via
    :meth:`from_env` (mirrors Telegram/Slack) or constructs it
    directly from loader values.

    Attributes:
        ping_url: The unique ping URL for this environment. Must not be
            empty — construction fails-fast if the loader passes a
            blank URL (that means the integration is disabled, and the
            caller should never build an adapter for a disabled
            integration). Format: ``https://hc-ping.com/<uuid>``.
        rate_limit: Sizing for the internal token-bucket limiter.
            Default is 30 req/min — generous for a heartbeat that
            typically fires 1/min.
        timeout_sec: Per-request timeout in seconds. Default is 5s —
            aggressive because a slow ping MUST NOT block the trading
            path.
    """

    ping_url: str
    rate_limit: RateLimitConfig = _DEFAULT_RATE_LIMIT
    timeout_sec: float = _DEFAULT_TIMEOUT_SEC

    def __post_init__(self) -> None:
        """Validate the URL and timeout — cheap fail-fast in the config layer."""
        if not self.ping_url:
            raise ValueError(
                "HealthchecksAdapterConfig.ping_url must not be empty — "
                "guard callers with HealthchecksConfig.enabled before construction"
            )
        if not self.ping_url.startswith(("https://", "http://")):
            raise ValueError(
                f"HealthchecksAdapterConfig.ping_url must start with http:// or https://, "
                f"got {self.ping_url!r}"
            )
        if self.timeout_sec <= 0:
            raise ValueError(
                f"HealthchecksAdapterConfig.timeout_sec must be > 0, got {self.timeout_sec}"
            )

    @classmethod
    def from_env(cls, env: dict[str, str]) -> HealthchecksAdapterConfig:
        """Build a wire config from the given env-var mapping.

        Mirrors the fail-fast convention shared by
        :class:`~hermes.adapters.notifier.TelegramConfig.from_env` and
        :class:`~hermes.adapters.notifier.SlackConfig.from_env`: the
        adapter constructs directly from env, never from a YAML file.
        A missing / empty ``HERMES_HEALTHCHECKS__PING_URL`` raises
        :class:`~hermes.domain.errors.HealthchecksError` so callers see
        the misconfig at process start rather than at first-ping time.

        Args:
            env: Environment snapshot. Pass ``os.environ`` in production;
                pass a controlled dict in tests so the ambient shell
                never leaks in.

        Returns:
            A validated :class:`HealthchecksAdapterConfig` instance.

        Raises:
            HealthchecksError: If ``HERMES_HEALTHCHECKS__PING_URL`` is
                missing or empty. The health-check wiring layer catches
                this and reports NOT_CONFIGURED (exit 3) instead of
                crashing the CLI.
        """
        url = env.get("HERMES_HEALTHCHECKS__PING_URL", "").strip()
        if not url:
            raise HealthchecksError("HERMES_HEALTHCHECKS__PING_URL is not set (or empty)")
        return cls(ping_url=url)


@dataclass(slots=True)
class HealthchecksAdapter:
    """Concrete Healthchecks.io ping adapter.

    Attributes:
        config: Wire settings — carries the ping URL, timeout, and rate-limit
            sizing. Owned by the adapter (never mutated post-construction).
        client: Injected :class:`httpx.Client`. Tests supply a
            :class:`httpx.MockTransport` (or ``respx`` mock) — production
            wiring uses :meth:`HealthchecksAdapter.create`.
        limiter: Token-bucket rate limiter. Adapter owns it; callers do
            not touch it.
    """

    config: HealthchecksAdapterConfig
    client: httpx.Client = field()
    limiter: RateLimiter = field()

    @classmethod
    def create(cls, config: HealthchecksAdapterConfig) -> HealthchecksAdapter:
        """Build an adapter with a default :class:`httpx.Client` and limiter.

        Prefer this factory in production wiring; tests should construct
        :class:`HealthchecksAdapter` directly with a
        :class:`httpx.MockTransport` (or ``respx``) so no real socket
        ever opens under pytest.

        Args:
            config: Wire settings (see
                :class:`HealthchecksAdapterConfig`).

        Returns:
            A new adapter owning its :class:`httpx.Client` and
            :class:`RateLimiter`.
        """
        client = httpx.Client(timeout=config.timeout_sec)
        limiter = RateLimiter(config=config.rate_limit)
        return cls(config=config, client=client, limiter=limiter)

    def close(self) -> None:
        """Release the underlying :class:`httpx.Client` resources.

        Safe to call multiple times.
        """
        self.client.close()

    # ------------------------------------------------------------------
    # Public ping surface
    # ------------------------------------------------------------------

    def ping_success(self, details: str | None = None) -> None:
        """Send the success heartbeat to Healthchecks.

        GETs the raw ping URL when ``details`` is ``None``; POSTs it
        (with ``details`` as the body) otherwise. Both variants signal
        SUCCESS — resets the Healthchecks-side timeout and clears any
        pending alert.

        Args:
            details: Optional short human-readable body Healthchecks
                will store + display in the UI (up to ~10KB). Useful
                for stamping the last-successful-cycle summary
                ("processed 42 signals in 3.1s").

        Raises:
            HealthchecksError: On transport failure or HTTP non-2xx.
        """
        self._ping(url=self.config.ping_url, body=details, endpoint="ping_success")

    def ping_fail(self, reason: str) -> None:
        """Send the failure heartbeat to Healthchecks.

        POSTs to ``<ping-url>/fail`` with ``reason`` as the body. Fires
        the alert channels IMMEDIATELY on the Healthchecks side (does
        NOT wait for the timeout).

        DO NOT call this from a routine health probe — the operator
        would be paged every tick. Reserve for genuine failures that
        the trader has already decided to escalate.

        Args:
            reason: Short human-readable body explaining what failed.
                Required (not optional) — a failure ping without
                context is useless triage-wise. Healthchecks stores
                it and displays it prominently in the alert email.

        Raises:
            HealthchecksError: On transport failure or HTTP non-2xx.
        """
        url = f"{self.config.ping_url}/fail"
        self._ping(url=url, body=reason, endpoint="ping_fail")

    def ping_start(self, details: str | None = None) -> None:
        """Send the start-of-job heartbeat to Healthchecks.

        POSTs to ``<ping-url>/start`` (or GETs if ``details`` is
        ``None``). Healthchecks uses this to compute the duration of
        the job (start → next success ping) and to display a spinner
        in the UI. Does NOT fire the dead-man's-switch alert on its
        own — idempotent + informational.

        This is safe to call from ``hermes health healthchecks`` (see
        :func:`check_healthchecks` in :mod:`hermes.entrypoints._health`):
        it verifies the ping URL is valid AND reachable WITHOUT ever
        firing an alert. A failure probe (``ping_fail``) from a health
        path would spam the operator.

        Args:
            details: Optional short body identifying which job started.

        Raises:
            HealthchecksError: On transport failure or HTTP non-2xx.
        """
        url = f"{self.config.ping_url}/start"
        self._ping(url=url, body=details, endpoint="ping_start")

    # ------------------------------------------------------------------
    # Internal wire helper
    # ------------------------------------------------------------------

    def _ping(self, *, url: str, body: str | None, endpoint: str) -> None:
        """Fire one ping — GET when ``body`` is ``None``, POST otherwise.

        Args:
            url: Full URL to hit (``ping_url`` / ``.../fail`` /
                ``.../start``).
            body: Optional plain-text body — Healthchecks stores it
                verbatim.
            endpoint: Short label for error messages (``ping_success``
                / ``ping_fail`` / ``ping_start``).

        Raises:
            HealthchecksError: On transport failure (wraps
                :class:`httpx.HTTPError`) or non-2xx HTTP response
                (includes the status code and a body excerpt).
        """
        self.limiter.acquire(_PING_REQUEST_WEIGHT)
        try:
            if body is None:
                response = self.client.get(url)
            else:
                response = self.client.post(
                    url,
                    content=body.encode("utf-8"),
                    headers=_CONTENT_TYPE_PLAIN,
                )
        except httpx.HTTPError as exc:
            raise HealthchecksError(f"transport failure calling {endpoint}: {exc!r}") from exc

        if response.status_code >= _HTTP_ERROR_THRESHOLD:
            excerpt = response.text[:_ERROR_EXCERPT_MAX_CHARS]
            raise HealthchecksError(f"HTTP {response.status_code} from {endpoint}: {excerpt}")
