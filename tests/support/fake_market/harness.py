"""Run the production ``hermes serve`` composition against the fake market.

Everything is what ``hermes serve`` wires from the repository's ``config/``:
the pre-registration, the three pairs, the swing strategy at its signed
parameters, the fill kernel, the ledger, and the real Binance klines, Binance
depth and Frankfurter adapters with their real base URLs. Three things are
swapped: the HTTP transport under those adapters (the fake exchange), the
clock (simulated), and the Telegram channel (a recorder). No order path exists
in serve, so nothing can trade (ADR-003 §00 I-08).
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import functools
import sqlite3
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from unittest import mock

import httpx

from hermes.adapters._shared.rate_limiter import RateLimitConfig, RateLimiter
from hermes.adapters.fx.frankfurter import FrankfurterAdapter
from hermes.adapters.fx.frankfurter import FrankfurterConfig as FrankfurterWireConfig
from hermes.adapters.market_data.binance_depth_snapshot import BinanceDepthSnapshotAdapter
from hermes.adapters.market_data.binance_live_klines import BinanceLiveKlinesAdapter
from hermes.application.notification_router import RouterConfig
from hermes.application.signal_notifier import SignalNotifier
from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.value_objects.pair import Pair
from hermes.entrypoints import serve as serve_module
from hermes.infrastructure.config.loader import load_config
from hermes.infrastructure.config.root import HermesConfig
from tests.support.fake_market.exchange import FakeExchange, SimClock
from tests.support.fake_market.script import Market
from tests.support.serve_loop import PROJECT_ROOT, RecordingChannel

_RUN_TIMEOUT_SECONDS = 120

type AfterBar = Callable[[Bar, datetime, sqlite3.Connection], None]
"""Called after serve handled a bar: the bar, the simulated time, serve's ledger connection."""


@dataclass
class ServeRun:
    """What one run against the fake market produced.

    Attributes:
        exit_code: The exit code of :func:`hermes.entrypoints.serve.run`.
        channel: The Telegram stand-in, holding every event notified.
        exchange: The fake exchange, holding every request served.
        clock: The simulated clock, at the instant the run stopped.
        bar_seconds: CPU seconds the event loop's thread spent handling each bar, in order.

    """

    exit_code: int
    channel: RecordingChannel
    exchange: FakeExchange
    clock: SimClock
    bar_seconds: list[float] = field(default_factory=list)


@functools.cache
def production_config() -> HermesConfig:
    """Load and startup-check the repository's ``config/`` with no secrets, once per process.

    The config is frozen, and a generated scenario boots serve a dozen times.
    """
    config = load_config(PROJECT_ROOT / "config", env={})
    serve_module.check_startup(config, env={})
    return config


def compose(
    config: HermesConfig, db: Path, exchange: FakeExchange, channel: RecordingChannel
) -> serve_module.Composition:
    """Build the serve composition with the real adapters over the fake transport."""
    clock = exchange.clock
    md = config.market_data
    feed = BinanceLiveKlinesAdapter(
        config=md,
        clock=clock,
        client=httpx.AsyncClient(transport=exchange.transport(), timeout=md.timeout_sec),
        limiter=RateLimiter(
            RateLimitConfig(
                max_weight=md.rate_limit_max_weight, window_seconds=md.rate_limit_window_seconds
            ),
            clock=clock.monotonic,
        ),
        sleep=clock.sleep,
    )
    books = BinanceDepthSnapshotAdapter(
        config=md,
        clock=clock,
        client=httpx.AsyncClient(transport=exchange.transport(), timeout=md.timeout_sec),
        limiter=RateLimiter(
            RateLimitConfig(
                max_weight=md.rate_limit_max_weight, window_seconds=md.rate_limit_window_seconds
            ),
            clock=clock.monotonic,
        ),
        sleep=clock.sleep,
    )
    fx_config = config.frankfurter
    fx = FrankfurterAdapter(
        config=FrankfurterWireConfig(
            base_url=fx_config.base_url,
            timeout_sec=fx_config.timeout_sec,
            wire_codes=FrankfurterWireConfig.USDT_AS_USD,
        ),
        client=httpx.Client(
            base_url=fx_config.base_url,
            transport=exchange.transport(),
            timeout=fx_config.timeout_sec,
        ),
        limiter=RateLimiter(
            config=RateLimitConfig(max_weight=fx_config.requests_per_minute, window_seconds=60),
            clock=clock.monotonic,
            sleep=lambda _seconds: None,
        ),
    )
    composition = serve_module.build_composition(
        config,
        db_path=db,
        env={},
        feed=feed,
        clock=clock,
        orderbooks=books,
        fx=fx,
        new_ledger=True,
    )
    # Serve commits several times per bar, and each commit waits for the disk: a 90-day
    # run made about 25,800 fsync calls. Cheap here, but on a loaded runner's virtual
    # disk they took the run past its timeout. A test ledger needs no crash durability;
    # what serve reads and writes is unchanged.
    composition.sqlite_connection.execute("PRAGMA synchronous = OFF")
    return dataclasses.replace(
        composition, signal_notifier=SignalNotifier(RouterConfig(telegram=channel, slack=None))
    )


