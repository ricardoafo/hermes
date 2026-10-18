"""The fake market served on a socket answers what the in-process fake answers.

Issue #971, part 1. Invariants protected here:

- over loopback HTTP the service returns, status and bytes, what
  :class:`FakeExchange` returns through the mock transport, so every generated
  payload still meets the production contract;
- the real klines, depth and Frankfurter adapters, with only their base URL
  pointed at the service, read the scripted market exactly;
- the clock moves only forward, by setting, advancing or ticking;
- every fault the control API takes reaches the client as the vendor's failure
  would, and a reset clears them all;
- a market script loaded as JSON is the market the builder makes;
- the standalone entry point serves a script from another process.

The market-data config refuses a loopback host on purpose; widening that for
dev and qa is issue #970's work, so the adapters here get a copied config.
"""

import asyncio
import json
import subprocess
import sys
import time
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from hermes.adapters.exchanges.rate_limiter import RateLimitConfig, RateLimiter
from hermes.adapters.fx.frankfurter import FrankfurterAdapter
from hermes.adapters.fx.frankfurter import FrankfurterConfig as FrankfurterWireConfig
from hermes.adapters.fx.rate_limiter import RateLimitConfig as FxRateLimitConfig
from hermes.adapters.fx.rate_limiter import RateLimiter as FxRateLimiter
from hermes.adapters.market_data.binance_depth_snapshot import (
    DEPTH_LEVELS,
    BinanceDepthSnapshotAdapter,
)
from hermes.adapters.market_data.binance_live_klines import BinanceLiveKlinesAdapter
from hermes.domain.errors import ExchangeError
from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.value_objects.currency import Currency
from hermes.infrastructure.config.models import MarketDataConfig
from tests.support.fake_market import schema, wire
from tests.support.fake_market.exchange import BINANCE_HOST, FakeExchange, SimClock
from tests.support.fake_market.script import Market, MarketScript
from tests.support.fake_market.service import (
    ControlError,
    FakeMarketService,
    Fault,
    Upstream,
    market_from_spec,
)
from tests.support.serve_loop import BTC, ETH, PROJECT_ROOT

pytestmark = pytest.mark.integration

START = datetime(2024, 1, 1, tzinfo=UTC)
LIVE_FROM = START + timedelta(days=301)
FX_URL = "https://api.frankfurter.dev/v1"
SPEC: dict[str, object] = {
    "start": START.isoformat(),
    "now": LIVE_FROM.isoformat(),
    "pairs": {
        "BTC/USDT": {
            "price": "100",
            "steps": [
                {"ramp": {"days": 302, "to": "150"}},
                {"bar": {"close": "156", "high": "157"}},
                {"hold": {"days": 2}},
                {"bar": {"close": "120", "low": "115"}},
                {"hold": {"days": 2}},
            ],
        },
        "ETH/USDT": {"price": "100", "steps": []},
    },
}


def _built_market() -> Market:
    """Return the market :data:`SPEC` describes, made with the builder calls it mirrors."""
    script = MarketScript(start=START, prices={BTC: Decimal(100), ETH: Decimal(100)})
    script.ramp(BTC, days=302, to=Decimal(150))
    script.bar(BTC, close=Decimal(156), high=Decimal(157))
    script.hold(BTC, days=2)
    script.bar(BTC, close=Decimal(120), low=Decimal(115))
    script.hold(BTC, days=2)
    return script.build()


def _exchange(at: datetime = LIVE_FROM) -> FakeExchange:
    market = _built_market()
    return FakeExchange(market=market, clock=SimClock(at, market.end))


@pytest.fixture
def service() -> Iterator[FakeMarketService]:
    """Serve the breakout market at ``LIVE_FROM`` with a short slow-fault hold."""
    with FakeMarketService(_exchange(), slow_hold_s=0.5) as running:
        yield running


def _get(url: str, params: dict[str, str] | None = None) -> httpx.Response:
    return httpx.get(url, params=params, timeout=5)


def _control(service: FakeMarketService, path: str, body: object) -> httpx.Response:
    return httpx.post(f"{service.url}/_fake{path}", json=body, timeout=5)


