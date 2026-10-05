"""A fake Binance and Frankfurter that answer on a simulated clock.

:class:`FakeExchange` is an ``httpx`` request handler. The production adapters
keep their real base URLs (``https://data-api.binance.vision`` and the
configured Frankfurter host) and send their real requests; only the transport
under the client is swapped, so no socket is opened.

What a request sees depends on :class:`SimClock`'s ``now``, as on the real
host: klines list every candle that has opened, the last one still forming;
server time is ``now``; the depth book sits around the latest closed 4h close.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal

import httpx

from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.value_objects.pair import Pair
from tests.support.fake_market import wire
from tests.support.fake_market.script import Market

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
        fx_down: When ``True`` Frankfurter answers HTTP 503.
        requests: Every request served, as ``(host, path, params)``.

    """

    market: Market
    clock: SimClock
    fx_host: str = "api.frankfurter.dev"
    fx_prefix: str = "/v1"
    eur_usd: Decimal = Decimal("1.10")
    book_outages: list[tuple[datetime, datetime]] = field(default_factory=list)
    fx_down: bool = False
    requests: list[tuple[str, str, dict[str, str]]] = field(default_factory=list)
    _update_id: int = 1

    def transport(self) -> httpx.MockTransport:
        """Return a transport for an ``httpx.Client`` or ``httpx.AsyncClient``."""
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        """Answer one request the way the real host would."""
        params = dict(request.url.params)
        host, path = request.url.host, request.url.path
        self.requests.append((host, path, params))
        if host == BINANCE_HOST:
            return self._binance(path, params)
        if host == self.fx_host and path.startswith(self.fx_prefix):
            return self._frankfurter(path.removeprefix(self.fx_prefix), params)
        return httpx.Response(404, text=f"fake exchange: no route for {host}{path}")

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
        opened = [b for b in self.market.bars(pair, interval) if b.open_time <= now]
        if "startTime" in params:
            start = int(params["startTime"])
            rows = [b for b in opened if wire.ms(b.open_time) >= start][:limit]
        else:
            rows = opened[-limit:]
        # The forming candle is served with its scripted values: the adapter
        # drops it by the closed-bar rule, so its numbers are never read.
        return _json([wire.kline_row(b) for b in rows])

    def _depth(self, params: dict[str, str]) -> httpx.Response:
        pair = self._pair(params.get("symbol"))
        if pair is None:
            return _binance_error(400, -1121, "Invalid symbol.")
        now = self.clock.now()
        if any(start <= now < end for start, end in self.book_outages):
            return httpx.Response(503, text="Service Unavailable")
        closed = [b for b in self.market.h4[pair] if b.close_time < now]
        if not closed:
            return _binance_error(400, -1121, "Invalid symbol.")
        self._update_id += 1
        levels = min(int(params.get("limit", 100)), DEPTH_LEVELS_MAX)
        return _json(wire.depth(closed[-1].close, levels=levels, update_id=self._update_id))

    # -- Frankfurter -----------------------------------------------------

    def _frankfurter(self, path: str, params: dict[str, str]) -> httpx.Response:
        if self.fx_down:
            return httpx.Response(503, text="Service Unavailable")
        base, quote = params.get("from"), params.get("to")
        if base != "EUR" or quote != "USD":
            return _json({"message": "not found"}, status=404)
        day = self.clock.now().date() if path == "/latest" else date.fromisoformat(path[1:])
        while day.weekday() >= 5:  # the ECB publishes on weekdays; weekends answer Friday
            day -= timedelta(days=1)
        return _json(wire.fx_rate("EUR", "USD", day, self.eur_usd))


def _json(body: object, status: int = 200) -> httpx.Response:
    return httpx.Response(
        status, content=json.dumps(body).encode(), headers={"content-type": "application/json"}
    )


def _binance_error(status: int, code: int, msg: str) -> httpx.Response:
    return _json({"code": code, "msg": msg}, status=status)
