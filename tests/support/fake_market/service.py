"""The fake market as a loopback HTTP service another process can reach.

:class:`~tests.support.fake_market.exchange.FakeExchange` answers inside the
test process through an ``httpx`` mock transport. This module puts the same
handler behind a real socket, so an installed release, a scenario in another
process, or an adapter with only its base URL changed reads the same payloads,
built by the same code and held to the same schema (issue #971, part 1):

- ``/binance/...`` answers as ``https://data-api.binance.vision/...``.
- ``/binance-spot/...`` answers as ``https://testnet.binance.vision/...``:
  the same public endpoints plus the signed spot order endpoints of
  :mod:`tests.support.fake_market.spot` (part 3).
- ``/frankfurter/...`` answers as the configured Frankfurter base URL.

A control API under ``/_fake/`` (or the Python methods of the same names)
loads a market script, drives the scripted clock (set, advance, or tick at a
multiple of wall time) and injects faults: the exchange's own outages, late,
duplicate and broken bars, a thin book, and service-level rate-limit replies
and slow answers, and an order submission whose answer is lost.

A script can also replay recorded Train-window bars instead of scripted
steps (part 2, :mod:`tests.support.fake_market.replay`); a window reaching the
Validation start is refused.

Run standalone with ``python -m tests.support.fake_market.service --port N
--script path.json``. It binds loopback by default; the order endpoints take
only the fake key in :data:`tests.support.fake_market.spot.FAKE_API_KEY`.
"""

import argparse
import contextlib
import json
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Final
from urllib.parse import urlsplit

import httpx
from returns.result import Failure

from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.value_objects.pair import Pair
from tests.support.fake_market.exchange import BINANCE_HOST, FakeExchange, SimClock
from tests.support.fake_market.replay import ReplayError, market_from_recording
from tests.support.fake_market.script import Market, MarketScript
from tests.support.fake_market.spot import SPOT_HOST, Lost

BINANCE_PREFIX: Final = "/binance"
BINANCE_SPOT_PREFIX: Final = "/binance-spot"
FRANKFURTER_PREFIX: Final = "/frankfurter"
CONTROL_PREFIX: Final = "/_fake"


class Upstream(StrEnum):
    """The vendor a request is addressed to."""

    BINANCE = "binance"
    FRANKFURTER = "frankfurter"


class Fault(StrEnum):
    """A service-level failure served instead of the next answers."""

    RATE_LIMITED = "rate_limited"
    SLOW = "slow"


class ControlError(ValueError):
    """A control request the service refuses; answered as HTTP 400."""


@dataclass(frozen=True)
class _Reply:
    status: int
    body: bytes
    headers: tuple[tuple[str, str], ...] = ()

    @classmethod
    def json(cls, status: int, payload: object, *headers: tuple[str, str]) -> _Reply:
        return cls(
            status, json.dumps(payload).encode(), (("Content-Type", "application/json"), *headers)
        )


_RATE_LIMITED: Final[dict[Upstream, Callable[[int], _Reply]]] = {
    Upstream.BINANCE: lambda after: _Reply.json(
        429,
        {
            "code": -1003,
            "msg": "Too many requests; current limit is 6000 request weight per 1 MINUTE.",
        },
        ("Retry-After", str(after)),
    ),
    Upstream.FRANKFURTER: lambda after: _Reply.json(
        429, {"message": "Too Many Requests"}, ("Retry-After", str(after))
    ),
}