_PROBES = [
    ("/api/v3/klines", {"symbol": "BTCUSDT", "interval": "4h", "limit": "1000"}),
    ("/api/v3/klines", {"symbol": "BTCUSDT", "interval": "1d", "startTime": "1704067200000"}),
    ("/api/v3/time", {}),
    ("/api/v3/depth", {"symbol": "ETHUSDT", "limit": str(DEPTH_LEVELS)}),
    ("/api/v3/klines", {"symbol": "DOGEUSDT", "interval": "4h"}),
    ("/api/v3/ticker", {}),
]


def test_the_socket_answers_byte_for_byte_what_the_in_process_fake_answers(
    service: FakeMarketService,
) -> None:
    """Same market, same instant: status, body and weight header match the mock transport."""
    in_process = _exchange()
    sent = [(f"https://{BINANCE_HOST}{p}", f"{service.binance_base}{p}", q) for p, q in _PROBES]
    sent += [
        (f"{FX_URL}{p}", f"{service.frankfurter_base}{p}", {"from": "EUR", "to": "USD"})
        for p in ("/2024-11-02", "/latest")
    ]
    with httpx.Client(transport=in_process.transport()) as client:
        expected = [client.get(url, params=q) for url, _, q in sent]
    served = [_get(url, q) for _, url, q in sent]

    def seen(r: httpx.Response) -> tuple[int, bytes, str | None]:
        return r.status_code, r.content, r.headers.get("x-mbx-used-weight-1m")

    assert [seen(r) for r in served] == [seen(r) for r in expected]


def test_payloads_over_the_socket_meet_the_production_contract(
    service: FakeMarketService,
) -> None:
    """Klines, time, depth and FX read off the socket pass the production checks."""
    base = f"{service.binance_base}/api/v3"
    h4 = _get(f"{base}/klines", {"symbol": "BTCUSDT", "interval": "4h", "limit": "1000"})
    d1 = _get(f"{base}/klines", {"symbol": "BTCUSDT", "interval": "1d", "limit": "1000"})
    depth = _get(f"{base}/depth", {"symbol": "BTCUSDT", "limit": str(DEPTH_LEVELS)})
    fx = _get(f"{service.frankfurter_base}/2024-10-28", {"from": "EUR", "to": "USD"})
    assert {
        "4h": schema.klines_problems(h4.json(), KlineInterval.H4),
        "1d": schema.klines_problems(d1.json(), KlineInterval.D1),
        "time": schema.time_problems(_get(f"{base}/time").json()),
        "depth": schema.depth_problems(depth.json(), levels=DEPTH_LEVELS),
        "fx": schema.fx_problems(fx.json()),
    } == {"4h": [], "1d": [], "time": [], "depth": [], "fx": []}


def _market_config(service: FakeMarketService) -> MarketDataConfig:
    return MarketDataConfig().model_copy(update={"base_url": service.binance_base})


def _limiter(clock: SimClock) -> RateLimiter:
    return RateLimiter(RateLimitConfig(max_weight=600, window_seconds=60), clock=clock.monotonic)


def _klines_adapter(service: FakeMarketService) -> BinanceLiveKlinesAdapter:
    clock = service.exchange.clock
    return BinanceLiveKlinesAdapter(
        config=_market_config(service),
        clock=clock,
        client=httpx.AsyncClient(timeout=5),
        limiter=_limiter(clock),
        sleep=clock.sleep,
    )


def test_the_real_klines_adapter_warms_up_over_the_socket_to_the_scripted_bars(
    service: FakeMarketService,
) -> None:
    """Mid-candle warm-up over HTTP gives the last closed scripted bars, forming one dropped."""
    at = LIVE_FROM + timedelta(hours=2)
    service.set_clock(at)
    adapter = _klines_adapter(service)

    async def warm() -> tuple[list[Bar], list[Bar]]:
        try:
            return (
                await adapter.warm_up(BTC, KlineInterval.H4, 300),
                await adapter.warm_up(BTC, KlineInterval.D1, 300),
            )
        finally:
            await adapter.aclose()

    h4, d1 = asyncio.run(warm())
    market = service.exchange.market
    assert (h4, d1) == (
        [b for b in market.h4[BTC] if b.close_time < at][-300:],
        [b for b in market.d1[BTC] if b.close_time < at][-300:],
    )


