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
import dataclasses
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import httpx

from hermes.adapters.exchanges.rate_limiter import RateLimitConfig, RateLimiter
from hermes.adapters.fx.frankfurter import FrankfurterAdapter
from hermes.adapters.fx.frankfurter import FrankfurterConfig as FrankfurterWireConfig
from hermes.adapters.fx.rate_limiter import RateLimitConfig as FxRateLimitConfig
from hermes.adapters.fx.rate_limiter import RateLimiter as FxRateLimiter
from hermes.adapters.market_data.binance_depth_snapshot import BinanceDepthSnapshotAdapter
from hermes.adapters.market_data.binance_live_klines import BinanceLiveKlinesAdapter
from hermes.application.notification_router import RouterConfig
from hermes.application.signal_notifier import SignalNotifier
from hermes.entrypoints import serve as serve_module
from hermes.infrastructure.config.loader import load_config
from hermes.infrastructure.config.root import HermesConfig
from tests.support.fake_market.exchange import FakeExchange, SimClock
from tests.support.fake_market.script import Market
from tests.support.serve_loop import PROJECT_ROOT, RecordingChannel

_RUN_TIMEOUT_SECONDS = 120


@dataclass
class ServeRun:
    """What one run against the fake market produced.

    Attributes:
        exit_code: The exit code of :func:`hermes.entrypoints.serve.run`.
        channel: The Telegram stand-in, holding every event notified.
        exchange: The fake exchange, holding every request served.
        clock: The simulated clock, at the instant the run stopped.

    """

    exit_code: int
    channel: RecordingChannel
    exchange: FakeExchange
    clock: SimClock


def production_config() -> HermesConfig:
    """Load and startup-check the repository's ``config/`` with no secrets."""
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
        limiter=FxRateLimiter(
            config=FxRateLimitConfig(max_weight=fx_config.requests_per_minute, window_seconds=60),
            clock=clock.monotonic,
            sleep=lambda _seconds: None,
        ),
    )
    composition = serve_module.build_composition(
        config, db_path=db, env={}, feed=feed, clock=clock, orderbooks=books, fx=fx
    )
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
    fx_down: bool = False,
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
        fx_down: Make Frankfurter answer HTTP 503 for the whole run.

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
        fx_down=fx_down,
    )
    channel = RecordingChannel()
    composition = compose(production_config(), db, exchange, channel)
    code = asyncio.run(_drive(composition, clock))
    return ServeRun(exit_code=code, channel=channel, exchange=exchange, clock=clock)


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
