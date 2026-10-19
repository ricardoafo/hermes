"""Unit tests for the shared Telegram Bot API transport (issue #1187).

Invariants protected here, one per test:

- a call goes to ``<api_base>/bot<token>/<method>`` and returns the parsed
  ``ok`` body;
- an HTTP 429 is waited out (``parameters.retry_after``, then the
  ``Retry-After`` header, then 1 s), at most 3 times, and only while the whole
  call stays within 30 s with at least 1 s left for the next attempt;
- every failure (wire, HTTP status from 400, non-JSON, ``ok`` not true)
  raises :class:`TelegramHttpError` with its exact reason, and no error text
  ever holds the token.

No socket opens: every client answers from an ``httpx.MockTransport``. The
seam's real counterpart is
``tests/contract/test_telegram_getme.py::test_get_me_against_real_telegram_api``.
"""

import json
from collections.abc import Callable
from typing import Any, Final

import httpx
import pytest
from hypothesis import example, given
from hypothesis import strategies as st

from hermes.adapters._shared.telegram_http import (
    TelegramHttp,
    TelegramHttpError,
    TelegramHttpFailure,
)
from hermes.domain.errors import InfraError

_TOKEN: Final = "123456:SHARED-transport-token"  # noqa: S105 - test-only  # gitleaks:allow
_BASE: Final = "https://api.telegram.org"
_OK: Final = {"ok": True, "result": {"message_id": 8}}

type _Handler = Callable[[httpx.Request], httpx.Response]


class _FakeTime:
    """A monotonic clock that only moves when the transport sleeps or a request runs."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class _Recorder:
    """Answers each request with the next response and keeps every request seen."""

    def __init__(self, *responses: httpx.Response) -> None:
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]


def _http(
    handler: _Handler,
    *,
    fake: _FakeTime | None = None,
    api_base: str = _BASE,
    base_url: str = "",
) -> TelegramHttp:
    clock = fake or _FakeTime()
    client = httpx.Client(base_url=base_url, transport=httpx.MockTransport(handler))
    return TelegramHttp(
        token=_TOKEN,
        client=client,
        api_base=api_base,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )


def _too_many(retry_after: int | None = None, retry_header: str | None = None) -> httpx.Response:
    body: dict[str, Any] = {"ok": False, "error_code": 429, "description": "Too Many Requests"}
    if retry_after is not None:
        body["parameters"] = {"retry_after": retry_after}
    headers = {} if retry_header is None else {"Retry-After": retry_header}
    return httpx.Response(429, json=body, headers=headers)


def _error(handler: _Handler, method: str = "sendMessage") -> TelegramHttpError:
    with pytest.raises(TelegramHttpError) as caught:
        _http(handler).call(method, json={})
    return caught.value


class TestRequest:
    """Where a call goes and what it returns."""

    def test_a_call_posts_to_the_token_method_url_and_returns_the_body(self) -> None:
        """The method URL is ``<api_base>/bot<token>/<method>``; the parsed body comes back."""
        recorder = _Recorder(httpx.Response(200, json=_OK))

        body = _http(recorder).call("sendMessage", json={"chat_id": "1", "text": "hi"})

        sent = recorder.requests[0]
        assert (sent.method, str(sent.url), json.loads(sent.content), body) == (
            "POST",
            f"{_BASE}/bot{_TOKEN}/sendMessage",
            {"chat_id": "1", "text": "hi"},
            _OK,
        )

    def test_an_empty_api_base_resolves_against_the_client_base_url(self) -> None:
        """With no ``api_base`` the client's own ``base_url`` picks the host (a lab fake)."""
        recorder = _Recorder(httpx.Response(200, json=_OK))

        _http(recorder, api_base="", base_url="http://127.0.0.1:8081").call("getMe", http="GET")

        assert (recorder.requests[0].method, str(recorder.requests[0].url)) == (
            "GET",
            f"http://127.0.0.1:8081/bot{_TOKEN}/getMe",
        )

    def test_the_read_timeout_is_set_apart_from_the_connect_timeout(self) -> None:
        """A long poll holds the read open longer than the 10 s connect limit."""
        recorder = _Recorder(httpx.Response(200, json=_OK))

        _http(recorder).call("getUpdates", params={"timeout": 30}, read_timeout=40.0)

        timeout = recorder.requests[0].extensions["timeout"]
        assert (timeout["connect"], timeout["read"]) == (10.0, 40.0)