def test_the_real_depth_and_fx_adapters_read_the_socket(service: FakeMarketService) -> None:
    """The book sits 1 bp around the last close; a Saturday rate resolves to Friday's."""
    clock = service.exchange.clock
    depth = BinanceDepthSnapshotAdapter(
        config=_market_config(service),
        clock=clock,
        client=httpx.AsyncClient(timeout=5),
        limiter=_limiter(clock),
        sleep=clock.sleep,
    )
    fx = FrankfurterAdapter(
        config=FrankfurterWireConfig(base_url=FX_URL, wire_codes=FrankfurterWireConfig.USDT_AS_USD),
        client=httpx.Client(base_url=service.frankfurter_base, timeout=5),
        limiter=FxRateLimiter(config=FxRateLimitConfig(max_weight=60, window_seconds=60)),
    )

    async def read() -> tuple[Decimal, Decimal]:
        try:
            book = await depth.snapshot(BTC)
        finally:
            await depth.aclose()
        return book.bids[0].price, book.asks[0].price

    bid, ask = asyncio.run(read())
    rate = fx.get_for_date(Currency.EUR, Currency.USDT, date(2024, 11, 2))
    fx.close()
    last = next(
        b.close for b in reversed(service.exchange.market.h4[BTC]) if b.close_time < LIVE_FROM
    )
    step = last / Decimal(10_000)
    assert (bid, ask, rate.date, rate.rate) == (
        (last - step).quantize(Decimal("0.00000001")),
        (last + step).quantize(Decimal("0.00000001")),
        date(2024, 11, 1),
        Decimal("1.1"),
    )


def test_a_rate_limit_reply_reaches_the_adapter_with_its_retry_after(
    service: FakeMarketService,
) -> None:
    """A 429 with Retry-After fails one warm-up as the adapter reports it; the next one lands."""
    service.set_clock(LIVE_FROM + timedelta(hours=2))
    service.fail_next(Upstream.BINANCE, Fault.RATE_LIMITED)
    adapter = _klines_adapter(service)

    async def twice() -> tuple[str, int]:
        try:
            try:
                await adapter.warm_up(BTC, KlineInterval.H4, 3)
            except ExchangeError as exc:
                first = str(exc)
            else:
                first = "no error"
            return first, len(await adapter.warm_up(BTC, KlineInterval.H4, 3))
        finally:
            await adapter.aclose()

    assert asyncio.run(twice()) == (
        "Exchange error: HTTP 429 from klines during warm-up (retry after 7.0 s)",
        3,
    )


def test_a_frankfurter_rate_limit_is_a_429_with_retry_after(service: FakeMarketService) -> None:
    """Rate-limit faults are per vendor: Frankfurter's answers 429 while Binance still answers."""
    service.fail_next(Upstream.FRANKFURTER, Fault.RATE_LIMITED)
    binance = _get(f"{service.binance_base}/api/v3/time")
    fx = _get(f"{service.frankfurter_base}/latest", {"from": "EUR", "to": "USD"})
    assert (binance.status_code, fx.status_code, fx.headers["retry-after"], fx.json()) == (
        200,
        429,
        "7",
        {"message": "Too Many Requests"},
    )


def test_a_slow_answer_outlasts_a_short_client_timeout(service: FakeMarketService) -> None:
    """A slow fault holds one answer past the client's timeout; the next answer is prompt."""
    service.fail_next(Upstream.BINANCE, Fault.SLOW)
    url = f"{service.binance_base}/api/v3/time"
    with pytest.raises(httpx.ReadTimeout):
        httpx.get(url, timeout=0.1)
    assert _get(url).json() == wire.server_time(LIVE_FROM)


# -- clock --------------------------------------------------------------------


def test_the_clock_sets_and_advances_forward(service: FakeMarketService) -> None:
    """Set and advance move the instant server time reports."""
    first = _control(service, "/clock", {"now": "2024-11-01T00:00:00+00:00"}).json()
    second = _control(service, "/clock", {"advance_seconds": 90}).json()
    served = _get(f"{service.binance_base}/api/v3/time").json()
    assert (first["now"], second["now"], served) == (
        "2024-11-01T00:00:00+00:00",
        "2024-11-01T00:01:30+00:00",
        wire.server_time(datetime(2024, 11, 1, 0, 1, 30, tzinfo=UTC)),
    )


