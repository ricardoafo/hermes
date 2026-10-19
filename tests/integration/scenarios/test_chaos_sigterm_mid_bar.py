"""Chaos: how Hermes stops when SIGTERM lands while a bar is being handled (issue #871).

``systemctl stop`` or a deploy sends SIGTERM, then SIGKILL once the stop
timeout runs out. ``deploy/systemd/hermes.service`` sets no ``TimeoutStopSec``,
so systemd's default of 90 s applies. Serve races shutdown only at the idle
await between bars (``_next_or_stop``): the bar in flight is never cancelled,
so a handler cannot be cut between its ledger writes and its push. The risk is
the other way round: a bar slow enough to outlast the stop timeout is killed
mid-write after all.

Here the breakout bar of the chandelier scenario sends a real SIGTERM to the
process as it starts, through the handlers :func:`install_signal_handlers`
binds. The calls serve makes from then until it exits are recorded, so the
time to exit is bounded by their timeouts, not measured on a wall clock.

The fake clock's sleep resolves within one turn of the event loop, sooner than
a shutdown event already set; a real sleep of a poll interval never does. The
sleep here yields a few more turns so the race at the idle await ends as it
does in production.
"""

import asyncio
import contextlib
import functools
import inspect
import json
import os
import re
import signal
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from unittest import mock

import httpx
import pytest
from structlog.testing import capture_logs

from hermes.adapters.notifier.telegram_adapter import TelegramAdapter
from hermes.domain.events.base import DomainEvent
from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.kline import KlineInterval
from hermes.entrypoints import serve as serve_module
from tests.integration.scenarios import markets
from tests.support.fake_market import harness
from tests.support.fake_market.exchange import FakeExchange, SimClock
from tests.support.serve_loop import PROJECT_ROOT, RecordingChannel, events

pytestmark = [pytest.mark.integration, pytest.mark.chaos]

_UNIT = PROJECT_ROOT / "deploy" / "systemd" / "hermes.service"
_SYSTEMD_DEFAULT_STOP_SEC = 90.0
"""systemd's ``DefaultTimeoutStopSec``, which applies when a unit sets none."""
_TURNS_A_REAL_SLEEP_YIELDS = 3
_TELEGRAM = "telegram"


@dataclass
class _Stop:
    """What happened between the SIGTERM and serve's exit."""

    exit_code: int = -1
    signalled_at: datetime | None = None
    bars_started_after: list[tuple[str, str]] = field(default_factory=list)
    calls_after: list[str] = field(default_factory=list)
    cursors: dict[str, datetime] = field(default_factory=dict)


@functools.cache
def _scenario() -> markets.Scenario:
    return markets.chandelier_exit()


def _is_breakout(bar: Bar) -> bool:
    return (
        bar.pair == markets.BTC
        and bar.interval is KlineInterval.H4
        and bar.close_time == _scenario().breakout_at
    )


def _stop_timeout_seconds() -> float:
    """Return the unit's ``TimeoutStopSec`` in seconds, or systemd's default when unset."""
    found = re.search(r"^TimeoutStopSec=(\d+)s?$", _UNIT.read_text(encoding="utf-8"), re.MULTILINE)
    return float(found.group(1)) if found else _SYSTEMD_DEFAULT_STOP_SEC


def _call_timeout_seconds(call: str) -> float:
    """Return the longest one call can take before its client gives up."""
    config = harness.production_config()
    if call == _TELEGRAM:
        return float(inspect.signature(TelegramAdapter.create).parameters["timeout_sec"].default)
    if call.startswith(httpx.URL(config.frankfurter.base_url).host):
        return config.frankfurter.timeout_sec
    return config.market_data.timeout_sec


def _cursors(db: Path) -> dict[str, datetime]:
    """Return the close time of the last 4h bar each pair's cursor records."""
    last: dict[str, datetime] = {}
    for row in events(db, "BarProcessed"):
        payload = json.loads(row["payload_json"])
        if payload["interval"] == KlineInterval.H4.value:
            pair = f"{payload['pair']['base']}/{payload['pair']['quote']}"
            close = datetime.fromisoformat(payload["occurred_at"])
            last[pair] = max(last.get(pair, close), close)
    return last


