"""A fake Binance and Frankfurter that answer on a simulated clock.

:class:`FakeExchange` is an ``httpx`` request handler. The production adapters
keep their real base URLs (``https://data-api.binance.vision`` and the
configured Frankfurter host) and send their real requests; only the transport
under the client is swapped, so no socket is opened.

What a request sees depends on :class:`SimClock`'s ``now``, as on the real
host: klines list every candle that has opened, the last one still forming;
server time is ``now``; the depth book sits around the latest closed 4h close.

On the testnet host (``testnet.binance.vision``) the same public endpoints
answer, and :attr:`FakeExchange.spot` matches signed orders against that book
(:mod:`tests.support.fake_market.spot`).
"""

from __future__ import annotations

import asyncio
import bisect
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal

import httpx

from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.value_objects.pair import Pair
from tests.support.fake_market import wire
from tests.support.fake_market.script import Market
from tests.support.fake_market.spot import SPOT_HOST, SPOT_PATHS, SpotAccount

BINANCE_HOST = "data-api.binance.vision"
DEPTH_LEVELS_MAX = 5000
_KLINES_LIMIT_MAX = 1000
_KLINES_LIMIT_DEFAULT = 500
_WEIGHTS = {"/api/v3/klines": 2, "/api/v3/time": 1, "/api/v3/depth": 5}


class SimClock:
    """A clock the adapters' injected ``sleep`` moves forward.

    It is the serve composition's :class:`~hermes.ports.clock.ClockPort`, the
    adapters' rate-limiter clock and their sleep, so a 90-day scenario runs in
    seconds. Once ``now`` reaches ``stop_at`` the next sleep sets ``finished``
    and never returns, which leaves the feed idle like a quiet market.
    """

    def __init__(self, start: datetime, stop_at: datetime) -> None:
        """Start at ``start``; finish at ``stop_at``."""
        self.current = start
        self.stop_at = stop_at
        self.finished = asyncio.Event()
        self.slept = timedelta(0)

    def now(self) -> datetime:
        """Return the simulated instant."""
        return self.current

    def monotonic(self) -> float:
        """Return the simulated instant in seconds, for rate limiters."""
        return self.current.timestamp()

    async def sleep(self, seconds: float) -> None:
        """Advance the clock by ``seconds`` instead of waiting."""
        if self.current >= self.stop_at:
            self.finished.set()
            await asyncio.Event().wait()
        step = timedelta(seconds=max(seconds, 0.001))
        self.current += step
        self.slept += step
        await asyncio.sleep(0)


