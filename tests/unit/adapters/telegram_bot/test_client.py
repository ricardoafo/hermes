"""Contract tests for the bot's Telegram client against recorded API shapes.

Invariants protected here:

- ``getUpdates`` long-polls with the offset and returns text messages with
  their chat id; non-message updates are skipped but still advance the offset;
- ``sendMessage`` posts plain text with ``disable_notification`` set for
  silent messages;
- an API error or a network error raises without the token in the message;
- sends are throttled to 1 per second and 20 per minute by waiting.

respx answers with recorded Bot API responses; no real Telegram call.
"""

from urllib.parse import parse_qs

import httpx
import pytest
import respx
from pydantic import SecretStr

from hermes.adapters.telegram_bot import TelegramBotClient, TelegramBotError, Update

_TOKEN = "123456789:FAKE-bot-token_for_tests"  # noqa: S105 - test-only  # gitleaks:allow
_BASE = f"https://api.telegram.org/bot{_TOKEN}"
_CHAT = "-1001234567890"

_UPDATES = {
    "ok": True,
    "result": [
        {
            "update_id": 501,
            "message": {
                "message_id": 7,
                "chat": {"id": -1001234567890, "type": "private"},
                "date": 1791050000,
                "text": "/status",
            },
        },
        {"update_id": 502, "edited_message": {"chat": {"id": 5}, "text": "x"}},
    ],
}
_SENT = {"ok": True, "result": {"message_id": 8}}


class _MonotonicClock:
    """Seconds since an arbitrary start, with a recorded ``sleep`` that moves it on."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(round(seconds, 3))
        self.now += seconds


def _client(clock: _MonotonicClock | None = None) -> TelegramBotClient:
    clk = clock or _MonotonicClock()
    return TelegramBotClient(
        token=SecretStr(_TOKEN), client=httpx.Client(), clock=clk, sleep=clk.sleep
    )


@respx.mock
def test_get_updates_long_polls_with_the_offset_and_parses_text_messages() -> None:
    """The offset and timeout go out; text messages come back with their chat id."""
    route = respx.post(f"{_BASE}/getUpdates").mock(return_value=httpx.Response(200, json=_UPDATES))

    updates = _client().get_updates(offset=500, timeout_s=30)

    sent = route.calls.last.request.url.params
    assert (sent["offset"], sent["timeout"]) == ("500", "30")
    assert updates == [
        Update(update_id=501, chat_id=_CHAT, text="/status"),
        Update(update_id=502, chat_id="", text=""),
    ]


@respx.mock
def test_malformed_updates_are_skipped_not_raised() -> None:
    """Issue #783: an item with no integer update_id is dropped; the good ones still come back."""
    body = {
        "ok": True,
        "result": [
            {"message": {"chat": {"id": 1}, "text": "/status"}},
            {"update_id": "x", "message": {"chat": {"id": 1}, "text": "/status"}},
            "not an object",
            {"update_id": 503, "message": "not an object"},
            {"update_id": 504, "message": {"chat": {"id": 9}, "text": "/logs"}},
        ],
    }
    respx.post(f"{_BASE}/getUpdates").mock(return_value=httpx.Response(200, json=body))

    assert _client().get_updates(offset=None) == [
        Update(update_id=503, chat_id="", text=""),
        Update(update_id=504, chat_id="9", text="/logs"),
    ]


@respx.mock
@pytest.mark.parametrize(("silent", "flag"), [(True, "true"), (False, "false")])
def test_send_message_posts_plain_text_with_the_notification_flag(silent: bool, flag: str) -> None:
    """Silent messages carry disable_notification=true; no parse mode is sent."""
    route = respx.post(f"{_BASE}/sendMessage").mock(return_value=httpx.Response(200, json=_SENT))

    _client().send_message(_CHAT, "All good.", silent=silent)

    form = {k: v[0] for k, v in parse_qs(route.calls.last.request.content.decode()).items()}
    assert form == {"chat_id": _CHAT, "text": "All good.", "disable_notification": flag}


@respx.mock
def test_an_api_error_raises_without_the_token() -> None:
    """Telegram's error description is kept; the token never appears."""
    respx.post(f"{_BASE}/sendMessage").mock(
        return_value=httpx.Response(
            400, json={"ok": False, "description": "Bad Request: chat not found"}
        )
    )

    with pytest.raises(TelegramBotError) as caught:
        _client().send_message(_CHAT, "x")

    assert str(caught.value) == "sendMessage failed: HTTP 400 Bad Request: chat not found"
    assert _TOKEN not in str(caught.value)


@respx.mock
def test_a_network_error_raises_without_the_token_or_its_url() -> None:
    """Network errors carry the URL, and so the token; the client drops them."""
    respx.post(f"{_BASE}/getUpdates").mock(side_effect=httpx.ConnectError("boom"))

    with pytest.raises(TelegramBotError) as caught:
        _client().get_updates(offset=None)

    assert str(caught.value) == "getUpdates failed: ConnectError"
    assert caught.value.__cause__ is None


@respx.mock
def test_sends_wait_one_second_apart_and_twenty_per_minute() -> None:
    """Back-to-back sends wait 1 s each; the 21st waits for the minute to roll."""
    respx.post(f"{_BASE}/sendMessage").mock(return_value=httpx.Response(200, json=_SENT))
    clock = _MonotonicClock()
    client = _client(clock)

    for _ in range(21):
        client.send_message(_CHAT, "x")

    assert clock.slept[:2] == [1.0, 1.0]
    assert len(clock.slept) == 20
    assert clock.slept[-1] == 41.0


@respx.mock
def test_calls_go_to_the_configured_api_base() -> None:
    """With ``api_base`` set, the method URL is built on it, not on api.telegram.org."""
    respx.post(url__startswith="http://127.0.0.1:8081/").mock(
        return_value=httpx.Response(200, json=_SENT)
    )
    client = TelegramBotClient(
        token=SecretStr(_TOKEN), api_base="http://127.0.0.1:8081", client=httpx.Client()
    )

    client.send_message("5", "hi")

    assert [str(call.request.url) for call in respx.calls] == [
        f"http://127.0.0.1:8081/bot{_TOKEN}/sendMessage"
    ]
