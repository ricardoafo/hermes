"""RemoteClock — reads the time from a test clock over loopback HTTP (#972).

An installed ``hermes serve`` or ``hermes bot`` in the lower environment runs
against the fake market, whose bars carry the scripted dates of its clock,
not today's. Pointed at that clock, the release reads the same instant the
fake serves bars for, and sleeps until the clock reaches a deadline instead
of waiting the same span of wall time, so a scenario can tick a 4h bar in a
second.

The URL comes from :data:`TEST_CLOCK_URL_ENV`. The config loader refuses it
outside dev and qa, and anywhere but loopback (ADR-024 §4), so staging and
prod always run on :class:`~hermes.adapters.clock.WallClock`.

The clock answers ``GET <url>`` with a JSON object whose ``now`` is an
ISO-8601 instant with a UTC offset, as the fake market's ``/_fake/clock``
does.
"""

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Final

import anyio
import httpx

from hermes.domain.errors import InfraError

__all__ = ["TEST_CLOCK_URL_ENV", "RemoteClock", "RemoteClockError", "remote_clock_from_env"]

TEST_CLOCK_URL_ENV: Final = "HERMES_TEST_CLOCK_URL"

_TIMEOUT_S: Final = 5.0
_POLL_S: Final = 0.02


class RemoteClockError(InfraError):
    """The test clock did not answer with an aware instant.

    A release on a test clock has no other time to fall back to, so the
    caller fails rather than mixing wall time into a scripted run.
    """


class RemoteClock:
    """ClockPort backed by a test clock reached over HTTP.

    Args:
        url: The clock's URL.
        poll_seconds: Wall time between reads while :meth:`sleep` waits.
        transport: HTTP transport override for tests.
    """

    def __init__(
        self,
        url: str,
        *,
        poll_seconds: float = _POLL_S,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        """Remember where the clock is; nothing is read until :meth:`now`."""
        self._url = url
        self._poll_seconds = poll_seconds
        self._transport = transport

    @property
    def url(self) -> str:
        """Return the clock's URL."""
        return self._url

    def now(self) -> datetime:
        """Return the test clock's current instant, in UTC.

        Returns:
            A timezone-aware :class:`~datetime.datetime` in UTC.

        Raises:
            RemoteClockError: The clock is unreachable, or its answer holds
                no aware ``now``.
        """
        try:
            with httpx.Client(transport=self._transport, timeout=_TIMEOUT_S) as client:
                response = client.get(self._url)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise RemoteClockError(f"test clock unreachable: {type(exc).__name__}") from exc
        raw = payload.get("now") if isinstance(payload, dict) else None
        try:
            instant = datetime.fromisoformat(raw) if isinstance(raw, str) else None
        except ValueError:
            instant = None
        if instant is None or instant.tzinfo is None:
            raise RemoteClockError("test clock answered without an aware 'now'")
        return instant.astimezone(UTC)

    async def sleep(self, seconds: float) -> None:
        """Wait until the test clock is ``seconds`` past its current instant.

        The clock may tick at any multiple of wall time, or stand still until
        a test moves it, so this polls the clock rather than waiting wall time.

        Args:
            seconds: Span of test-clock time to wait; ``<= 0`` returns at once.
        """
        if seconds <= 0:
            return
        deadline = self.now().timestamp() + seconds
        while self.now().timestamp() < deadline:
            await anyio.sleep(self._poll_seconds)


def remote_clock_from_env(env: Mapping[str, str]) -> RemoteClock | None:
    """Return the test clock :data:`TEST_CLOCK_URL_ENV` names, or ``None`` when it is unset.

    Args:
        env: Env-var snapshot, already checked by the config loader.

    Returns:
        The clock, or ``None`` to keep the wall clock.
    """
    url = env.get(TEST_CLOCK_URL_ENV, "").strip()
    return RemoteClock(url) if url else None
