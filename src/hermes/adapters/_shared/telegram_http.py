"""One HTTP transport for the Telegram Bot API, shared by the notifier and the bot.

The urgent-alert notifier (:mod:`hermes.adapters.notifier.telegram_adapter`)
and ``hermes bot`` (:mod:`hermes.adapters.telegram_bot.client`) used to carry
their own copies of the Bot API call, and they drifted: only the notifier
waited out an HTTP 429 (issue #1187). One copy here builds the method URL,
posts, waits out a 429, and checks the reply. Each adapter still raises its
own error type, mapped from :class:`TelegramHttpError` at its edge.

Rate limits (issue #859 item 5): Telegram answers HTTP 429 with
``parameters.retry_after``. A call waits that long (or the ``Retry-After``
header, or 1 s) and retries, at most :data:`MAX_429_RETRIES` times and only
while the whole call, request time included, stays within
:data:`MAX_CALL_SEC`. Serve dispatches from a worker thread, so the wait never
blocks the event loop. The bot's proactive per-chat throttle sits on top, in
the bot client.

The token is never in an error's text: a wire failure names only the
exception type, and any body excerpt has the token replaced.
"""

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Final, Literal

import httpx
import structlog

from hermes.domain.errors import InfraError

__all__ = [
    "ERROR_EXCERPT_MAX_CHARS",
    "MAX_429_RETRIES",
    "MAX_CALL_SEC",
    "TelegramHttp",
    "TelegramHttpError",
    "TelegramHttpFailure",
]

DEFAULT_TIMEOUT_SEC: Final[float] = 10.0
HTTP_ERROR_THRESHOLD: Final[int] = 400
ERROR_EXCERPT_MAX_CHARS: Final[int] = 200
MAX_429_RETRIES: Final[int] = 3
# Waits and request time together. Serve awaits the dispatch before it
# finishes the bar, so a longer stall delays the next bar.
MAX_CALL_SEC: Final[float] = 30.0
DEFAULT_429_WAIT_SEC: Final[float] = 1.0
# A retry with less time than this left would be cut off by its own timeout.
MIN_ATTEMPT_SEC: Final[float] = 1.0
_TOO_MANY_REQUESTS: Final[int] = 429
_TOKEN_MASK: Final = "<token>"  # noqa: S105 - the placeholder, not a credential

_log = structlog.get_logger(__name__)


class TelegramHttpFailure(StrEnum):
    """Which layer of a Bot API call failed."""

    TRANSPORT = "transport"
    HTTP_STATUS = "http_status"
    NON_JSON = "non_json"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class TelegramHttpError(InfraError):
    """A Bot API call failed; no field holds the token.

    Args:
        method: The Bot API method, e.g. ``"sendMessage"``.
        failure: The layer that failed.
        excerpt: For a wire failure the exception type name; otherwise the
            first :data:`ERROR_EXCERPT_MAX_CHARS` chars of the body (raw text,
            or the parsed object for a rejection).
        status: The HTTP status, or ``None`` when no response came back.
        description: Telegram's ``description`` field, when the body has one.
    """

    method: str
    failure: TelegramHttpFailure
    excerpt: str
    status: int | None = None
    description: str = ""

    def __str__(self) -> str:
        """Return the reason in the notifier's long-standing wording."""
        match self.failure:
            case TelegramHttpFailure.TRANSPORT:
                return f"transport failure calling {self.method}: {self.excerpt}"
            case TelegramHttpFailure.HTTP_STATUS:
                return f"HTTP {self.status} from {self.method}: {self.excerpt}"
            case TelegramHttpFailure.NON_JSON:
                return f"non-JSON response from {self.method}: {self.excerpt}"
            case TelegramHttpFailure.REJECTED:
                return f"Bot API rejected {self.method}: {self.excerpt}"


def retry_after(response: httpx.Response) -> float:
    """Return how long a 429 asks the client to wait, in seconds.

    The Bot API puts it in the body as ``parameters.retry_after``; a proxy may
    send only the ``Retry-After`` header (seconds form). Neither, or an
    unreadable or negative value, gives :data:`DEFAULT_429_WAIT_SEC`.

    Args:
        response: The HTTP 429 response.

    Returns:
        The wait in seconds, never negative.
    """
    candidates: list[object] = []
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict) and isinstance(params := body.get("parameters"), dict):
        candidates.append(params.get("retry_after"))
    candidates.append(response.headers.get("Retry-After"))
    for raw in candidates:
        if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
            continue
        try:
            wait = float(raw)
        except ValueError:
            continue
        if 0 <= wait < float("inf"):
            return wait
    return DEFAULT_429_WAIT_SEC


