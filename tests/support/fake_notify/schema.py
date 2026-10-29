"""The vendor contract for every payload the notification fakes accept or return.

One checker per payload. Each returns the ways a payload breaks the contract,
empty when it conforms. The same checkers judge three sources:

- what Hermes sends: the fakes check every request on arrival and refuse a
  bad one the way the vendor would;
- what the fakes answer: every generated response must pass;
- what the vendors answer today: the contract run (``tests/contract/``)
  fetches live responses and checks them here.

So a format change at a vendor fails the contract run, and a Hermes change
that sends a field the vendor would refuse fails the scenario that sent it
(issue #972, the same pattern as the fake market in issue #688).
"""

import json
import re
from collections.abc import Mapping

TELEGRAM_TEXT_MAX = 4096
SLACK_TEXT_MAX = 40_000
HEALTHCHECKS_BODY_MAX = 100_000

TELEGRAM_SEND_FIELDS = frozenset(
    {
        "chat_id",
        "text",
        "parse_mode",
        "disable_notification",
        "disable_web_page_preview",
        "link_preview_options",
        "message_thread_id",
        "reply_markup",
    }
)
_TELEGRAM_PARSE_MODES = frozenset({"MarkdownV2", "HTML", "Markdown"})
_SLACK_POST_FIELDS = frozenset(
    {"channel", "text", "blocks", "attachments", "thread_ts", "mrkdwn", "unfurl_links"}
)
_SLACK_TS = re.compile(r"^\d+\.\d{6}$")
_HEX32 = re.compile(r"^[0-9a-f]{32}$")
_PING_SUFFIX = re.compile(
    r"^(|/fail|/start|/log|/(?:[0-9]|[1-9][0-9]|1[0-9]{2}|2[0-4][0-9]|25[0-5]))$"
)


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_flag(value: object) -> bool:
    """Telegram takes a JSON boolean, or ``true``/``false`` in a form body."""
    return isinstance(value, bool) or value in {"true", "false"}


def telegram_send_message_problems(body: Mapping[str, object]) -> list[str]:
    """Return how a ``sendMessage`` request body breaks the Bot API contract.

    Args:
        body: The decoded JSON or form fields.

    Returns:
        The problems found; empty when the request conforms.

    """
    problems = [f"unknown field {name!r}" for name in sorted(set(body) - TELEGRAM_SEND_FIELDS)]
    chat_id = body.get("chat_id")
    if not (_is_int(chat_id) or (isinstance(chat_id, str) and chat_id.strip())):
        problems.append("chat_id is missing or not an integer or non-empty string")
    text = body.get("text")
    if not isinstance(text, str) or not text:
        problems.append("text is missing or empty")
    elif len(text) > TELEGRAM_TEXT_MAX:
        problems.append(f"text is {len(text)} characters, over {TELEGRAM_TEXT_MAX}")
    if "parse_mode" in body and body["parse_mode"] not in _TELEGRAM_PARSE_MODES:
        problems.append(f"parse_mode {body['parse_mode']!r} is not one Telegram accepts")
    problems.extend(
        f"{name} is not a boolean"
        for name in ("disable_notification", "disable_web_page_preview")
        if name in body and not _is_flag(body[name])
    )
    return problems


def telegram_response_problems(body: object) -> list[str]:
    """Return how a Bot API response body breaks the contract.

    Args:
        body: The decoded response.

    Returns:
        The problems found; empty when the response conforms.

    """
    if not isinstance(body, dict):
        return [f"body is {type(body).__name__}, not an object"]
    ok = body.get("ok")
    if not isinstance(ok, bool):
        return ["ok is missing or not a boolean"]
    if ok:
        return [] if "result" in body else ["ok response has no result"]
    problems = []
    if not _is_int(body.get("error_code")):
        problems.append("error response has no integer error_code")
    if not isinstance(body.get("description"), str):
        problems.append("error response has no description")
    return problems


def telegram_update_problems(update: object) -> list[str]:
    """Return how one ``getUpdates`` result item breaks the contract.

    Args:
        update: One decoded update.

    Returns:
        The problems found; empty when the update conforms.

    """
    if not isinstance(update, dict):
        return [f"update is {type(update).__name__}, not an object"]
    problems = [] if _is_int(update.get("update_id")) else ["update_id is not an integer"]
    message = update.get("message")
    if message is None:
        return problems
    if not isinstance(message, dict):
        return [*problems, "message is not an object"]
    if not _is_int(message.get("message_id")):
        problems.append("message.message_id is not an integer")
    if not _is_int(message.get("date")):
        problems.append("message.date is not an integer")
    chat = message.get("chat")
    if not isinstance(chat, dict) or not _is_int(chat.get("id")):
        problems.append("message.chat.id is not an integer")
    if "text" in message and not isinstance(message["text"], str):
        problems.append("message.text is not a string")
    return problems