@pytest.mark.parametrize(
    ("body", "error"),
    [
        (
            {"now": "2024-01-01T00:00:00+00:00"},
            "clock only moves forward: 2024-01-01T00:00:00+00:00 is before "
            "2024-10-28T00:00:00+00:00",
        ),
        ({"now": "2024-11-01T00:00:00"}, "now must carry a UTC offset: '2024-11-01T00:00:00'"),
        ({"advance_seconds": -1}, "advance must be zero or more seconds, got -1.0"),
        ({"speed": -2}, "speed must be zero or more, got -2.0"),
        ({"speed": "fast"}, "speed must be a number, got 'fast'"),
    ],
)
def test_the_clock_refuses_to_run_backwards_or_take_bad_values(
    service: FakeMarketService, body: dict[str, object], error: str
) -> None:
    """A backward, naive or negative clock request is a 400 that names it; the clock stays."""
    response = _control(service, "/clock", body)
    assert (response.status_code, response.json(), service.now()) == (
        400,
        {"error": error},
        LIVE_FROM,
    )


def test_a_ticking_clock_runs_at_its_speed_times_wall_time(service: FakeMarketService) -> None:
    """At 3600x, 50 ms of wall time moves the market at least 3 minutes; speed 0 stops it."""
    service.set_speed(3600)
    time.sleep(0.05)
    service.set_speed(0)
    stopped = service.now()
    time.sleep(0.02)
    assert (stopped - LIVE_FROM >= timedelta(minutes=3), service.now()) == (True, stopped)


def test_direct_control_errors_are_control_errors(service: FakeMarketService) -> None:
    """The Python API refuses what the HTTP API refuses, with the same message."""
    with pytest.raises(ControlError, match=r"^clock time must be timezone-aware"):
        service.set_clock(datetime(2025, 1, 1))


# -- faults ---------------------------------------------------------------------


def _klines(service: FakeMarketService, interval: str = "4h") -> httpx.Response:
    return _get(
        f"{service.binance_base}/api/v3/klines",
        {"symbol": "BTCUSDT", "interval": interval, "limit": "3"},
    )


def test_a_klines_outage_answers_503_only_inside_its_window(service: FakeMarketService) -> None:
    """Inside the window klines answer 503; once the clock leaves it they answer again."""
    _control(
        service,
        "/faults",
        {
            "kind": "klines_outage",
            "start": LIVE_FROM.isoformat(),
            "end": (LIVE_FROM + timedelta(hours=1)).isoformat(),
        },
    )
    inside = _klines(service).status_code
    service.advance(3600)
    assert (inside, _klines(service).status_code) == (503, 200)


def test_a_late_bar_is_left_out_until_it_is_published(service: FakeMarketService) -> None:
    """The last closed 4h bar is missing until ``shown_from``, then served."""
    last_open = LIVE_FROM - timedelta(hours=4)
    _control(
        service,
        "/faults",
        {
            "kind": "late_bar",
            "pair": "BTC/USDT",
            "interval": "4h",
            "open_time": last_open.isoformat(),
            "shown_from": (LIVE_FROM + timedelta(minutes=5)).isoformat(),
        },
    )
    before = [row[0] for row in _klines(service).json()]
    service.advance(300)
    after = [row[0] for row in _klines(service).json()]
    assert (wire.ms(last_open) in before, wire.ms(last_open) in after) == (False, True)


def test_duplicate_and_broken_bars_are_served_as_scripted(service: FakeMarketService) -> None:
    """A duplicated bar appears twice; a broken bar's close reads ``NaN`` in its window."""
    last_open = LIVE_FROM - timedelta(hours=4)
    bar = {"pair": "BTC/USDT", "interval": "4h", "open_time": last_open.isoformat()}
    _control(service, "/faults", {"kind": "duplicate_bar", **bar})
    _control(
        service,
        "/faults",
        {
            "kind": "corrupt_bar",
            **bar,
            "broken_from": LIVE_FROM.isoformat(),
            "clean_from": (LIVE_FROM + timedelta(hours=1)).isoformat(),
        },
    )
    rows = [(row[0], row[4]) for row in _klines(service).json() if row[0] == wire.ms(last_open)]
    assert rows == [(wire.ms(last_open), "NaN")] * 2


