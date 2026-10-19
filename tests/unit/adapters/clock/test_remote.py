"""Tests for RemoteClock, the test clock read over HTTP (#972).

Invariants protected here:

- ``now()`` is the clock's ``now``, converted to UTC;
- an answer without an aware instant, an error status or an unreachable
  clock raises ``RemoteClockError``, never falls back to wall time, and the
  error never holds the URL;
- ``sleep(s)`` returns once the clock has moved ``s`` seconds on, however
  little wall time that took, and returns at once for ``s <= 0``;
- ``remote_clock_from_env`` builds a clock only when the variable is set.

The seam with a real clock over a socket is covered by
``tests/integration/lower_env/test_installed_release.py``.
"""

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import anyio
import httpx
import pytest

from hermes.adapters.clock.remote import (
    TEST_CLOCK_URL_ENV,
    RemoteClock,
    RemoteClockError,
    remote_clock_from_env,
)

_URL = "http://127.0.0.1:9/_fake/clock?secret=sekrit"
_T0 = datetime(2024, 10, 28, tzinfo=UTC)


def _answering(payload: Callable[[], object], status: int = 200) -> httpx.MockTransport:
    return httpx.MockTransport(
        lambda request: httpx.Response(status, content=json.dumps(payload()).encode())
    )


def test_now_is_the_clocks_instant_in_utc() -> None:
    """An instant with any offset is returned as the same instant in UTC."""
    clock = RemoteClock(_URL, transport=_answering(lambda: {"now": "2024-10-28T02:00:00+02:00"}))

    assert (clock.now(), clock.now().tzinfo) == (_T0, UTC)


@pytest.mark.parametrize(
    "payload",
    [{"now": "2024-10-28T00:00:00"}, {"now": "yesterday"}, {"now": 1730073600}, {}, ["now"]],
    ids=["naive", "not-iso", "number", "missing", "not-an-object"],
)
def test_an_answer_without_an_aware_instant_is_refused(payload: object) -> None:
    """A scripted run never mixes in a guessed or naive time."""
    clock = RemoteClock(_URL, transport=_answering(lambda: payload))

    with pytest.raises(RemoteClockError, match="without an aware 'now'"):
        clock.now()


def test_an_error_status_is_refused_without_the_url() -> None:
    """A 5xx is an unreachable clock; the message holds no part of the URL."""
    clock = RemoteClock(_URL, transport=_answering(lambda: {"now": _T0.isoformat()}, 503))

    with pytest.raises(RemoteClockError) as excinfo:
        clock.now()

    assert str(excinfo.value) == "test clock unreachable: HTTPStatusError"


def test_an_unreachable_clock_is_refused() -> None:
    """A connection failure raises rather than falling back to the wall clock."""

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(RemoteClockError, match="test clock unreachable: ConnectError"):
        RemoteClock(_URL, transport=httpx.MockTransport(refuse)).now()


def test_sleep_returns_once_the_clock_has_moved_on() -> None:
    """Four scripted hours pass in a few reads, not four hours of wall time."""
    reads: list[datetime] = []

    def tick() -> object:
        reads.append(_T0 + timedelta(hours=len(reads)))
        return {"now": reads[-1].isoformat()}

    clock = RemoteClock(_URL, poll_seconds=0, transport=_answering(tick))

    anyio.run(clock.sleep, 4 * 3600.0)

    assert reads[-1] == _T0 + timedelta(hours=4)


@pytest.mark.parametrize("seconds", [0.0, -5.0])
def test_sleep_for_no_time_reads_nothing(seconds: float) -> None:
    """Nothing to wait for: no request is made."""
    requests: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"now": _T0.isoformat()})

    anyio.run(RemoteClock(_URL, transport=httpx.MockTransport(record)).sleep, seconds)

    assert requests == []


@pytest.mark.parametrize(
    ("env", "url"),
    [
        ({}, None),
        ({TEST_CLOCK_URL_ENV: ""}, None),
        ({TEST_CLOCK_URL_ENV: "  "}, None),
        ({TEST_CLOCK_URL_ENV: f" {_URL} "}, _URL),
    ],
    ids=["unset", "empty", "blank", "set"],
)
def test_only_a_set_url_moves_a_release_onto_a_test_clock(
    env: dict[str, str], url: str | None
) -> None:
    """Unset or blank keeps the wall clock; a URL builds a clock reading exactly that URL."""
    clock = remote_clock_from_env(env)

    assert (None if clock is None else clock.url) == url