def slack_post_message_problems(body: Mapping[str, object]) -> list[str]:
    """Return how a ``chat.postMessage`` request body breaks the Web API contract.

    Args:
        body: The decoded JSON body.

    Returns:
        The problems found; empty when the request conforms.

    """
    problems = [f"unknown field {name!r}" for name in sorted(set(body) - _SLACK_POST_FIELDS)]
    channel = body.get("channel")
    if not isinstance(channel, str) or not channel.strip():
        problems.append("channel is missing or empty")
    text = body.get("text")
    blocks = body.get("blocks")
    if text is None and blocks is None:
        problems.append("neither text nor blocks is set")
    if text is not None and (not isinstance(text, str) or len(text) > SLACK_TEXT_MAX):
        problems.append(f"text is not a string of at most {SLACK_TEXT_MAX} characters")
    if blocks is not None and (
        not isinstance(blocks, list)
        or not all(isinstance(b, dict) and isinstance(b.get("type"), str) for b in blocks)
    ):
        problems.append("blocks is not a list of objects that each name a type")
    thread_ts = body.get("thread_ts")
    if thread_ts is not None and not (isinstance(thread_ts, str) and _SLACK_TS.match(thread_ts)):
        problems.append("thread_ts is not a Slack timestamp")
    return problems


def slack_response_problems(body: object) -> list[str]:
    """Return how a Web API response body breaks the contract.

    Args:
        body: The decoded response.

    Returns:
        The problems found; empty when the response conforms.

    """
    if not isinstance(body, dict):
        return [f"body is {type(body).__name__}, not an object"]
    ok = body.get("ok")
    if not isinstance(ok, bool):
        return ["ok is missing or not a boolean"]
    if not ok and not isinstance(body.get("error"), str):
        return ["error response has no error string"]
    ts = body.get("ts")
    if ts is not None and not (isinstance(ts, str) and _SLACK_TS.match(ts)):
        return ["ts is not a Slack timestamp"]
    return []


def healthchecks_ping_problems(method: str, suffix: str, body: bytes) -> list[str]:
    """Return how a ping breaks the healthchecks contract.

    Args:
        method: The HTTP method.
        suffix: The path after the check's UUID (``""``, ``/fail``, ...).
        body: The raw request body.

    Returns:
        The problems found; empty when the ping conforms.

    """
    problems = [] if method in {"GET", "HEAD", "POST"} else [f"method {method} is not a ping"]
    if not _PING_SUFFIX.match(suffix):
        problems.append(f"suffix {suffix!r} is not a ping signal")
    if len(body) > HEALTHCHECKS_BODY_MAX:
        problems.append(f"body is {len(body)} bytes, over {HEALTHCHECKS_BODY_MAX}")
    return problems


def sentry_envelope_problems(raw: bytes) -> list[str]:
    """Return how a Sentry envelope breaks the envelope format.

    An envelope is a JSON header line, then items: each a JSON item header
    naming its ``type`` and an optional byte ``length``, then its payload.

    Args:
        raw: The decompressed request body.

    Returns:
        The problems found; empty when the envelope conforms.

    """
    head, _, rest = raw.partition(b"\n")
    try:
        header = json.loads(head)
    except ValueError:
        return ["envelope header is not JSON"]
    if not isinstance(header, dict):
        return ["envelope header is not an object"]
    problems = []
    event_id = header.get("event_id")
    if event_id is not None and not (isinstance(event_id, str) and _HEX32.match(event_id)):
        problems.append("event_id is not 32 lowercase hex characters")
    items = 0
    while rest.strip():
        item_head, _, rest = rest.partition(b"\n")
        try:
            item = json.loads(item_head)
        except ValueError:
            return [*problems, f"item {items} header is not JSON"]
        if not isinstance(item, dict) or not isinstance(item.get("type"), str):
            return [*problems, f"item {items} header names no type"]
        length = item.get("length")
        if length is None:
            _, _, rest = rest.partition(b"\n")
        elif _is_int(length) and 0 <= length <= len(rest):
            rest = rest[length:].removeprefix(b"\n")
        else:
            return [*problems, f"item {items} length does not fit the envelope"]
        items += 1
    if items == 0:
        problems.append("envelope has no items")
    return problems