@dataclass
class FakeExchange:
    """Answers Binance public market data and Frankfurter rates for ``market``.

    Attributes:
        market: The scripted bars.
        clock: The simulated clock requests are answered at.
        fx_host: Host of the configured Frankfurter base URL.
        fx_prefix: Path prefix of that base URL (``/v1`` in ``config/``).
        eur_usd: Euro rate in dollars Frankfurter reports for every weekday.
        book_outages: ``(start, end)`` windows when depth answers HTTP 503.
        book_step_bps: Gap between depth levels; wider means a thinner book.
        late_bars: ``(pair, interval, open_time, shown_from)``: the bar is left
            out of klines answers until ``shown_from``, as if published late.
        corrupt_bars: ``(pair, interval, open_time, broken_from, clean_from)``:
            the bar's close is served as ``"NaN"`` from ``broken_from`` until
            ``clean_from``, as if the feed published a broken row and
            corrected it later.
        fx_down: When ``True`` Frankfurter answers HTTP 503.
        klines_outages: ``(start, end)`` windows when klines answer HTTP 503.
        fx_outages: ``(start, end)`` windows when Frankfurter answers HTTP 503.
        duplicate_bars: ``(pair, interval, open_time)``: the bar's row appears
            twice in every klines answer that holds it.
        requests: Every request served, as ``(host, path, params)``.
        spot: The account signed order requests on the testnet host trade in.

    """

    market: Market
    clock: SimClock
    fx_host: str = "api.frankfurter.dev"
    fx_prefix: str = "/v1"
    eur_usd: Decimal = Decimal("1.10")
    book_outages: list[tuple[datetime, datetime]] = field(default_factory=list)
    book_step_bps: Decimal = Decimal(1)
    late_bars: list[tuple[Pair, KlineInterval, datetime, datetime]] = field(default_factory=list)
    corrupt_bars: list[tuple[Pair, KlineInterval, datetime, datetime, datetime]] = field(
        default_factory=list
    )
    fx_down: bool = False
    klines_outages: list[tuple[datetime, datetime]] = field(default_factory=list)
    fx_outages: list[tuple[datetime, datetime]] = field(default_factory=list)
    duplicate_bars: list[tuple[Pair, KlineInterval, datetime]] = field(default_factory=list)
    requests: list[tuple[str, str, dict[str, str]]] = field(default_factory=list)
    spot: SpotAccount = field(default_factory=SpotAccount)
    _update_id: int = 1
    _index: dict[tuple[Pair, KlineInterval], _Stream] = field(default_factory=dict)

    def _stream(self, pair: Pair, interval: KlineInterval) -> _Stream:
        """Return the stream's bars with their open and close times, for bisection.

        A 90-day scenario serves thousands of klines requests over hundreds of
        bars each; scanning every bar per request was most of a run's time.
        """
        stream = self._index.get((pair, interval))
        if stream is None:
            bars = self.market.bars(pair, interval)
            stream = _Stream(
                bars=bars,
                open_ms=[wire.ms(b.open_time) for b in bars],
                open_times=[b.open_time for b in bars],
                close_times=[b.close_time for b in bars],
            )
            self._index[(pair, interval)] = stream
        return stream

    def load(self, market: Market) -> None:
        """Answer from ``market`` from now on, dropping the old market's index."""
        self.market = market
        self._index.clear()

    def transport(self) -> httpx.MockTransport:
        """Return a transport for an ``httpx.Client`` or ``httpx.AsyncClient``."""
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        """Answer one request the way the real host would."""
        params = dict(request.url.params)
        host, path = request.url.host, request.url.path
        self.requests.append((host, path, params))
        if host == SPOT_HOST and path in SPOT_PATHS:
            return self.spot.handle(request, self)
        if host in {BINANCE_HOST, SPOT_HOST}:
            return self._binance(path, params)
        if host == self.fx_host and path.startswith(self.fx_prefix):
            return self._frankfurter(path.removeprefix(self.fx_prefix), params)
        return httpx.Response(404, text=f"fake exchange: no route for {host}{path}")

    # -- what the spot account reads ------------------------------------

    def now(self) -> datetime:
        """Return the scripted instant."""
        return self.clock.now()

    def pair_for(self, symbol: str) -> Pair | None:
        """Return the scripted pair Binance names ``symbol``, if any."""
        return self._pair(symbol)

    def last_close(self, pair: Pair) -> Decimal | None:
        """Return the close of the latest closed 4h bar of ``pair``, if any has closed."""
        stream = self._stream(pair, KlineInterval.H4)
        closed = bisect.bisect_left(stream.close_times, self.clock.now())
        return stream.bars[closed - 1].close if closed else None

    def closed_h4(self, pair: Pair, opened_from: datetime) -> list[Bar]:
        """Return the closed 4h bars of ``pair`` opened at or after ``opened_from``."""
        stream = self._stream(pair, KlineInterval.H4)
        first = bisect.bisect_left(stream.open_times, opened_from)
        closed = bisect.bisect_left(stream.close_times, self.clock.now())
        return stream.bars[first:closed]

    # -- Binance ---------------------------------------------------------

    def _binance(self, path: str, params: dict[str, str]) -> httpx.Response:
        handlers: dict[str, Callable[[dict[str, str]], httpx.Response]] = {
            "/api/v3/time": lambda _: _json(wire.server_time(self.clock.now())),
            "/api/v3/klines": self._klines,
            "/api/v3/depth": self._depth,
        }
        handler = handlers.get(path)
        if handler is None:
            return _binance_error(404, -1, f"unknown path {path}")
        response = handler(params)
        response.headers["x-mbx-used-weight-1m"] = str(_WEIGHTS[path])
        return response

    def _pair(self, symbol: str | None) -> Pair | None:
        for pair in self.market.h4:
            if symbol == f"{pair.base.value}{pair.quote.value}":
                return pair
        return None

    def _klines(self, params: dict[str, str]) -> httpx.Response:
        pair = self._pair(params.get("symbol"))
        if pair is None:
            return _binance_error(400, -1121, "Invalid symbol.")
        try:
            interval = KlineInterval(params.get("interval", ""))
        except ValueError:
            return _binance_error(400, -1120, "Invalid interval.")
        limit = min(int(params.get("limit", _KLINES_LIMIT_DEFAULT)), _KLINES_LIMIT_MAX)
        now = self.clock.now()
        if _inside(now, self.klines_outages):
            return httpx.Response(503, text="Service Unavailable")
        hidden = {
            open_time
            for p, i, open_time, shown_from in self.late_bars
            if (p, i) == (pair, interval) and now < shown_from
        }
        stream = self._stream(pair, interval)
        opened_until = bisect.bisect_right(stream.open_times, now)
        if "startTime" in params:
            first = bisect.bisect_left(stream.open_ms, int(params["startTime"]), hi=opened_until)
        else:
            first = max(0, opened_until - limit - len(hidden))
        rows = [b for b in stream.bars[first:opened_until] if b.open_time not in hidden]
        rows = rows[:limit] if "startTime" in params else rows[-limit:]
        doubled = {
            open_time for p, i, open_time in self.duplicate_bars if (p, i) == (pair, interval)
        }
        broken = {
            open_time
            for p, i, open_time, broken_from, clean_from in self.corrupt_bars
            if (p, i) == (pair, interval) and broken_from <= now < clean_from
        }
        # The forming candle is served with its scripted values: the adapter
        # drops it by the closed-bar rule, so its numbers are never read.
        return _json(
            [
                row
                for b in rows
                for row in [_row(b, broken=b.open_time in broken)]
                * (2 if b.open_time in doubled else 1)
            ]
        )

    def _depth(self, params: dict[str, str]) -> httpx.Response:
        pair = self._pair(params.get("symbol"))
        if pair is None:
            return _binance_error(400, -1121, "Invalid symbol.")
        now = self.clock.now()
        if _inside(now, self.book_outages):
            return httpx.Response(503, text="Service Unavailable")
        stream = self._stream(pair, KlineInterval.H4)
        closed = bisect.bisect_left(stream.close_times, now)
        if closed == 0:
            return _binance_error(400, -1121, "Invalid symbol.")
        self._update_id += 1
        levels = min(int(params.get("limit", 100)), DEPTH_LEVELS_MAX)
        return _json(
            wire.depth(
                stream.bars[closed - 1].close,
                levels=levels,
                update_id=self._update_id,
                step_bps=self.book_step_bps,
            )
        )

    # -- Frankfurter -----------------------------------------------------

    def _frankfurter(self, path: str, params: dict[str, str]) -> httpx.Response:
        if self.fx_down or _inside(self.clock.now(), self.fx_outages):
            return httpx.Response(503, text="Service Unavailable")
        base, quote = params.get("from"), params.get("to")
        if base != "EUR" or quote != "USD":
            return _json({"message": "not found"}, status=404)
        day = self.clock.now().date() if path == "/latest" else date.fromisoformat(path[1:])
        while day.weekday() >= 5:  # the ECB publishes on weekdays; weekends answer Friday
            day -= timedelta(days=1)
        return _json(wire.fx_rate("EUR", "USD", day, self.eur_usd))


@dataclass(frozen=True)
class _Stream:
    """One stream's bars and their times, oldest first."""

    bars: list[Bar]
    open_ms: list[int]
    open_times: list[datetime]
    close_times: list[datetime]


def _inside(now: datetime, windows: list[tuple[datetime, datetime]]) -> bool:
    return any(start <= now < end for start, end in windows)


def _row(bar: Bar, *, broken: bool) -> list[object]:
    """Return the klines row for ``bar``, with a ``"NaN"`` close when ``broken``."""
    row = wire.kline_row(bar)
    return [*row[:4], "NaN", *row[5:]] if broken else row


def _json(body: object, status: int = 200) -> httpx.Response:
    return httpx.Response(
        status, content=json.dumps(body).encode(), headers={"content-type": "application/json"}
    )


def _binance_error(status: int, code: int, msg: str) -> httpx.Response:
    return _json({"code": code, "msg": msg}, status=status)