def test_fx_down_and_a_thin_book_reach_the_client(service: FakeMarketService) -> None:
    """``fx_down`` makes Frankfurter answer 503; ``thin_book`` widens the depth levels."""
    _control(service, "/faults", {"kind": "fx_down"})
    _control(service, "/faults", {"kind": "thin_book", "step_bps": "25"})
    fx = _get(f"{service.frankfurter_base}/latest", {"from": "EUR", "to": "USD"})
    book = _get(f"{service.binance_base}/api/v3/depth", {"symbol": "BTCUSDT", "limit": "5"}).json()
    last = next(
        b.close for b in reversed(service.exchange.market.h4[BTC]) if b.close_time < LIVE_FROM
    )
    assert (fx.status_code, book["bids"][0][0]) == (
        503,
        wire.decimal_text(last - last * Decimal(25) / Decimal(10_000)),
    )


@pytest.mark.parametrize(
    ("body", "error"),
    [
        ({"kind": "meteor"}, "unknown fault kind 'meteor'"),
        (
            {"kind": "slow", "upstream": "kraken"},
            "upstream 'kraken' is not one of binance, frankfurter",
        ),
        ({"kind": "rate_limited", "times": 0}, "times must be a whole number of at least 1, got 0"),
        ({"kind": "book_outage", "start": "soon"}, "start is not an ISO 8601 time: 'soon'"),
        ({"kind": "thin_book", "step_bps": "-1"}, "step_bps must be a positive decimal, got '-1'"),
        (
            {"kind": "late_bar", "pair": "BTC/XXX", "interval": "4h"},
            "unknown pair 'BTC/XXX'",
        ),
        (
            {"kind": "duplicate_bar", "pair": "BTC/USDT", "interval": "3h"},
            "interval '3h' is not one of 1m, 5m, 1h, 4h, 1d",
        ),
    ],
)
def test_a_malformed_fault_is_a_400_naming_the_field(
    service: FakeMarketService, body: dict[str, object], error: str
) -> None:
    """Each malformed fault request is refused with a message naming what is wrong."""
    response = _control(service, "/faults", body)
    assert (response.status_code, response.json()) == (400, {"error": error})


def test_reset_clears_every_fault_and_the_request_log(service: FakeMarketService) -> None:
    """After a reset, outages and queued faults are gone and the log starts empty."""
    window = {"start": LIVE_FROM.isoformat(), "end": (LIVE_FROM + timedelta(days=1)).isoformat()}
    for kind in ("klines_outage", "book_outage", "fx_outage"):
        _control(service, "/faults", {"kind": kind, **window})
    _control(service, "/faults", {"kind": "fx_down"})
    _control(service, "/faults", {"kind": "rate_limited", "times": 3})
    _klines(service)
    reset = _control(service, "/reset", {}).status_code
    statuses = [
        _klines(service).status_code,
        _get(f"{service.binance_base}/api/v3/depth", {"symbol": "BTCUSDT"}).status_code,
        _get(f"{service.frankfurter_base}/latest", {"from": "EUR", "to": "USD"}).status_code,
    ]
    log = httpx.get(f"{service.url}/_fake/requests", timeout=5).json()
    assert (reset, statuses, [entry["path"] for entry in log]) == (
        200,
        [200, 200, 200],
        ["/api/v3/klines", "/api/v3/depth", "/v1/latest"],
    )


def test_the_request_log_holds_the_upstream_host_path_and_params(
    service: FakeMarketService,
) -> None:
    """Each served request is logged as the vendor would have seen it."""
    _klines(service, "1d")
    _get(f"{service.frankfurter_base}/2024-11-01", {"from": "EUR", "to": "USD"})
    log = httpx.get(f"{service.url}/_fake/requests", timeout=5).json()
    assert log == [
        {
            "host": BINANCE_HOST,
            "path": "/api/v3/klines",
            "params": {"symbol": "BTCUSDT", "interval": "1d", "limit": "3"},
        },
        {
            "host": "api.frankfurter.dev",
            "path": "/v1/2024-11-01",
            "params": {"from": "EUR", "to": "USD"},
        },
    ]


def test_an_unknown_path_is_a_404(service: FakeMarketService) -> None:
    """Only the two vendor prefixes and the control API are served."""
    responses = [
        _get(f"{service.url}/kraken/0/public/Ticker"),
        httpx.post(f"{service.url}/_fake/clockwork", json={}, timeout=5),
    ]
    assert [(r.status_code, r.json()) for r in responses] == [
        (404, {"error": "no route for /kraken/0/public/Ticker"}),
        (404, {"error": "unknown control path POST /clockwork"}),
    ]


# -- scripts ----------------------------------------------------------------------


