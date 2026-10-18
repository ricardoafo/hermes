"""One loopback service that records every message and ping Hermes sends.

Four sinks share one port, each under its own prefix and speaking its
vendor's wire format, so a release reaches them by config alone:

- ``/telegram``: the Bot API (``getMe``, ``sendMessage``, ``getUpdates``);
  set ``HERMES_TELEGRAM__API_BASE`` to :attr:`FakeNotify.telegram_base`.
- ``/slack``: the Web API (``auth.test``, ``chat.postMessage``, the
  three-step file upload); set ``HERMES_SLACK__API_BASE`` to
  :attr:`FakeNotify.slack_base`.
- ``/hc``: healthchecks pings; set ``HERMES_HEALTHCHECKS__PING_URL`` to
  :meth:`FakeNotify.ping_url`.
- ``/sentry``: the envelope endpoint; set the DSN to
  :meth:`FakeNotify.sentry_dsn`.

Every request is checked against :mod:`.schema` and recorded with what it
broke. A request that breaks the contract is refused the way the vendor
refuses it. A test, or a scenario run against an installed release, then
reads the record, injects inbound Telegram commands and schedules outages
(a timeout, a 429, a 5xx), either through this class or the control API
under ``/_fake/``.

Recorded paths never hold a bot token: it is replaced by ``<token>``.

Run it standalone with ``python -m tests.support.fake_notify.server --port N``.
"""

import argparse
import contextlib
import gzip
import json
import re
import threading
import time
import zlib
from collections import defaultdict, deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Final
from urllib.parse import parse_qsl, urlsplit

from tests.support.fake_notify import schema

_TELEGRAM_METHOD = re.compile(r"^/telegram/bot([^/]+)/(\w+)$")
_PING = re.compile(r"^/hc/ping/([0-9a-fA-F-]{36})(/.*)?$")
_ENVELOPE = re.compile(r"^/sentry/api/(\d+)/envelope/$")
_UPLOAD = re.compile(r"^/slack/upload/(F\w+)$")
_BEARER = re.compile(r"^Bearer xox[bp]-\S+$")
_EPOCH: Final = 1_760_000_000
_EXAMPLE_CHECK: Final = "00000000-0000-4000-8000-000000000000"


class Sink(StrEnum):
    """The four vendors the service stands in for."""

    TELEGRAM = "telegram"
    SLACK = "slack"
    HEALTHCHECKS = "hc"
    SENTRY = "sentry"


class Outage(StrEnum):
    """A failure the next request to a sink meets."""

    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    SERVER_ERROR = "server_error"


@dataclass(frozen=True, slots=True)
class Call:
    """One request a sink received.

    Attributes:
        sink: Which vendor it was sent to.
        method: The HTTP method.
        path: The request path, with any bot token replaced by ``<token>``.
        body: The decoded JSON or form fields; ``{"raw": text}`` otherwise.
        problems: How the request broke the vendor contract; empty if it conformed.
        status: The HTTP status the fake answered with.

    """

    sink: Sink
    method: str
    path: str
    body: Mapping[str, object]
    problems: tuple[str, ...]
    status: int

    def as_json(self) -> dict[str, object]:
        """Return the call as the control API serves it."""
        return {
            "sink": self.sink.value,
            "method": self.method,
            "path": self.path,
            "body": dict(self.body),
            "problems": list(self.problems),
            "status": self.status,
        }


@dataclass(frozen=True, slots=True)
class _Reply:
    status: int
    body: bytes
    headers: tuple[tuple[str, str], ...] = ()

    @classmethod
    def json(cls, status: int, payload: object, *headers: tuple[str, str]) -> _Reply:
        return cls(
            status, json.dumps(payload).encode(), (("Content-Type", "application/json"), *headers)
        )

    @classmethod
    def text(cls, status: int, text: str) -> _Reply:
        return cls(status, text.encode(), (("Content-Type", "text/plain"),))