@dataclass(slots=True)
class TelegramHttp:
    """Calls one Bot API method and returns its checked reply.

    Attributes:
        token: The bot token. Not in ``repr``.
        client: The HTTP client; tests pass one with an
            :class:`httpx.MockTransport` or route it through ``respx``.
        api_base: The Bot API root the method URL is built on. Empty means
            the client's own ``base_url`` decides the host.
        timeout_sec: Per-attempt timeout; also the connect timeout when a
            call sets its own read timeout.
        sleep: Waits out a 429. Tests pass a fake so no real time passes.
        monotonic: Clock the whole-call cap is measured on.
    """

    token: str = field(repr=False)
    client: httpx.Client
    api_base: str = ""
    timeout_sec: float = DEFAULT_TIMEOUT_SEC
    sleep: Callable[[float], None] = time.sleep
    monotonic: Callable[[], float] = time.monotonic

    def call(
        self,
        method: str,
        *,
        http: Literal["GET", "POST"] = "POST",
        json: Mapping[str, Any] | None = None,
        params: Mapping[str, Any] | None = None,
        data: Mapping[str, str] | None = None,
        read_timeout: float | None = None,
        retry_429: bool = True,
    ) -> dict[str, Any]:
        """Call ``method`` and return the reply body, whose ``ok`` is true.

        Args:
            method: Bot API method name, e.g. ``"sendMessage"``.
            http: The HTTP verb.
            json: JSON request body.
            params: Query parameters.
            data: Form-encoded request body.
            read_timeout: Read timeout for a long poll; ``None`` uses
                :attr:`timeout_sec` for every phase.
            retry_429: Wait out a 429 (see the module). The read-only health
                probe passes ``False`` so it answers at once.

        Returns:
            The parsed reply object.

        Raises:
            TelegramHttpError: On a wire failure (chained to the httpx
                exception), an HTTP status from 400 (a 429 only once its
                retries or the cap are spent), a non-JSON body, or a body
                whose ``ok`` is not true.
        """
        url = f"{self.api_base}/bot{self.token}/{method}"
        started = self.monotonic()
        timeout = self._timeout(self.timeout_sec, read_timeout)
        retries = 0
        while True:
            try:
                response = self.client.request(
                    http, url, json=json, params=params, data=data, timeout=timeout
                )
            except httpx.HTTPError as exc:
                failure = TelegramHttpFailure.TRANSPORT
                raise TelegramHttpError(method, failure, type(exc).__name__) from exc
            if (
                not retry_429
                or response.status_code != _TOO_MANY_REQUESTS
                or retries >= MAX_429_RETRIES
            ):
                break
            wait = retry_after(response)
            left = MAX_CALL_SEC - (self.monotonic() - started) - wait
            if left < MIN_ATTEMPT_SEC:
                break
            retries += 1
            _log.warning("telegram.rate_limited", method=method, retry=retries, wait_sec=wait)
            self.sleep(wait)
            timeout = self._timeout(
                min(self.timeout_sec, left),
                None if read_timeout is None else min(read_timeout, left),
            )
        return self._checked(method, response)

    def _timeout(self, base: float, read: float | None) -> float | httpx.Timeout:
        return base if read is None else httpx.Timeout(base, read=read)

    def _checked(self, method: str, response: httpx.Response) -> dict[str, Any]:
        status = response.status_code
        try:
            data = response.json()
        except ValueError:
            data = None
            parsed = False
        else:
            parsed = True
        description = (
            self._redact(str(data.get("description", ""))) if isinstance(data, dict) else ""
        )
        if status >= HTTP_ERROR_THRESHOLD:
            failure = TelegramHttpFailure.HTTP_STATUS
            raise TelegramHttpError(
                method, failure, self._excerpt(response.text), status, description
            )
        if not parsed:
            failure = TelegramHttpFailure.NON_JSON
            raise TelegramHttpError(method, failure, self._excerpt(response.text), status)
        if not isinstance(data, dict) or not data.get("ok", False):
            failure = TelegramHttpFailure.REJECTED
            raise TelegramHttpError(method, failure, self._excerpt(str(data)), status, description)
        return data

    def _excerpt(self, text: str) -> str:
        return self._redact(text)[:ERROR_EXCERPT_MAX_CHARS]

    def _redact(self, text: str) -> str:
        return text.replace(self.token, _TOKEN_MASK) if self.token else text