def serve(
    market: Market,
    db: Path,
    *,
    live_from: datetime,
    until: datetime | None = None,
    book_outages: list[tuple[datetime, datetime]] | None = None,
    book_step_bps: Decimal = Decimal(1),
    late_bars: list[tuple[Pair, KlineInterval, datetime, datetime]] | None = None,
    corrupt_bars: list[tuple[Pair, KlineInterval, datetime, datetime, datetime]] | None = None,
    fx_down: bool = False,
    notifier_error: Exception | None = None,
    klines_outages: list[tuple[datetime, datetime]] | None = None,
    fx_outages: list[tuple[datetime, datetime]] | None = None,
    duplicate_bars: list[tuple[Pair, KlineInterval, datetime]] | None = None,
    after_bar: AfterBar | None = None,
) -> ServeRun:
    """Boot ``hermes serve`` at ``live_from`` and run it on the fake market.

    The run stops when the simulated clock reaches ``until`` (default: the
    end of the script) and the feed goes idle, or when serve fails.

    Args:
        market: The scripted market.
        db: Ledger file; reuse it across calls to restart over the same ledger.
        live_from: Boot instant. Warm-up sees every bar closed by then.
        until: Stop instant.
        book_outages: Windows when the depth endpoint answers HTTP 503.
        book_step_bps: Gap between depth levels; wider means a thinner book.
        late_bars: Bars the klines endpoint publishes late; see ``FakeExchange``.
        corrupt_bars: Bars served with a NaN close for a while; see ``FakeExchange``.
        fx_down: Make Frankfurter answer HTTP 503 for the whole run.
        notifier_error: Make every Telegram send raise this.
        klines_outages: Windows when the klines endpoint answers HTTP 503.
        fx_outages: Windows when Frankfurter answers HTTP 503.
        duplicate_bars: Bars whose row the klines endpoint sends twice.
        after_bar: Called after each bar serve's loop handled without raising,
            outside the timed part, with serve's own ledger connection so
            rows it has not committed yet are visible.

    Returns:
        The run's exit code, notifications, requests and final clock.

    """
    clock = SimClock(live_from, until if until is not None else market.end)
    fx_url = httpx.URL(production_config().frankfurter.base_url)
    exchange = FakeExchange(
        market=market,
        clock=clock,
        fx_host=fx_url.host,
        fx_prefix=fx_url.path.rstrip("/"),
        book_outages=book_outages or [],
        book_step_bps=book_step_bps,
        late_bars=late_bars or [],
        corrupt_bars=corrupt_bars or [],
        fx_down=fx_down,
        klines_outages=klines_outages or [],
        fx_outages=fx_outages or [],
        duplicate_bars=duplicate_bars or [],
    )
    channel = RecordingChannel(raises=notifier_error)
    composition = compose(production_config(), db, exchange, channel)
    bar_seconds: list[float] = []
    with _timed_bars(bar_seconds, after_bar):
        code = asyncio.run(_drive(composition, clock))
    return ServeRun(
        exit_code=code, channel=channel, exchange=exchange, clock=clock, bar_seconds=bar_seconds
    )


@contextlib.contextmanager
def _timed_bars(into: list[float], after_bar: AfterBar | None = None) -> Iterator[None]:
    """Record how long each bar takes in serve's bar loop.

    The per-bar cost is what decides whether serve keeps up with the feed, and
    the bar loop has no public hook for it, so the handler is wrapped for the
    length of one run (issue #689 time budget).

    It records the CPU time of the event loop's thread, not wall time: on a
    shared CI runner other jobs take the CPU away mid-bar, which stretched the
    wall time of the same bars from 4 ms to over 30 ms. Work serve hands to a
    worker thread (the notifier send) is not counted; it does not hold up the
    loop.
    """
    original = serve_module._BarLoop.handle

    async def timed(self: serve_module._BarLoop, bar: Bar) -> None:
        started = time.thread_time()
        try:
            await original(self, bar)
        finally:
            into.append(time.thread_time() - started)
        if after_bar is not None:
            after_bar(bar, self.c.clock.now(), self.c.sqlite_connection)

    with mock.patch.object(serve_module._BarLoop, "handle", timed):
        yield


async def _drive(composition: serve_module.Composition, clock: SimClock) -> int:
    shutdown = asyncio.Event()
    task = asyncio.create_task(serve_module.run(composition, shutdown))
    finished = asyncio.create_task(clock.finished.wait())
    await asyncio.wait(
        {task, finished}, timeout=_RUN_TIMEOUT_SECONDS, return_when=asyncio.FIRST_COMPLETED
    )
    shutdown.set()
    finished.cancel()
    return await asyncio.wait_for(task, timeout=_RUN_TIMEOUT_SECONDS)