class TestRateLimitBackOff:
    """An HTTP 429 is waited out a bounded number of times, never past the cap."""

    def test_one_429_then_ok_returns_the_body_after_one_wait(self) -> None:
        """A single 429 is waited out and the retry's body comes back."""
        fake = _FakeTime()
        recorder = _Recorder(_too_many(2), httpx.Response(200, json=_OK))

        body = _http(recorder, fake=fake).call("sendMessage", json={})

        assert (body, len(recorder.requests), fake.sleeps) == (_OK, 2, [2.0])

    def test_retry_after_in_the_body_wins_over_the_header(self) -> None:
        """The Bot API's ``parameters.retry_after`` sets the wait before ``Retry-After``."""
        fake = _FakeTime()
        recorder = _Recorder(_too_many(7, retry_header="3"), httpx.Response(200, json=_OK))

        _http(recorder, fake=fake).call("sendMessage", json={})

        assert fake.sleeps == [7.0]

    def test_the_retry_after_header_is_the_wait_when_the_body_has_none(self) -> None:
        """Without ``parameters.retry_after`` (a proxy's 429) the header is honoured."""
        fake = _FakeTime()
        recorder = _Recorder(_too_many(retry_header="3"), httpx.Response(200, json=_OK))

        _http(recorder, fake=fake).call("sendMessage", json={})

        assert fake.sleeps == [3.0]

    @pytest.mark.parametrize(
        "response",
        [
            httpx.Response(429, text="Too Many Requests"),
            _too_many(-5),
            httpx.Response(429, json={"ok": False, "parameters": {"retry_after": True}}),
            _too_many(retry_header="Wed, 21 Oct 2026 07:28:00 GMT"),
        ],
        ids=["plain-text-body", "negative", "boolean", "http-date-header"],
    )
    def test_an_unreadable_wait_falls_back_to_one_second(self, response: httpx.Response) -> None:
        """A 429 without a usable wait is retried after 1 s, never at once or never."""
        fake = _FakeTime()

        _http(_Recorder(response, httpx.Response(200, json=_OK)), fake=fake).call("m", json={})

        assert fake.sleeps == [1.0]

    def test_repeated_429s_give_up_after_three_retries(self) -> None:
        """At most 3 retries, then the 429 raises like any other HTTP failure."""
        fake = _FakeTime()
        recorder = _Recorder(_too_many(5))

        with pytest.raises(TelegramHttpError) as caught:
            _http(recorder, fake=fake).call("sendMessage", json={})

        assert (len(recorder.requests), fake.sleeps, caught.value.status) == (4, [5.0] * 3, 429)

    def test_a_wait_leaving_exactly_one_second_is_still_retried(self) -> None:
        """At the limit: a 29 s wait leaves 1 s, the minimum attempt, so it retries."""
        fake = _FakeTime()
        recorder = _Recorder(_too_many(29), httpx.Response(200, json=_OK))

        _http(recorder, fake=fake).call("sendMessage", json={})

        assert fake.sleeps == [29.0]

    def test_a_wait_leaving_under_one_second_is_not_slept(self) -> None:
        """Just past the limit: a 29.5 s wait leaves too little time, so it raises at once."""
        fake = _FakeTime()
        response = httpx.Response(429, json={"ok": False, "parameters": {"retry_after": 29.5}})
        recorder = _Recorder(response, httpx.Response(200, json=_OK))

        with pytest.raises(TelegramHttpError):
            _http(recorder, fake=fake).call("sendMessage", json={})

        assert (len(recorder.requests), fake.sleeps) == (1, [])

    def test_waits_never_add_up_past_the_cap(self) -> None:
        """A second 20 s wait would pass the 30 s cap, so the call gives up after one."""
        fake = _FakeTime()
        recorder = _Recorder(_too_many(20))

        with pytest.raises(TelegramHttpError):
            _http(recorder, fake=fake).call("sendMessage", json={})

        assert (len(recorder.requests), fake.sleeps) == (2, [20.0])

    def test_a_retry_attempt_gets_only_the_time_left(self) -> None:
        """After a 25 s wait the retry may read for 5 s, not the full 10 s or 40 s."""
        fake = _FakeTime()
        recorder = _Recorder(_too_many(25), httpx.Response(200, json=_OK))

        _http(recorder, fake=fake).call("getUpdates", read_timeout=40.0)

        timeout = recorder.requests[1].extensions["timeout"]
        assert (timeout["connect"], timeout["read"]) == (5.0, 5.0)

    def test_slow_attempts_count_towards_the_cap(self) -> None:
        """Request time plus waits stay within 30 s even when every call is slow."""
        fake = _FakeTime()

        def slow_429(request: httpx.Request) -> httpx.Response:
            fake.now += request.extensions["timeout"]["read"]
            return _too_many(1)

        with pytest.raises(TelegramHttpError):
            _http(slow_429, fake=fake).call("sendMessage", json={})

        assert fake.now <= 30.0

    def test_a_call_without_retry_raises_on_the_first_429(self) -> None:
        """``retry_429=False`` (the read-only health probe) never waits."""
        fake = _FakeTime()
        recorder = _Recorder(_too_many(1), httpx.Response(200, json=_OK))

        with pytest.raises(TelegramHttpError):
            _http(recorder, fake=fake).call("getMe", http="GET", retry_429=False)

        assert (len(recorder.requests), fake.sleeps) == (1, [])