def test_a_json_script_builds_the_market_its_builder_calls_build() -> None:
    """The JSON format and the builder give identical bars for both intervals."""
    built = _built_market()
    loaded = market_from_spec(SPEC)
    assert (loaded.h4, loaded.d1) == (built.h4, built.d1)


def test_loading_a_script_replaces_the_market_and_restarts_the_clock(
    service: FakeMarketService,
) -> None:
    """After a load, klines come from the new market at its ``now``; old faults are gone.

    At 00:00 on day three the third daily candle has just opened, so it is
    served as the forming row, as Binance does.
    """
    _control(service, "/faults", {"kind": "rate_limited"})
    spec = {
        "start": "2023-03-06T00:00:00+00:00",
        "now": "2023-03-08T00:00:00+00:00",
        "pairs": {"ETH/USDT": {"price": "2000", "steps": [{"ramp": {"days": 3, "to": "2300"}}]}},
    }
    loaded = _control(service, "/script", spec)
    rows = _get(
        f"{service.binance_base}/api/v3/klines",
        {"symbol": "ETHUSDT", "interval": "1d", "limit": "10"},
    ).json()
    market = market_from_spec(spec)
    assert (loaded.json(), rows, service.now()) == (
        {"start": "2023-03-06T00:00:00+00:00", "end": "2023-03-09T00:00:00+00:00"},
        [wire.kline_row(b) for b in market.d1[ETH]],
        datetime(2023, 3, 8, tzinfo=UTC),
    )


@pytest.mark.parametrize(
    ("spec", "error"),
    [
        (
            {"start": "2024-01-01T00:00:00+00:00"},
            "pairs must be a non-empty object of pair to {price, steps}",
        ),
        (
            {
                "start": "2024-01-01T04:00:00+00:00",
                "pairs": {"BTC/USDT": {"price": "1", "steps": [{"hold": {"days": 1}}]}},
            },
            "start must be 00:00 UTC, got 2024-01-01T04:00:00+00:00",
        ),
        (
            {"start": "2024-01-01T00:00:00+00:00", "pairs": {"BTC/USDT": {"price": 1.5}}},
            "BTC/USDT price must be a decimal string, got 1.5",
        ),
        (
            {
                "start": "2024-01-01T00:00:00+00:00",
                "pairs": {"BTC/USDT": {"price": "1", "steps": [{"jump": {}}]}},
            },
            "unknown step 'jump'",
        ),
        (
            {
                "start": "2024-01-01T00:00:00+00:00",
                "pairs": {"BTC/USDT": {"price": "1", "steps": []}},
            },
            "the script has no steps",
        ),
        (
            {
                "start": "2024-01-01T00:00:00+00:00",
                "pairs": {"BTC/USDT": {"price": "1", "steps": [{"bar": {}}]}},
            },
            "close must be a decimal string, got None",
        ),
    ],
)
def test_a_malformed_script_is_a_400_and_the_market_stays(
    service: FakeMarketService, spec: dict[str, object], error: str
) -> None:
    """A bad script is refused with a message naming the problem; the old market keeps serving."""
    before = service.exchange.market
    response = _control(service, "/script", spec)
    assert (response.status_code, response.json(), service.exchange.market) == (
        400,
        {"error": error},
        before,
    )


# -- standalone ----------------------------------------------------------------------


def test_the_standalone_entry_point_serves_a_script_from_another_process(tmp_path: Path) -> None:
    """``python -m tests.support.fake_market.service`` prints its URLs and answers on them."""
    script = tmp_path / "market.json"
    script.write_text(json.dumps(SPEC), encoding="utf-8")
    process = subprocess.Popen(  # noqa: S603 - fixed interpreter and module, path built here
        [sys.executable, "-m", "tests.support.fake_market.service", "--script", str(script)],
        cwd=PROJECT_ROOT,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout is not None
        urls = dict(process.stdout.readline().strip().split(": ", 1) for _ in range(3))
        served = _get(f"{urls['binance']}/api/v3/time").json()
        clock = httpx.get(f"{urls['control']}/clock", timeout=5).json()
    finally:
        process.terminate()
        process.wait(timeout=10)
        if process.stdout is not None:
            process.stdout.close()
    assert (sorted(urls), served, clock) == (
        ["binance", "control", "frankfurter"],
        wire.server_time(LIVE_FROM),
        {"now": LIVE_FROM.isoformat(), "speed": 0.0},
    )