@contextlib.contextmanager
def _sigterm_as_the_breakout_starts(stop: _Stop) -> Iterator[None]:
    """Send SIGTERM as the breakout bar starts; record every bar and call after it."""
    handle = serve_module._BarLoop.handle
    answer = FakeExchange.handle
    sleep = SimClock.sleep

    async def handle_bar(self: serve_module._BarLoop, bar: Bar) -> None:
        if stop.signalled_at is not None:
            stop.bars_started_after.append((str(bar.pair), bar.interval.value))
        elif _is_breakout(bar):
            stop.signalled_at = self.c.clock.now()
            os.kill(os.getpid(), signal.SIGTERM)
        await handle(self, bar)

    def answer_request(self: FakeExchange, request: httpx.Request) -> httpx.Response:
        if stop.signalled_at is not None:
            stop.calls_after.append(f"{request.url.host}{request.url.path}")
        return answer(self, request)

    async def real_sleep(self: SimClock, seconds: float) -> None:
        await sleep(self, seconds)
        for _ in range(_TURNS_A_REAL_SLEEP_YIELDS):
            await asyncio.sleep(0)

    class Channel(RecordingChannel):
        def notify(self, event: DomainEvent) -> None:
            if stop.signalled_at is not None:
                stop.calls_after.append(_TELEGRAM)
            super().notify(event)

    async def drive(composition: serve_module.Composition, clock: SimClock) -> int:
        shutdown = asyncio.Event()
        serve_module.install_signal_handlers(asyncio.get_running_loop(), shutdown)
        task = asyncio.create_task(serve_module.run(composition, shutdown))
        finished = asyncio.create_task(clock.finished.wait())
        await asyncio.wait({task, finished}, return_when=asyncio.FIRST_COMPLETED)
        shutdown.set()
        finished.cancel()
        return await task

    dispositions = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)}
    try:
        with (
            mock.patch.object(serve_module._BarLoop, "handle", handle_bar),
            mock.patch.object(FakeExchange, "handle", answer_request),
            mock.patch.object(SimClock, "sleep", real_sleep),
            mock.patch.object(harness, "RecordingChannel", Channel),
            mock.patch.object(harness, "_drive", drive),
        ):
            yield
    finally:
        for sig, disposition in dispositions.items():
            signal.signal(sig, disposition)


@pytest.fixture(scope="module")
def stopped(tmp_path_factory: pytest.TempPathFactory) -> _Stop:
    """Run the scenario once with SIGTERM sent as the breakout bar starts."""
    db = tmp_path_factory.mktemp("sigterm") / "ledger.sqlite3"
    stop = _Stop()
    scenario = _scenario()
    with capture_logs(), _sigterm_as_the_breakout_starts(stop):
        stop.exit_code = harness.serve(scenario.market, db, live_from=scenario.live_from).exit_code
    stop.cursors = _cursors(db)
    return stop


def test_sigterm_mid_bar_finishes_the_poll_in_flight_and_exits_zero(stopped: _Stop) -> None:
    """Every pair's 4h cursor stops at the breakout close: that bar whole, no later poll."""
    close = _scenario().breakout_at
    assert (stopped.exit_code, stopped.cursors) == (
        0,
        {"BTC/USDT": close, "ETH/USDT": close, "SOL/USDT": close},
    )


def test_sigterm_mid_bar_exits_before_systemd_s_stop_timeout_even_when_every_call_is_slow(
    stopped: _Stop,
) -> None:
    """The calls left after SIGTERM, each at its full timeout, end inside TimeoutStopSec."""
    worst_case = sum(_call_timeout_seconds(call) for call in stopped.calls_after)
    assert (stopped.calls_after, worst_case < _stop_timeout_seconds()) == (
        [
            "data-api.binance.vision/api/v3/depth",
            _TELEGRAM,
        ],
        True,
    )
