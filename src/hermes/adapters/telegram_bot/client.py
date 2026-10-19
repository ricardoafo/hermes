"""Telegram Bot API client for ``hermes bot`` (ADR-015 D-01, D-08).

- :meth:`TelegramBotClient.get_updates` long-polls ``getUpdates`` over
  outbound HTTPS with an offset, so each update is handled once. No webhook,
  no listening port.
- :meth:`TelegramBotClient.send_message` posts plain text, with
  ``disable_notification`` for silent messages.
- Sends are throttled to at most 1 per second and 20 per minute by waiting
  (injected sleeper), under Telegram's per-chat limits.
- Calls go through the shared Bot API transport
  (:mod:`hermes.adapters._shared.telegram_http`, issue #1187), which also
  waits out an HTTP 429 a bounded number of times.

The token is held as a ``SecretStr`` and never appears in errors or logs: the
error text names the method and status only.

Reference: openspec/archive/379-telegram-bot/change.md (D-4, D-5).
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Final

import httpx
import structlog
from pydantic import SecretStr

from hermes.adapters._shared.telegram_http import (
    TelegramHttp,
    TelegramHttpError,
    TelegramHttpFailure,
)
from hermes.adapters.endpoint import DEFAULT_TELEGRAM_API_BASE
from hermes.domain.errors import InfraError

__all__ = ["TelegramBotClient", "TelegramBotError", "Update"]

_PER_SECOND: Final = 1.0
_PER_MINUTE: Final = 20
_MINUTE_S: Final = 60.0

_log = structlog.get_logger(__name__)


class TelegramBotError(InfraError, RuntimeError):
    """A Telegram call failed; the message never contains the token.

    An :class:`~hermes.domain.errors.InfraError` like every adapter I/O
    failure (issue #1187); still a ``RuntimeError`` for older handlers.
    """


@dataclass(frozen=True, slots=True)
class Update:
    """One incoming text message.

    Attributes:
        update_id: Telegram's update id; the next poll offset is this plus one.
        chat_id: The chat the message came from, as a string.
        text: The message text (empty for non-text updates).
    """

    update_id: int
    chat_id: str
    text: str


@dataclass
class TelegramBotClient:
    """Minimal Bot API client: poll and send.

    Attributes:
        token: The bot token.
        api_base: The Bot API root; :func:`hermes.adapters.endpoint.api_base_from_env`
            reads it from ``HERMES_TELEGRAM__API_BASE``.
        client: The HTTP client; tests pass one routed through respx.
        clock: Monotonic seconds, for the send throttle.
        sleep: Waits the given seconds, for the send throttle.
    """

    token: SecretStr
    api_base: str = DEFAULT_TELEGRAM_API_BASE
    client: httpx.Client = field(default_factory=lambda: httpx.Client(timeout=60.0))
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep
    _sent: deque[float] = field(default_factory=deque)

    def get_updates(self, offset: int | None, timeout_s: int = 30) -> list[Update]:
        """Long-poll for new updates after ``offset``.

        Args:
            offset: The last handled update id plus one, or ``None`` at start.
            timeout_s: How long Telegram may hold the request open.

        Returns:
            New updates, oldest first. One that is not a text message (or
            whose message is malformed) comes back with an empty chat id and
            text, so the caller still advances the offset past it.

        Raises:
            TelegramBotError: On a network error, a non-OK answer, or a reply
                whose ``result`` is not a list of updates with integer ids.
        """
        params: dict[str, Any] = {"timeout": timeout_s, "allowed_updates": '["message"]'}
        if offset is not None:
            params["offset"] = offset
        body = self._call("getUpdates", params=params, read_timeout=timeout_s + 10)
        items = body.get("result", [])
        if not isinstance(items, list):
            raise TelegramBotError("getUpdates failed: result is not a list")
        updates: list[Update] = []
        for item in items:
            update = _parse_update(item)
            if update is not None:
                updates.append(update)
        return updates

    def send_message(self, chat_id: str, text: str, *, silent: bool = False) -> None:
        """Send ``text`` to ``chat_id`` as plain text.

        Args:
            chat_id: The destination chat.
            text: The message, plain text.
            silent: Deliver without a notification sound.

        Raises:
            TelegramBotError: On a network error or a non-OK answer.
        """
        self._throttle()
        self._call(
            "sendMessage",
            data={"chat_id": chat_id, "text": text, "disable_notification": str(silent).lower()},
        )

    def close(self) -> None:
        """Close the HTTP client."""
        self.client.close()

    def _throttle(self) -> None:
        now = self.clock()
        while self._sent and now - self._sent[0] >= _MINUTE_S:
            self._sent.popleft()
        waits = []
        if self._sent:
            waits.append(self._sent[-1] + _PER_SECOND - now)
        if len(self._sent) >= _PER_MINUTE:
            waits.append(self._sent[0] + _MINUTE_S - now)
        wait = max(waits, default=0.0)
        if wait > 0:
            self.sleep(wait)
        self._sent.append(self.clock())

    def _call(
        self,
        method: str,
        *,
        params: dict[str, Any] | None = None,
        data: dict[str, str] | None = None,
        read_timeout: float = 30.0,
    ) -> dict[str, Any]:
        transport = TelegramHttp(
            token=self.token.get_secret_value(),
            client=self.client,
            api_base=self.api_base,
            sleep=self.sleep,
            monotonic=self.clock,
        )
        try:
            return transport.call(method, params=params, data=data, read_timeout=read_timeout)
        except TelegramHttpError as exc:
            if exc.failure is TelegramHttpFailure.TRANSPORT:
                msg = f"{method} failed: {exc.excerpt}"
            else:
                msg = f"{method} failed: HTTP {exc.status} {exc.description}".rstrip()
            raise TelegramBotError(msg) from None


def _parse_update(item: object) -> Update | None:
    """Return the update in ``item``, or ``None`` when it is malformed.

    A malformed update is skipped rather than raised so one bad item cannot
    restart-loop the bot; a later well-formed update moves the offset past it.
    """
    if not isinstance(item, dict):
        _log.warning("telegram_bot.update.malformed", reason="not an object")
        return None
    update_id = item.get("update_id")
    if not isinstance(update_id, int) or isinstance(update_id, bool):
        _log.warning("telegram_bot.update.malformed", reason="no integer update_id")
        return None
    message = item.get("message")
    message = message if isinstance(message, dict) else {}
    chat = message.get("chat")
    chat = chat if isinstance(chat, dict) else {}
    return Update(
        update_id=update_id,
        chat_id=str(chat.get("id", "")),
        text=str(message.get("text", "")),
    )