class FakeMarketService:
    """Serves one :class:`FakeExchange` over loopback HTTP.

    The exchange and its clock stay shared objects: a test in the same process
    can still read ``exchange.requests`` or let an adapter's injected sleep move
    ``exchange.clock`` while the service answers on the socket.
    """

    def __init__(
        self,
        exchange: FakeExchange,
        host: str = "127.0.0.1",
        port: int = 0,
        *,
        slow_hold_s: float = 2.0,
        retry_after_s: int = 7,
    ) -> None:
        """Bind ``host:port`` (``0`` picks a free port) without serving yet.

        Args:
            exchange: The fake answering every vendor request.
            host: Interface to bind; loopback by default.
            port: Port to bind; ``0`` lets the OS choose.
            slow_hold_s: How long a ``slow`` fault holds its answer.
            retry_after_s: The ``Retry-After`` a ``rate_limited`` fault sends.

        """
        self.exchange = exchange
        self.slow_hold_s = slow_hold_s
        self.retry_after_s = retry_after_s
        self._lock = threading.RLock()
        self._faults: dict[Upstream, list[Fault]] = {u: [] for u in Upstream}
        self._speed = 0.0
        self._anchor: tuple[datetime, float] = (exchange.clock.current, time.monotonic())
        self._host = host
        self._httpd = ThreadingHTTPServer((host, port), self._handler_class())
        self._httpd.daemon_threads = True
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        """Return the service root, ``http://host:port``."""
        return f"http://{self._host}:{self._httpd.server_port}"

    @property
    def binance_base(self) -> str:
        """Return the base URL that stands in for ``https://data-api.binance.vision``."""
        return f"{self.url}{BINANCE_PREFIX}"

    @property
    def binance_spot_base(self) -> str:
        """Return the base URL that stands in for ``https://testnet.binance.vision``."""
        return f"{self.url}{BINANCE_SPOT_PREFIX}"

    @property
    def frankfurter_base(self) -> str:
        """Return the base URL that stands in for the configured Frankfurter root."""
        return f"{self.url}{FRANKFURTER_PREFIX}"

    def start(self) -> FakeMarketService:
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

    def __enter__(self) -> FakeMarketService:
        """Start serving."""
        return self.start()

    def __exit__(self, *exc: object) -> None:
        """Stop serving."""
        self.stop()

    # -- clock -------------------------------------------------------------

    def now(self) -> datetime:
        """Return the scripted instant requests are answered at."""
        with self._lock:
            self._tick()
            return self.exchange.clock.current

    def set_clock(self, now: datetime) -> None:
        """Move the clock to ``now``.

        Raises:
            ControlError: ``now`` is naive or earlier than the current instant;
                a market never runs backwards, so a scenario cannot unsee a bar.

        """
        if now.tzinfo is None:
            raise ControlError(f"clock time must be timezone-aware, got {now.isoformat()}")
        with self._lock:
            self._tick()
            current = self.exchange.clock.current
            if now < current:
                raise ControlError(
                    f"clock only moves forward: {now.isoformat()} is before {current.isoformat()}"
                )
            self.exchange.clock.current = now
            self._anchor = (now, time.monotonic())

    def advance(self, seconds: float) -> datetime:
        """Move the clock forward by ``seconds`` and return the new instant.

        Raises:
            ControlError: ``seconds`` is negative.

        """
        if seconds < 0:
            raise ControlError(f"advance must be zero or more seconds, got {seconds}")
        with self._lock:
            self.set_clock(self.now() + timedelta(seconds=seconds))
            return self.exchange.clock.current

    def set_speed(self, speed: float) -> None:
        """Tick the clock at ``speed`` times wall time; ``0`` stops it.

        Raises:
            ControlError: ``speed`` is negative.

        """
        if speed < 0:
            raise ControlError(f"speed must be zero or more, got {speed}")
        with self._lock:
            self._tick()
            self._speed = speed
            self._anchor = (self.exchange.clock.current, time.monotonic())

    def _tick(self) -> None:
        if self._speed:
            start, wall = self._anchor
            ticked = start + timedelta(seconds=(time.monotonic() - wall) * self._speed)
            self.exchange.clock.current = max(self.exchange.clock.current, ticked)

    # -- script and faults ---------------------------------------------------

    def load_script(self, spec: Mapping[str, object]) -> Market:
        """Replace the market with the one ``spec`` describes; see :func:`market_from_spec`.

        The clock restarts at the spec's ``now``, else at the market's first
        bar, and stops ticking; faults and the request log are cleared.

        Returns:
            The built market.

        """
        market = market_from_spec(spec)
        now = _instant(spec["now"], "now") if "now" in spec else market.start
        with self._lock:
            self.exchange.load(market)
            self.exchange.clock.current = now
            self.exchange.clock.stop_at = market.end
            self._speed = 0.0
            self._anchor = (now, time.monotonic())
            self.reset()
        return market

    def fail_next(self, upstream: Upstream, fault: Fault, times: int = 1) -> None:
        """Serve ``fault`` instead of the next ``times`` answers from ``upstream``."""
        with self._lock:
            self._faults[upstream].extend([fault] * times)

    def add_fault(self, spec: Mapping[str, object]) -> None:
        """Add one fault described as the control API takes it.

        Raises:
            ControlError: The kind is unknown or a field is missing or malformed.

        """
        kind = spec.get("kind")
        with self._lock:
            match kind:
                case "rate_limited" | "slow":
                    self.fail_next(
                        _choice(Upstream, spec.get("upstream", "binance"), "upstream"),
                        Fault(kind),
                        _count(spec.get("times", 1)),
                    )
                case "klines_outage" | "book_outage" | "fx_outage":
                    window = (
                        _instant(spec.get("start"), "start"),
                        _instant(spec.get("end"), "end"),
                    )
                    getattr(self.exchange, kind + "s").append(window)
                case "fx_down":
                    self.exchange.fx_down = spec.get("down", True) is True
                case "thin_book":
                    self.exchange.book_step_bps = _decimal(spec.get("step_bps"), "step_bps")
                case "late_bar" | "duplicate_bar" | "corrupt_bar":
                    self._add_bar_fault(kind, spec)
                case "order_lost":
                    self.exchange.spot.lose_next_order(
                        _choice(Lost, spec.get("how", "placed"), "how")
                    )
                case _:
                    raise ControlError(f"unknown fault kind {kind!r}")

    def _add_bar_fault(self, kind: str, spec: Mapping[str, object]) -> None:
        bar = (
            _pair(spec.get("pair")),
            _choice(KlineInterval, spec.get("interval"), "interval"),
            _instant(spec.get("open_time"), "open_time"),
        )
        if kind == "late_bar":
            self.exchange.late_bars.append((*bar, _instant(spec.get("shown_from"), "shown_from")))
        elif kind == "duplicate_bar":
            self.exchange.duplicate_bars.append(bar)
        else:
            self.exchange.corrupt_bars.append(
                (
                    *bar,
                    _instant(spec.get("broken_from"), "broken_from"),
                    _instant(spec.get("clean_from"), "clean_from"),
                )
            )

    def reset(self) -> None:
        """Clear every fault and the request log; the market and clock stay."""
        with self._lock:
            for queue in self._faults.values():
                queue.clear()
            ex = self.exchange
            for windows in (ex.book_outages, ex.klines_outages, ex.fx_outages):
                windows.clear()
            ex.late_bars.clear()
            ex.corrupt_bars.clear()
            ex.duplicate_bars.clear()
            ex.requests.clear()
            ex.spot.lost.clear()
            ex.fx_down = False
            ex.book_step_bps = Decimal(1)

    # -- serving -------------------------------------------------------------

    def _handle(self, method: str, target: str, raw: bytes, api_key: str | None = None) -> _Reply:
        split = urlsplit(target)
        path = split.path
        try:
            if path.startswith(CONTROL_PREFIX + "/"):
                return self._control(method, path.removeprefix(CONTROL_PREFIX), raw)
            # Both Binance hosts are one upstream: their faults are Binance's.
            for prefix, upstream, host in (
                (BINANCE_SPOT_PREFIX, Upstream.BINANCE, SPOT_HOST),
                (BINANCE_PREFIX, Upstream.BINANCE, BINANCE_HOST),
                (FRANKFURTER_PREFIX, Upstream.FRANKFURTER, self.exchange.fx_host),
            ):
                if path.startswith(prefix + "/"):
                    return self._vendor(
                        upstream, method, f"{host}{path.removeprefix(prefix)}", split.query, api_key
                    )
        except ControlError as exc:
            return _Reply.json(400, {"error": str(exc)})
        return _Reply.json(404, {"error": f"no route for {path}"})

    def _vendor(
        self, upstream: Upstream, method: str, target: str, query: str, api_key: str | None
    ) -> _Reply:
        """Answer ``target`` (host and path) as the vendor would, unless a fault comes first."""
        with self._lock:
            self._tick()
            fault = self._faults[upstream].pop(0) if self._faults[upstream] else None
            if fault is Fault.RATE_LIMITED:
                return _RATE_LIMITED[upstream](self.retry_after_s)
            url = f"https://{target}"
            if upstream is Upstream.FRANKFURTER:
                host, _, rest = target.partition("/")
                url = f"https://{host}{self.exchange.fx_prefix}/{rest}"
            # The query goes on byte for byte: the spot endpoints check a signature over it.
            sent = {"X-MBX-APIKEY": api_key} if api_key is not None else {}
            response = self.exchange.handle(
                httpx.Request(method, f"{url}?{query}" if query else url, headers=sent)
            )
        if fault is Fault.SLOW:
            time.sleep(self.slow_hold_s)
        headers = tuple(
            (k, v) for k, v in response.headers.items() if k.lower() != "content-length"
        )
        return _Reply(response.status_code, response.content, headers)

    def _control(self, method: str, path: str, raw: bytes) -> _Reply:
        body = _body(raw) if method == "POST" else {}
        payload: object
        match method, path:
            case "GET", "/clock":
                payload = self._clock_state()
            case "POST", "/clock":
                if "now" in body:
                    self.set_clock(_instant(body["now"], "now"))
                if "advance_seconds" in body:
                    self.advance(_number(body["advance_seconds"], "advance_seconds"))
                if "speed" in body:
                    self.set_speed(_number(body["speed"], "speed"))
                payload = self._clock_state()
            case "POST", "/script":
                market = self.load_script(body)
                payload = {"start": market.start.isoformat(), "end": market.end.isoformat()}
            case "POST", "/faults":
                self.add_fault(body)
                payload = {"ok": True}
            case "GET", "/requests":
                with self._lock:
                    payload = [
                        {"host": h, "path": p, "params": q} for h, p, q in self.exchange.requests
                    ]
            case "POST", "/reset":
                self.reset()
                payload = {"ok": True}
            case _:
                return _Reply.json(404, {"error": f"unknown control path {method} {path}"})
        return _Reply.json(200, payload)

    def _clock_state(self) -> dict[str, object]:
        return {"now": self.now().isoformat(), "speed": self._speed}

    def _handler_class(self) -> type[BaseHTTPRequestHandler]:
        service = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def _serve(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                reply = service._handle(
                    self.command, self.path, raw, self.headers.get("X-MBX-APIKEY")
                )
                self.send_response(reply.status)
                for name, value in reply.headers:
                    self.send_header(name, value)
                self.send_header("Content-Length", str(len(reply.body)))
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(reply.body)

            do_GET = do_POST = do_HEAD = do_DELETE = _serve  # noqa: N815

            def log_message(self, format: str, *args: object) -> None:
                """Keep test output quiet."""

        return Handler


# -- the script format -------------------------------------------------------


def market_from_spec(spec: Mapping[str, object]) -> Market:
    """Build a market from its JSON description.

    The format mirrors :class:`MarketScript`'s builder calls::

        {"start": "2024-01-01T00:00:00+00:00",
         "now": "2024-10-28T00:00:00+00:00",
         "pairs": {"BTC/USDT": {"price": "100", "steps": [
             {"ramp": {"days": 302, "to": "150"}},
             {"bar": {"close": "156", "high": "157"}},
             {"hold": {"days": 2}}]}}}

    Or, to replay recorded 4h bars of the Train window (issue #971, part 2)::

        {"replay": {"dir": "/srv/hermes-data", "pairs": ["BTC/USDT"],
                    "start": "2023-01-01T00:00:00+00:00",
                    "end": "2023-07-01T00:00:00+00:00"},
         "now": "2023-03-01T00:00:00+00:00"}

    Args:
        spec: The decoded description; ``now`` is optional and read by the
            service, not here.

    Returns:
        The built market.

    Raises:
        ControlError: A field is missing, malformed or names an unknown step.

    """
    if "replay" in spec:
        return _replayed(spec["replay"])
    pairs = spec.get("pairs")
    if not isinstance(pairs, dict) or not pairs:
        raise ControlError("pairs must be a non-empty object of pair to {price, steps}")
    prices: dict[Pair, Decimal] = {}
    steps: dict[Pair, list[object]] = {}
    for text, entry in pairs.items():
        if not isinstance(entry, dict):
            raise ControlError(f"pair {text!r} must map to an object with price and steps")
        pair = _pair(text)
        prices[pair] = _decimal(entry.get("price"), f"{text} price")
        listed = entry.get("steps", [])
        if not isinstance(listed, list):
            raise ControlError(f"{text} steps must be a list")
        steps[pair] = listed
    try:
        script = MarketScript(start=_instant(spec.get("start"), "start"), prices=prices)
    except ValueError as exc:
        raise ControlError(str(exc)) from exc
    for pair, listed in steps.items():
        for step in listed:
            _apply_step(script, pair, step)
    if not any(script.steps.values()):
        raise ControlError("the script has no steps")
    return script.build()


def _replayed(replay: object) -> Market:
    if not isinstance(replay, dict):
        raise ControlError("replay must be an object of dir, pairs, start and end")
    data_dir = replay.get("dir")
    if not isinstance(data_dir, str) or not data_dir:
        raise ControlError(f"replay dir must be a path, got {data_dir!r}")
    pairs = replay.get("pairs")
    if not isinstance(pairs, list) or not pairs:
        raise ControlError("replay pairs must be a non-empty list")
    try:
        return market_from_recording(
            Path(data_dir),
            [_pair(text) for text in pairs],
            _instant(replay.get("start"), "start"),
            _instant(replay.get("end"), "end"),
        )
    except ReplayError as exc:
        raise ControlError(str(exc)) from exc


def _apply_step(script: MarketScript, pair: Pair, step: object) -> None:
    if not isinstance(step, dict) or len(step) != 1:
        raise ControlError(f"each step must be one of ramp, hold or bar, got {step!r}")
    ((name, args),) = step.items()
    if not isinstance(args, dict):
        raise ControlError(f"step {name!r} needs an object of arguments")
    match name:
        case "ramp":
            script.ramp(
                pair, days=_number(args.get("days"), "days"), to=_decimal(args.get("to"), "to")
            )
        case "hold":
            script.hold(pair, days=_number(args.get("days"), "days"))
        case "bar":
            optional = {k: _decimal(args[k], k) for k in ("high", "low", "volume") if k in args}
            script.bar(pair, close=_decimal(args.get("close"), "close"), **optional)
        case _:
            raise ControlError(f"unknown step {name!r}")


def _body(raw: bytes) -> dict[str, object]:
    try:
        body = json.loads(raw or b"{}")
    except ValueError as exc:
        raise ControlError("body is not JSON") from exc
    if not isinstance(body, dict):
        raise ControlError("body must be a JSON object")
    return body


def _instant(value: object, name: str) -> datetime:
    try:
        at = datetime.fromisoformat(str(value))
    except ValueError as exc:
        raise ControlError(f"{name} is not an ISO 8601 time: {value!r}") from exc
    if at.tzinfo is None:
        raise ControlError(f"{name} must carry a UTC offset: {value!r}")
    return at


def _decimal(value: object, name: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ControlError(f"{name} must be a decimal string, got {value!r}")
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise ControlError(f"{name} is not a decimal: {value!r}") from exc
    if not number.is_finite() or number <= 0:
        raise ControlError(f"{name} must be a positive decimal, got {value!r}")
    return number


def _number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ControlError(f"{name} must be a number, got {value!r}")
    return float(value)


def _count(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ControlError(f"times must be a whole number of at least 1, got {value!r}")
    return value


def _choice[E: StrEnum](enum: type[E], value: object, name: str) -> E:
    try:
        return enum(str(value))
    except ValueError as exc:
        allowed = ", ".join(member.value for member in enum)
        raise ControlError(f"{name} {value!r} is not one of {allowed}") from exc


def _pair(value: object) -> Pair:
    parsed = Pair.from_str(str(value))
    if isinstance(parsed, Failure):
        raise ControlError(f"unknown pair {value!r}")
    return parsed.unwrap()


def main(argv: list[str] | None = None) -> None:
    """Serve a scripted market until interrupted, printing the base URLs."""
    parser = argparse.ArgumentParser(prog="fake_market")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--script", type=Path, required=True, help="market script JSON")
    parser.add_argument("--speed", type=float, default=0.0, help="clock speed vs wall time")
    args = parser.parse_args(argv)
    spec = json.loads(args.script.read_text(encoding="utf-8"))
    market = market_from_spec(spec)
    exchange = FakeExchange(market=market, clock=SimClock(market.start, market.end))
    with FakeMarketService(exchange, args.host, args.port) as service:
        service.load_script(spec)
        service.set_speed(args.speed)
        print(f"binance: {service.binance_base}", flush=True)
        print(f"frankfurter: {service.frankfurter_base}", flush=True)
        print(f"control: {service.url}{CONTROL_PREFIX}", flush=True)
        with contextlib.suppress(KeyboardInterrupt):
            threading.Event().wait()


if __name__ == "__main__":
    main()