class TestFailures:
    """Each failure raises one error type with its exact, token-free reason."""

    def test_the_error_is_an_infra_error(self) -> None:
        """Code-conventions §2: adapter I/O failures are caught as ``InfraError``."""
        with pytest.raises(InfraError, match="HTTP 500 from sendMessage: down"):
            _http(_Recorder(httpx.Response(500, text="down"))).call("sendMessage", json={})

    def test_status_399_with_an_ok_body_is_accepted(self) -> None:
        """Just under the error threshold, an ``ok`` body is a success."""
        body = _http(_Recorder(httpx.Response(399, json=_OK))).call("getMe", http="GET")

        assert body == {"ok": True, "result": {"message_id": 8}}

    def test_status_400_is_an_error_even_with_an_ok_body(self) -> None:
        """At the threshold, HTTP 400 fails whatever the body says."""
        error = _error(_Recorder(httpx.Response(400, text='{"ok":true}')))

        assert (str(error), error.failure, error.status) == (
            'HTTP 400 from sendMessage: {"ok":true}',
            TelegramHttpFailure.HTTP_STATUS,
            400,
        )

    def test_an_error_status_carries_the_api_description(self) -> None:
        """Telegram's ``description`` is kept for the bot's one-line error."""
        response = httpx.Response(400, json={"ok": False, "description": "chat not found"})

        assert _error(_Recorder(response)).description == "chat not found"

    def test_a_status_excerpt_is_capped_at_200_chars(self) -> None:
        """A huge error body is cut to its first 200 chars."""
        error = _error(_Recorder(httpx.Response(502, text="x" * 200 + "TAIL")))

        assert str(error) == f"HTTP 502 from sendMessage: {'x' * 200}"

    def test_a_non_json_body_raises_with_an_excerpt(self) -> None:
        """A 2xx body that is not JSON raises the transport error, not ``ValueError``."""
        error = _error(_Recorder(httpx.Response(200, text="<html>gateway</html>")))

        assert (str(error), error.failure) == (
            "non-JSON response from sendMessage: <html>gateway</html>",
            TelegramHttpFailure.NON_JSON,
        )

    @pytest.mark.parametrize(
        ("body", "quoted"),
        [
            (
                {"ok": False, "description": "Bad Request"},
                "{'ok': False, 'description': 'Bad Request'}",
            ),
            ({"result": {}}, "{'result': {}}"),
            ([True], "[True]"),
        ],
        ids=["ok-false", "no-ok-field", "not-an-object"],
    )
    def test_a_body_whose_ok_is_not_true_is_rejected(self, body: object, quoted: str) -> None:
        """Only an object with ``ok`` true is a success; anything else quotes the body."""
        error = _error(_Recorder(httpx.Response(200, json=body)))

        assert (str(error), error.failure) == (
            f"Bot API rejected sendMessage: {quoted}",
            TelegramHttpFailure.REJECTED,
        )

    def test_a_wire_failure_names_the_exception_type_and_chains_it(self) -> None:
        """A transport failure keeps the httpx exception as its cause for the adapter to map."""
        cause = httpx.ConnectError("DNS failed")

        def boom(_request: httpx.Request) -> httpx.Response:
            raise cause

        error = _error(boom, "getUpdates")

        assert (str(error), error.failure, error.__cause__) == (
            "transport failure calling getUpdates: ConnectError",
            TelegramHttpFailure.TRANSPORT,
            cause,
        )

    def test_a_body_echoing_the_token_is_redacted_from_the_error(self) -> None:
        """An upstream that quotes the request URL cannot leak the token into the error text."""
        response = httpx.Response(404, text=f"no route /bot{_TOKEN}/sendMessage")

        error = _error(_Recorder(response))

        assert str(error) == "HTTP 404 from sendMessage: no route /bot<token>/sendMessage"


@given(status=st.integers(200, 599), text=st.text(max_size=300))
@example(status=200, text=json.dumps({"ok": True, "echo": _TOKEN}))
@example(status=429, text=json.dumps({"ok": False, "description": _TOKEN}))
def test_any_reply_returns_an_ok_object_or_raises_without_the_token(status: int, text: str) -> None:
    """For any status and body, a call returns an ``ok`` object or raises token-free."""
    http = _http(_Recorder(httpx.Response(status, text=text)))
    try:
        body = http.call("sendMessage", json={})
    except TelegramHttpError as exc:
        assert _TOKEN not in f"{exc} {exc.description}"
    else:
        assert (status < 400, body.get("ok")) == (True, True)