@dataclass
class _State:
    calls: list[Call] = field(default_factory=list)
    outages: dict[Sink, deque[Outage]] = field(default_factory=lambda: defaultdict(deque))
    updates: list[dict[str, object]] = field(default_factory=list)
    next_update_id: int = 1
    next_message_id: int = 1
    next_ts: int = 1


def _decode(content_type: str, raw: bytes) -> dict[str, object]:
    if content_type.startswith("application/json"):
        try:
            value = json.loads(raw or b"{}")
        except ValueError:
            return {"raw": raw.decode("utf-8", "replace")}
        return value if isinstance(value, dict) else {"raw": value}
    if content_type.startswith("application/x-www-form-urlencoded"):
        return dict(parse_qsl(raw.decode("utf-8", "replace"), keep_blank_values=True))
    return {"raw": raw.decode("utf-8", "replace")} if raw else {}


def _decompress(encoding: str, raw: bytes) -> bytes | None:
    """Return the body Sentry compressed, or ``None`` for an encoding it cannot read."""
    if encoding in {"", "identity"}:
        return raw
    if encoding == "gzip":
        return gzip.decompress(raw)
    if encoding == "deflate":
        return zlib.decompress(raw)
    return None


class FakeNotify:
    """The recording service. Use it as a context manager, or :meth:`start` and :meth:`stop`."""

    def __init__(
        self, host: str = "127.0.0.1", port: int = 0, timeout_hold_s: float = 15.0
    ) -> None:
        """Bind the service; nothing is served until :meth:`start`.

        Args:
            host: Interface to bind; loopback by default.
            port: Port to bind; 0 picks a free one.
            timeout_hold_s: How long a :attr:`Outage.TIMEOUT` holds the request
                before answering 504; set it above the client's timeout.

        """
        self._lock = threading.Lock()
        self._state = _State()
        self._timeout_hold_s = timeout_hold_s
        self._httpd = ThreadingHTTPServer((host, port), self._handler_class())
        self._httpd.daemon_threads = True
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        """The service root, e.g. ``http://127.0.0.1:41234``."""
        host, port = self._httpd.server_address[:2]
        return f"http://{host!s}:{port}"

    @property
    def telegram_base(self) -> str:
        """The Bot API root to configure as ``HERMES_TELEGRAM__API_BASE``."""
        return f"{self.url}/telegram"

    @property
    def slack_base(self) -> str:
        """The Web API root to configure as ``HERMES_SLACK__API_BASE``."""
        return f"{self.url}/slack"

    def ping_url(self, check_uuid: str) -> str:
        """Return a ping URL to configure as ``HERMES_HEALTHCHECKS__PING_URL``."""
        return f"{self.url}/hc/ping/{check_uuid}"

    def sentry_dsn(self, project_id: int = 1, key: str = "fakepublickey") -> str:
        """Return a DSN whose envelopes land in the Sentry sink."""
        _, netloc = self.url.split("://", 1)
        return f"http://{key}@{netloc}/sentry/{project_id}"

    def start(self) -> FakeNotify:
        """Serve in a background thread; returns ``self``."""
        self._thread = threading.Thread(
            target=self._httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
        )
        self._thread.start()
        return self

    def stop(self) -> None:
        """Stop serving and release the port."""
        self._httpd.shutdown()
        self._httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def __enter__(self) -> FakeNotify:
        """Start serving."""
        return self.start()

    def __exit__(self, *exc: object) -> None:
        """Stop serving."""
        self.stop()

    def calls(self, sink: Sink | None = None) -> list[Call]:
        """Return the recorded requests, oldest first, optionally for one sink."""
        with self._lock:
            return [c for c in self._state.calls if sink is None or c.sink is sink]

    def inject_command(self, chat_id: int, text: str) -> int:
        """Queue an inbound Telegram message for the next ``getUpdates``; returns its update id."""
        with self._lock:
            state = self._state
            update_id = state.next_update_id
            state.next_update_id += 1
            state.updates.append(
                {
                    "update_id": update_id,
                    "message": {
                        "message_id": self._message_id(),
                        "date": _EPOCH + update_id,
                        "chat": {"id": chat_id, "type": "private"},
                        "text": text,
                    },
                }
            )
            return update_id

    def fail_next(self, sink: Sink, outage: Outage, times: int = 1) -> None:
        """Make the next ``times`` requests to ``sink`` meet ``outage``."""
        with self._lock:
            self._state.outages[sink].extend([outage] * times)

    def reset(self) -> None:
        """Forget every call, queued update and scheduled outage."""
        with self._lock:
            self._state = _State()

    def _message_id(self) -> int:
        message_id = self._state.next_message_id
        self._state.next_message_id += 1
        return message_id

    def _ts(self) -> str:
        ts = self._state.next_ts
        self._state.next_ts += 1
        return f"{_EPOCH}.{ts:06d}"

    def _take_outage(self, sink: Sink) -> Outage | None:
        with self._lock:
            queue = self._state.outages[sink]
            return queue.popleft() if queue else None

    def _record(self, call: Call) -> None:
        with self._lock:
            self._state.calls.append(call)

    def _handle(self, method: str, target: str, headers: Mapping[str, str], raw: bytes) -> _Reply:
        parts = urlsplit(target)
        path = parts.path
        if path.startswith("/_fake/"):
            return self._control(method, path, parts.query, raw)
        sink = next((s for s in Sink if path.startswith(f"/{s.value}/")), None)
        if sink is None:
            return _Reply.text(404, "no sink at this path")
        content_type = headers.get("content-type", "")
        body: dict[str, object] = dict(parse_qsl(parts.query))
        problems: list[str] = []
        if sink is Sink.SENTRY:
            decoded = _decompress(headers.get("content-encoding", ""), raw)
            if decoded is None:
                problems.append(f"unreadable content-encoding {headers.get('content-encoding')!r}")
                decoded = b""
            body["raw"] = decoded.decode("utf-8", "replace")
        else:
            body.update(_decode(content_type, raw))
        recorded_path = _TELEGRAM_METHOD.sub(r"/telegram/bot<token>/\2", path)
        outage = self._take_outage(sink)
        if outage is Outage.TIMEOUT:
            # Recorded before the hold: the client has given up by the time it ends.
            reply = _Reply.text(504, "gateway timeout")
            self._record(Call(sink, method, recorded_path, body, (), reply.status))
            time.sleep(self._timeout_hold_s)
            return reply
        if outage is not None:
            reply = _OUTAGE_REPLIES[sink][outage]
        else:
            route: Callable[..., _Reply] = _ROUTES[sink]
            reply = route(self, method, path, headers, body, raw, problems)
        self._record(Call(sink, method, recorded_path, body, tuple(problems), reply.status))
        return reply

    def _telegram(
        self,
        method: str,
        path: str,
        headers: Mapping[str, str],
        body: dict[str, object],
        raw: bytes,
        problems: list[str],
    ) -> _Reply:
        del headers, raw
        match = _TELEGRAM_METHOD.match(path)
        if match is None:
            return _Reply.json(404, {"ok": False, "error_code": 404, "description": "Not Found"})
        name = match.group(2)
        with self._lock:
            if name == "getMe":
                result: object = {
                    "id": 1,
                    "is_bot": True,
                    "first_name": "Hermes fake",
                    "username": "hermes_fake_bot",
                }
            elif name == "sendMessage" and method == "POST":
                problems.extend(schema.telegram_send_message_problems(body))
                if problems:
                    description = "Bad Request: " + "; ".join(problems)
                    return _Reply.json(
                        400, {"ok": False, "error_code": 400, "description": description}
                    )
                chat_id = body["chat_id"]
                result = {
                    "message_id": self._message_id(),
                    "date": _EPOCH,
                    "chat": {
                        "id": int(str(chat_id)) if str(chat_id).lstrip("-").isdigit() else 0,
                        "type": "private",
                    },
                    "text": body["text"],
                }
            elif name == "getUpdates":
                offset = int(str(body.get("offset", 0)) or 0)
                self._state.updates = [
                    u for u in self._state.updates if int(str(u["update_id"])) >= offset
                ]
                result = list(self._state.updates)
            else:
                return _Reply.json(
                    404, {"ok": False, "error_code": 404, "description": "Not Found"}
                )
        return _Reply.json(200, {"ok": True, "result": result})

    def _slack(
        self,
        method: str,
        path: str,
        headers: Mapping[str, str],
        body: dict[str, object],
        raw: bytes,
        problems: list[str],
    ) -> _Reply:
        upload = _UPLOAD.match(path)
        if upload is not None:
            return _Reply.text(200, f"OK - {len(raw)}")
        if not _BEARER.match(headers.get("authorization", "")):
            problems.append("no bot token in the Authorization header")
            return _Reply.json(200, {"ok": False, "error": "not_authed"})
        with self._lock:
            payload = self._slack_answer(path.removeprefix("/slack/api/"), method, body, problems)
        if payload is None:
            return _Reply.json(404, {"ok": False, "error": "unknown_method"})
        return _Reply.json(200, payload)

    def _slack_answer(
        self, name: str, method: str, body: dict[str, object], problems: list[str]
    ) -> dict[str, object] | None:
        """Return the Web API answer to method ``name``, or ``None`` for an unknown one."""
        if name == "auth.test":
            return {
                "ok": True,
                "url": "https://hermes-fake.slack.com/",
                "team": "fake",
                "user": "hermes",
                "team_id": "T0FAKE",
                "user_id": "U0FAKE",
                "bot_id": "B0FAKE",
            }
        if name == "chat.postMessage" and method == "POST":
            problems.extend(schema.slack_post_message_problems(body))
            if problems:
                return {"ok": False, "error": "invalid_arguments"}
            return {"ok": True, "channel": body["channel"], "ts": self._ts()}
        if name == "files.getUploadURLExternal":
            file_id = f"F{self._message_id():08d}"
            return {
                "ok": True,
                "upload_url": f"{self.slack_base}/upload/{file_id}",
                "file_id": file_id,
            }
        if name == "files.completeUploadExternal":
            return {"ok": True, "files": body.get("files", [])}
        return None

    def _healthchecks(
        self,
        method: str,
        path: str,
        headers: Mapping[str, str],
        body: dict[str, object],
        raw: bytes,
        problems: list[str],
    ) -> _Reply:
        del headers, body
        match = _PING.match(path)
        if match is None:
            return _Reply.text(404, "not found")
        problems.extend(schema.healthchecks_ping_problems(method, match.group(2) or "", raw))
        return _Reply.text(400, "invalid request") if problems else _Reply.text(200, "OK")

    def _sentry(
        self,
        method: str,
        path: str,
        headers: Mapping[str, str],
        body: dict[str, object],
        raw: bytes,
        problems: list[str],
    ) -> _Reply:
        del raw
        if _ENVELOPE.match(path) is None or method != "POST":
            return _Reply.text(404, "not found")
        if "sentry_key=" not in headers.get("x-sentry-auth", ""):
            problems.append("no sentry_key in X-Sentry-Auth")
        envelope = str(body.get("raw", "")).encode()
        problems.extend(schema.sentry_envelope_problems(envelope))
        if problems:
            return _Reply.json(400, {"detail": "; ".join(problems)})
        header = json.loads(envelope.partition(b"\n")[0])
        return _Reply.json(200, {"id": header.get("event_id")})

    def _control(self, method: str, path: str, query: str, raw: bytes) -> _Reply:
        params = dict(parse_qsl(query))
        payload = json.loads(raw) if raw else {}
        try:
            if method == "GET" and path == "/_fake/calls":
                sink = Sink(params["sink"]) if "sink" in params else None
                return _Reply.json(200, [c.as_json() for c in self.calls(sink)])
            if method == "POST" and path == "/_fake/telegram/commands":
                return _Reply.json(
                    200,
                    {
                        "update_id": self.inject_command(
                            int(payload["chat_id"]), str(payload["text"])
                        )
                    },
                )
            if method == "POST" and path == "/_fake/outages":
                self.fail_next(
                    Sink(payload["sink"]), Outage(payload["kind"]), int(payload.get("times", 1))
                )
                return _Reply.json(200, {"ok": True})
            if method == "POST" and path == "/_fake/reset":
                self.reset()
                return _Reply.json(200, {"ok": True})
        except (KeyError, ValueError) as exc:
            return _Reply.json(400, {"error": f"bad control request: {exc}"})
        return _Reply.json(404, {"error": "unknown control path"})

    def _handler_class(self) -> type[BaseHTTPRequestHandler]:
        fake = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def _serve(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                headers = {k.lower(): v for k, v in self.headers.items()}
                reply = fake._handle(self.command, self.path, headers, raw)
                self.send_response(reply.status)
                for name, value in reply.headers:
                    self.send_header(name, value)
                self.send_header("Content-Length", str(len(reply.body)))
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(reply.body)

            do_GET = do_POST = do_PUT = do_HEAD = _serve  # noqa: N815

            def log_message(self, format: str, *args: object) -> None:
                """Keep test output quiet."""

        return Handler


_ROUTES: Final[dict[Sink, Callable[..., _Reply]]] = {
    Sink.TELEGRAM: FakeNotify._telegram,
    Sink.SLACK: FakeNotify._slack,
    Sink.HEALTHCHECKS: FakeNotify._healthchecks,
    Sink.SENTRY: FakeNotify._sentry,
}

_OUTAGE_REPLIES: Final[dict[Sink, dict[Outage, _Reply]]] = {
    Sink.TELEGRAM: {
        Outage.RATE_LIMITED: _Reply.json(
            429,
            {
                "ok": False,
                "error_code": 429,
                "description": "Too Many Requests: retry after 1",
                "parameters": {"retry_after": 1},
            },
            ("Retry-After", "1"),
        ),
        Outage.SERVER_ERROR: _Reply.json(
            502, {"ok": False, "error_code": 502, "description": "Bad Gateway"}
        ),
    },
    Sink.SLACK: {
        Outage.RATE_LIMITED: _Reply.json(
            429, {"ok": False, "error": "ratelimited"}, ("Retry-After", "1")
        ),
        Outage.SERVER_ERROR: _Reply.json(503, {"ok": False, "error": "service_unavailable"}),
    },
    Sink.HEALTHCHECKS: {
        Outage.RATE_LIMITED: _Reply.text(429, "rate limited"),
        Outage.SERVER_ERROR: _Reply.text(503, "service unavailable"),
    },
    Sink.SENTRY: {
        Outage.RATE_LIMITED: _Reply.text(429, "rate limited"),
        Outage.SERVER_ERROR: _Reply.text(503, "service unavailable"),
    },
}


def main(argv: list[str] | None = None) -> None:
    """Serve until interrupted, printing the base URLs to configure."""
    parser = argparse.ArgumentParser(prog="fake_notify")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args(argv)
    with FakeNotify(args.host, args.port) as fake:
        print(f"HERMES_TELEGRAM__API_BASE={fake.telegram_base}", flush=True)
        print(f"HERMES_SLACK__API_BASE={fake.slack_base}", flush=True)
        print(f"HERMES_HEALTHCHECKS__PING_URL={fake.ping_url(_EXAMPLE_CHECK)}", flush=True)
        print(f"Sentry DSN: {fake.sentry_dsn()}", flush=True)
        with contextlib.suppress(KeyboardInterrupt):
            threading.Event().wait()


if __name__ == "__main__":
    main()
