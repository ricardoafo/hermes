"""Deterministic stand-in for the public klines host, for live-feed tests.

:class:`FakeBinancePublic` answers ``/api/v3/time`` and ``/api/v3/klines`` from
a :class:`~hermes.adapters.clock.SimulatedClock`, so a test advances time and
the "exchange" publishes the candles a real one would: the forming candle is
the last row, closed candles appear after their boundary, and ``startTime`` is
honoured. Knobs inject the faults the feed must survive: outages, skipped
candles, a lagging or skewed exchange clock, an exchange that ignores
``startTime`` (overlapping polls) and scripted one-off responses.

:class:`SimulatedSleep` replaces ``anyio.sleep``: it advances the same clock
and raises :class:`StopSimulation` once the clock passes ``stop_at``, which is
how :func:`run_stream` ends an otherwise endless stream without waiting.

Every candle is a pure function of ``(symbol, interval, open_time)``, so two
runs over different fault schedules can be compared bar for bar.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import anyio
import httpx
import respx

from hermes.adapters.clock import SimulatedClock
from hermes.adapters.exchanges.rate_limiter import RateLimitConfig, RateLimiter
from hermes.adapters.market_data.binance_live_klines import BinanceLiveKlinesAdapter
from hermes.domain.errors import InfraError
from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.bar_schedule import (
    interval_duration,
    next_boundary,
    previous_boundary,
)
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair
from hermes.infrastructure.config.models import MarketDataConfig

BASE_URL = "https://data-api.binance.vision"
TIME_URL = f"{BASE_URL}/api/v3/time"
KLINES_URL = f"{BASE_URL}/api/v3/klines"

BTC_USDT = Pair(base=Currency.BTC, quote=Currency.USDT)
ETH_USDT = Pair(base=Currency.ETH, quote=Currency.USDT)
SOL_USDT = Pair(base=Currency.SOL, quote=Currency.USDT)
PAIRS = (BTC_USDT, ETH_USDT, SOL_USDT)
INTERVALS = (KlineInterval.H4, KlineInterval.D1)

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_ONE_MS = timedelta(milliseconds=1)

# 2026-09-30 23:00 UTC: one hour before a day boundary, so a two-day run
# crosses midnights where the 1d and 4h bars close at the same instant.
START = datetime(2026, 9, 30, 23, 0, tzinfo=UTC)

# One scripted reply: a response to return or an exception to raise.
Scripted = httpx.Response | Exception


class StopSimulation(Exception):  # noqa: N818 — test control flow, not an error
    """Raised by :class:`SimulatedSleep` once the clock passes ``stop_at``."""


def to_ms(moment: datetime) -> int:
    """Return exact milliseconds since the UTC epoch."""
    return (moment - _EPOCH) // _ONE_MS


def row_for(symbol: str, interval: KlineInterval, open_time: datetime) -> list[object]:
    """Return the 12-field klines row for one candle, a pure function of its identity."""
    seed = 1_000 + (to_ms(open_time) // 60_000) % 997 + len(symbol) + len(interval.value)
    close_time = open_time + interval_duration(interval) - _ONE_MS
    return [
        to_ms(open_time),
        f"{seed}.00000000",
        f"{seed + 5}.00000000",
        f"{seed - 5}.00000000",
        f"{seed + 1}.00000000",
        "12.50000000",
        to_ms(close_time),
        "15000.00000000",
        321,
        "6.25000000",
        "7500.00000000",
        "0",
    ]


@dataclass
class FakeBinancePublic:
    """Simulated public klines host bound to a :class:`SimulatedClock`.

    Attributes:
        clock: The shared simulated clock; the exchange clock is this plus
            :attr:`server_offset`.
        server_offset: Exchange clock minus local clock (skew injection).
        outages: ``[start, end)`` local-time windows answered with HTTP 503.
        missing: ``(interval, open_time)`` candles the exchange never publishes.
        lag: Extra time after a boundary before a closed candle is visible.
        time_latency: Round-trip time of a ``/time`` call; the exchange clock is
            read at its midpoint and the simulated clock advances by the full amount.
        ignore_start_time: Return the latest few candles whatever ``startTime``
            says, which makes consecutive polls overlap.
        time_script: Replies served, in order, before normal ``/time`` service.
        klines_script: Replies served, in order, before normal ``/klines`` service.
        requests: Every request received, in order.

    """

    clock: SimulatedClock
    server_offset: timedelta = timedelta(0)
    outages: list[tuple[datetime, datetime]] = field(default_factory=list)
    missing: set[tuple[str, datetime]] = field(default_factory=set)
    lag: timedelta = timedelta(0)
    time_latency: timedelta = timedelta(0)
    ignore_start_time: bool = False
    time_script: list[Scripted] = field(default_factory=list)
    klines_script: list[Scripted] = field(default_factory=list)
    requests: list[httpx.Request] = field(default_factory=list)

    def install(self, router: respx.MockRouter) -> None:
        """Register the two routes on ``router``."""
        router.get(TIME_URL).mock(side_effect=self._time)
        router.get(KLINES_URL).mock(side_effect=self._klines)

    def klines_requests(self) -> list[httpx.Request]:
        """Return the klines requests received so far."""
        return [r for r in self.requests if r.url.path == "/api/v3/klines"]

    def _server_now(self) -> datetime:
        return self.clock.now() + self.server_offset

    def _down(self) -> bool:
        now = self.clock.now()
        return any(start <= now < end for start, end in self.outages)

    @staticmethod
    def _scripted(script: list[Scripted]) -> httpx.Response | None:
        if not script:
            return None
        reply = script.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    def _time(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        scripted = self._scripted(self.time_script)
        if scripted is not None:
            return scripted
        if self._down():
            return httpx.Response(503, text="Service Unavailable")
        server_now = self._server_now() + self.time_latency / 2
        self.clock.advance(self.time_latency)
        return httpx.Response(
            200,
            json={"serverTime": to_ms(server_now)},
            headers={"x-mbx-used-weight-1m": "1"},
        )

    def _first_open(
        self, params: Mapping[str, str], interval: KlineInterval, limit: int
    ) -> datetime:
        server_now = self._server_now()
        duration = interval_duration(interval)
        if self.ignore_start_time or "startTime" not in params:
            overlap = 4 if self.ignore_start_time else limit - 1
            return previous_boundary(server_now, interval) - overlap * duration
        start = _EPOCH + timedelta(milliseconds=int(params["startTime"]))
        return (
            start if previous_boundary(start, interval) == start else next_boundary(start, interval)
        )

    def _klines(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        scripted = self._scripted(self.klines_script)
        if scripted is not None:
            return scripted
        if self._down():
            return httpx.Response(503, text="Service Unavailable")
        params = dict(request.url.params)
        interval = KlineInterval(params["interval"])
        duration = interval_duration(interval)
        limit = int(params.get("limit", "500"))
        server_now = self._server_now()
        rows: list[list[object]] = []
        open_time = self._first_open(params, interval, limit)
        while len(rows) < limit and open_time <= server_now:
            forming = open_time + duration > server_now
            published = server_now >= open_time + duration + self.lag
            if (forming or published) and (interval.value, open_time) not in self.missing:
                rows.append(row_for(params["symbol"], interval, open_time))
            open_time += duration
        return httpx.Response(200, json=rows, headers={"x-mbx-used-weight-1m": "2"})


@dataclass
class SimulatedSleep:
    """``anyio.sleep`` replacement that advances a simulated clock.

    Attributes:
        clock: Clock to advance.
        stop_at: Raise :class:`StopSimulation` once the clock is later than this.
        calls: Every requested duration, in seconds, in order.
        hook: Called after every sleep, for sampling state mid-run.

    """

    clock: SimulatedClock
    stop_at: datetime | None = None
    calls: list[float] = field(default_factory=list)
    hook: Callable[[], None] | None = None

    async def __call__(self, seconds: float) -> None:
        """Advance the clock by ``seconds`` and stop the run once past ``stop_at``."""
        self.calls.append(seconds)
        self.clock.advance(timedelta(seconds=seconds))
        if self.hook is not None:
            self.hook()
        if self.stop_at is not None and self.clock.now() > self.stop_at:
            raise StopSimulation


def build_adapter(
    clock: SimulatedClock,
    sleeper: SimulatedSleep,
    *,
    config: MarketDataConfig | None = None,
    limiter: RateLimiter | None = None,
) -> BinanceLiveKlinesAdapter:
    """Build an adapter on a fresh client, with a bucket driven by the simulated clock."""
    cfg = config or MarketDataConfig()
    bucket = limiter or RateLimiter.for_testing(
        RateLimitConfig(max_weight=cfg.rate_limit_max_weight, window_seconds=60),
        clock=lambda: clock.now().timestamp(),
        sleep=lambda seconds: None,
    )
    return BinanceLiveKlinesAdapter(
        config=cfg,
        clock=clock,
        client=httpx.AsyncClient(timeout=cfg.timeout_sec),
        limiter=bucket,
        sleep=sleeper,
    )


@dataclass
class StreamRun:
    """Result of :func:`run_stream`.

    Attributes:
        bars: Delivered bars in delivery order.
        received_at: Simulated local time at which each bar was delivered.
        warm_bars: Bars returned by each requested warm-up, per stream.
        adapter: The adapter, for health inspection after the run.
        sleeper: The sleeper, for cadence assertions.
        error: The :class:`InfraError` that ended the stream, if any.

    """

    bars: list[Bar]
    received_at: list[datetime]
    warm_bars: dict[tuple[Pair, KlineInterval], list[Bar]]
    adapter: BinanceLiveKlinesAdapter
    sleeper: SimulatedSleep
    error: InfraError | None


def run_stream(
    fake: FakeBinancePublic,
    *,
    until: datetime,
    pairs: Sequence[Pair] = PAIRS,
    intervals: Sequence[KlineInterval] = INTERVALS,
    resume_after: Mapping[tuple[Pair, KlineInterval], datetime] | None = None,
    config: MarketDataConfig | None = None,
    warm_up: Mapping[tuple[Pair, KlineInterval], int] | None = None,
    limiter: RateLimiter | None = None,
    states: list[str] | None = None,
    single: tuple[Pair, KlineInterval] | None = None,
) -> StreamRun:
    """Stream from the fake exchange until the simulated clock passes ``until``.

    Args:
        fake: The fake exchange (its clock drives the run).
        until: Stop once the simulated clock is later than this.
        pairs: Pairs to follow.
        intervals: Intervals to follow.
        resume_after: Optional per-stream resume markers.
        config: Optional market-data config.
        warm_up: Optional ``{(pair, interval): count}`` warm-ups to run first.
        limiter: Optional rate limiter.
        states: When given, the health state seen after every sleep is appended.
        single: Follow one stream through ``stream_bars`` instead.

    Returns:
        The :class:`StreamRun`. A permanent failure ends the stream and is
        reported in ``error`` rather than raised.

    """
    sleeper = SimulatedSleep(fake.clock)

    async def _drive() -> StreamRun:
        with respx.mock(assert_all_called=False) as router:
            fake.install(router)
            adapter = build_adapter(fake.clock, sleeper, config=config, limiter=limiter)
            if states is not None:
                sleeper.hook = lambda: states.append(adapter.health().state.value)
            warm_bars = {
                stream: await adapter.warm_up(stream[0], stream[1], count)
                for stream, count in (warm_up or {}).items()
            }
            sleeper.stop_at = until
            bars: list[Bar] = []
            received_at: list[datetime] = []
            error: InfraError | None = None
            source = (
                adapter.stream_bars(*single)
                if single is not None
                else adapter.stream_closed_bars(pairs, intervals, resume_after=resume_after)
            )
            try:
                async for bar in source:
                    bars.append(bar)
                    received_at.append(fake.clock.now())
            except StopSimulation:
                pass
            except InfraError as exc:
                error = exc
            return StreamRun(
                bars=bars,
                received_at=received_at,
                warm_bars=warm_bars,
                adapter=adapter,
                sleeper=sleeper,
                error=error,
            )

    return anyio.run(_drive)


def keys(bars: Sequence[Bar]) -> list[tuple[str, str, datetime]]:
    """Return ``(pair, interval, open_time)`` per bar, for sequence comparisons."""
    return [(str(bar.pair), bar.interval.value, bar.open_time) for bar in bars]
