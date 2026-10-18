"""The notification contract checks accept the vendor shapes and name each break.

Invariant (issue #972): a payload in the vendor's format passes, and every
wrong field fails with a problem that names it, so neither the fakes nor the
contract run can drift from the vendor silently.
"""

import gzip
import json

import pytest
from hypothesis import given
from hypothesis import strategies as st

from tests.support.fake_notify import schema

_SEND = {"chat_id": "-100123", "text": "hi", "parse_mode": "MarkdownV2"}
_POST = {"channel": "C0LAB", "text": "hi", "blocks": [{"type": "section"}]}


def _envelope(*items: tuple[dict[str, object], bytes]) -> bytes:
    lines = [json.dumps({"event_id": "0" * 32}).encode()]
    for header, payload in items:
        lines += [json.dumps(header).encode(), payload]
    return b"\n".join(lines)


def test_conforming_payloads_pass_every_check() -> None:
    """One valid payload per checker passes with no problems."""
    assert [
        schema.telegram_send_message_problems(_SEND),
        schema.telegram_send_message_problems(
            {"chat_id": 5, "text": "x", "disable_notification": "true"}
        ),
        schema.telegram_response_problems({"ok": True, "result": {}}),
        schema.telegram_response_problems({"ok": False, "error_code": 400, "description": "Bad"}),
        schema.telegram_update_problems(
            {
                "update_id": 1,
                "message": {"message_id": 2, "date": 3, "chat": {"id": 4}, "text": "/s"},
            }
        ),
        schema.slack_post_message_problems(_POST),
        schema.slack_response_problems({"ok": True, "ts": "1760000000.000001"}),
        schema.slack_response_problems({"ok": False, "error": "ratelimited"}),
        schema.healthchecks_ping_problems("POST", "/fail", b"reason"),
        schema.healthchecks_ping_problems("GET", "/0", b""),
        schema.sentry_envelope_problems(_envelope(({"type": "event", "length": 2}, b"{}"))),
        schema.sentry_envelope_problems(_envelope(({"type": "event"}, b"{}"))),
    ] == [[]] * 12


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        ({"chat_id": ""}, "chat_id is missing or not an integer or non-empty string"),
        ({"chat_id": True}, "chat_id is missing or not an integer or non-empty string"),
        ({"text": ""}, "text is missing or empty"),
        ({"text": "x" * 4097}, "text is 4097 characters, over 4096"),
        ({"parse_mode": "markdownv2"}, "parse_mode 'markdownv2' is not one Telegram accepts"),
        ({"disable_notification": "yes"}, "disable_notification is not a boolean"),
        ({"colour": "red"}, "unknown field 'colour'"),
    ],
)
def test_each_wrong_send_message_field_is_named(change: dict[str, object], problem: str) -> None:
    """A bad ``sendMessage`` field yields a problem naming it."""
    problems = schema.telegram_send_message_problems({**_SEND, **change})

    assert problems == [problem]


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        ({"channel": " "}, "channel is missing or empty"),
        ({"text": None, "blocks": None}, "neither text nor blocks is set"),
        ({"text": 5}, "text is not a string of at most 40000 characters"),
        (
            {"blocks": [{"text": "no type"}]},
            "blocks is not a list of objects that each name a type",
        ),
        ({"thread_ts": "123"}, "thread_ts is not a Slack timestamp"),
        ({"icon_emoji": ":x:"}, "unknown field 'icon_emoji'"),
    ],
)
def test_each_wrong_post_message_field_is_named(change: dict[str, object], problem: str) -> None:
    """A bad ``chat.postMessage`` field yields a problem naming it."""
    body = {k: v for k, v in {**_POST, **change}.items() if v is not None}

    assert schema.slack_post_message_problems(body) == [problem]


@pytest.mark.parametrize(
    ("body", "problem"),
    [
        ([], "body is list, not an object"),
        ({"result": {}}, "ok is missing or not a boolean"),
        ({"ok": True}, "ok response has no result"),
        ({"ok": False, "description": "x"}, "error response has no integer error_code"),
    ],
)
def test_each_broken_telegram_response_is_named(body: object, problem: str) -> None:
    """A Bot API answer missing its contract fields is caught."""
    assert problem in schema.telegram_response_problems(body)


@pytest.mark.parametrize(
    ("update", "problem"),
    [
        ({"update_id": "1"}, "update_id is not an integer"),
        (
            {"update_id": 1, "message": {"message_id": 1, "date": 1, "chat": {}}},
            "message.chat.id is not an integer",
        ),
        (
            {"update_id": 1, "message": {"message_id": 1, "date": 1, "chat": {"id": 1}, "text": 3}},
            "message.text is not a string",
        ),
    ],
)
def test_each_broken_update_is_named(update: object, problem: str) -> None:
    """A ``getUpdates`` item missing its contract fields is caught."""
    assert problem in schema.telegram_update_problems(update)


@pytest.mark.parametrize(
    ("method", "suffix", "problem"),
    [
        ("DELETE", "", "method DELETE is not a ping"),
        ("POST", "/256", "suffix '/256' is not a ping signal"),
        ("POST", "/stop", "suffix '/stop' is not a ping signal"),
    ],
)
def test_each_bad_ping_is_named(method: str, suffix: str, problem: str) -> None:
    """A ping with the wrong method or signal suffix is caught."""
    assert schema.healthchecks_ping_problems(method, suffix, b"") == [problem]


def test_an_oversized_ping_body_is_caught() -> None:
    """Healthchecks keeps at most 100 kB of a ping body."""
    assert schema.healthchecks_ping_problems("POST", "", b"x" * 100_001) == [
        "body is 100001 bytes, over 100000"
    ]


@pytest.mark.parametrize(
    ("raw", "problem"),
    [
        (b"not json\n{}", "envelope header is not JSON"),
        (b"{}", "envelope has no items"),
        (
            b'{"event_id": "XYZ"}\n{"type": "event"}\n{}',
            "event_id is not 32 lowercase hex characters",
        ),
        (b"{}\n{}\n{}", "item 0 header names no type"),
        (b'{}\n{"type": "event", "length": 99}\n{}', "item 0 length does not fit the envelope"),
    ],
)
def test_each_broken_envelope_is_named(raw: bytes, problem: str) -> None:
    """A Sentry envelope that breaks the format is caught."""
    assert problem in schema.sentry_envelope_problems(raw)


def test_a_compressed_envelope_must_be_decompressed_first() -> None:
    """The checker reads the decompressed body; gzip bytes are not an envelope."""
    raw = gzip.compress(_envelope(({"type": "event"}, b"{}")))

    assert schema.sentry_envelope_problems(raw) != []


@given(text=st.text(min_size=1, max_size=4096).filter(str.strip))
def test_any_nonblank_text_within_the_limit_passes(text: str) -> None:
    """Property: the length rule is the only text rule, so any text up to 4096 passes."""
    assert schema.telegram_send_message_problems({"chat_id": 1, "text": text}) == []


@given(name=st.text(min_size=1, max_size=20).filter(lambda n: n not in schema.TELEGRAM_SEND_FIELDS))
def test_any_field_telegram_does_not_define_is_refused(name: str) -> None:
    """Property: an extra field always yields a problem naming it."""
    problems = schema.telegram_send_message_problems({"chat_id": 1, "text": "x", name: 1})

    assert problems == [f"unknown field {name!r}"]
