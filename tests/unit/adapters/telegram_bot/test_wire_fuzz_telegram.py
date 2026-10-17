"""Fuzz properties for the Telegram Bot API response parsers (issue #842).

For any reply body, the bot's ``get_updates`` and ``send_message`` return or
raise ``TelegramBotError``, and the notifier's ``get_me`` and ``notify_urgent``
return or raise ``TelegramNotifierError``; nothing else. ``hermes bot`` only
catches ``TelegramBotError``: anything else escaping a poll ends the process.
An accepted update carries an integer id and string chat id and text.

Seeds copy the recorded ``getUpdates``/``getMe``/``sendMessage`` shapes in
the adapter tests. No network: clients answer from an ``httpx.MockTransport``.
"""

from __future__ import annotations

import json
from typing import Final

import httpx
from hypothesis import example, given
from pydantic import SecretStr

from hermes.adapters.notifier.telegram_adapter import (
    TelegramAdapter,
    TelegramConfig,
    TelegramNotifierError,
)
from hermes.adapters.telegram_bot import TelegramBotClient, TelegramBotError, Update
from tests.unit.adapters.wire_fuzz import FUZZ, accepted, foreign_error, mutated, mutated_text

_TOKEN: Final = "123456789:FUZZ-bot-token_for_tests"  # noqa: S105 - test-only  # gitleaks:allow
_UPDATES: Final = {
    "ok": True,
    "result": [
        {
            "update_id": 501,
            "message": {
                "message_id": 7,
                "chat": {"id": -1001234567890, "type": "private"},
                "date": 1_735_689_600,
                "text": "/status",
            },
        },
        {"update_id": 502, "edited_message": {"chat": {"id": 5}, "text": "x"}},
    ],
}
_GET_ME: Final = {"ok": True, "result": {"id": 1, "is_bot": True, "username": "fuzz_bot"}}
_SENT: Final = {"ok": True, "result": {"message_id": 8}}


def _client(body: str | bytes) -> httpx.Client:
    content = body.encode("utf-8") if isinstance(body, str) else body
    transport = httpx.MockTransport(lambda _request: httpx.Response(200, content=content))
    return httpx.Client(base_url="https://api.telegram.org", transport=transport)


def _bot(body: str | bytes) -> TelegramBotClient:
    return TelegramBotClient(
        token=SecretStr(_TOKEN), client=_client(body), clock=lambda: 0.0, sleep=lambda _s: None
    )


def _notifier(body: str | bytes) -> TelegramAdapter:
    return TelegramAdapter(
        config=TelegramConfig(bot_token=_TOKEN, chat_id="1"), client=_client(body)
    )


def _update_violations(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [
        f"bad update {update!r}"
        for update in value
        if not isinstance(update, Update)
        or type(update.update_id) is not int
        or not isinstance(update.chat_id, str)
        or not isinstance(update.text, str)
    ]


@FUZZ
@given(body=mutated_text(_UPDATES))
@example(body="[]")
@example(body='{"ok": true, "result": [{"message": {"text": "/status"}}]}')
@example(body='{"ok": true, "result": {"update_id": 1}}')
@example(body='{"ok": true, "result": [{"update_id": 1, "message": "hi"}]}')
def test_a_get_updates_reply_parses_or_raises_telegram_bot_error(body: str | bytes) -> None:
    """Invariant: any getUpdates reply gives updates or TelegramBotError, never a raw exception."""
    assert foreign_error(lambda: _bot(body).get_updates(offset=500), TelegramBotError) == ""


@FUZZ
@given(body=mutated(_UPDATES))
def test_accepted_updates_carry_an_int_id_and_string_chat_and_text(body: object) -> None:
    """Invariant: every update get_updates returns has an int id and str chat id and text."""
    updates = accepted(lambda: _bot(json.dumps(body)).get_updates(offset=None), TelegramBotError)
    assert _update_violations(updates) == []


@FUZZ
@given(body=mutated_text(_SENT))
def test_a_send_message_reply_returns_or_raises_telegram_bot_error(body: str | bytes) -> None:
    """Invariant: any sendMessage reply returns or raises TelegramBotError."""
    assert foreign_error(lambda: _bot(body).send_message("1", "hello"), TelegramBotError) == ""


@FUZZ
@given(body=mutated_text(_GET_ME))
def test_a_get_me_reply_parses_or_raises_telegram_notifier_error(body: str | bytes) -> None:
    """Invariant: any getMe reply gives the bot object or TelegramNotifierError."""
    assert foreign_error(lambda: _notifier(body).get_me(), TelegramNotifierError) == ""


@FUZZ
@given(body=mutated_text(_SENT))
def test_a_notifier_send_reply_returns_or_raises_telegram_notifier_error(
    body: str | bytes,
) -> None:
    """Invariant: any reply to an urgent push returns or raises TelegramNotifierError."""
    push = _notifier(body)
    assert (
        foreign_error(
            lambda: push.notify_urgent(event="fuzz.test", correlation_id="01J0FUZZ", message="m"),
            TelegramNotifierError,
        )
        == ""
    )
