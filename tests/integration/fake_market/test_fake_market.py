"""The fake market speaks the production contract and drives the real serve wiring.

Issue #688. Rick, 2026-10-05: the generated data may differ from production in
its values, because it is adjusted to force scenarios, but it must match
production by contract, in schema and in the way it arrives.

Invariants protected here:

- recorded production responses and generated responses pass the same
  contract checks, and a payload with a wrong field fails them;
- the real Binance klines adapter, reading the fake through its production
  URL, returns exactly the scripted bars, drops the forming candle and delivers
  each bar only after its close plus the settle delay;
- the real depth and Frankfurter adapters parse the fake's answers, and an
  outage reaches them as the error a real outage raises;
- ``hermes serve`` built from the repository's config, on a scripted
  breakout and crash, opens one simulated position and closes it on the
  chandelier stop, without one request leaving the fake.

The scenario suite (issue #689) builds on this module; these tests only prove
the fake itself is faithful.
"""

import asyncio
import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from hermes.adapters._shared.rate_limiter import RateLimitConfig, RateLimiter
from hermes.adapters.fx.frankfurter import FrankfurterAdapter
from hermes.adapters.fx.frankfurter import FrankfurterConfig as FrankfurterWireConfig
from hermes.adapters.market_data.binance_depth_snapshot import (
    DEPTH_LEVELS,
    BinanceDepthSnapshotAdapter,
)
from hermes.adapters.market_data.binance_live_klines import BinanceLiveKlinesAdapter
from hermes.domain.errors import InfraError
from hermes.domain.events.signal_emitted import SignalEmitted
from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.bar_schedule import delivery_key
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair
from hermes.infrastructure.config.models import MarketDataConfig
from tests.support.fake_market import schema, wire
from tests.support.fake_market.exchange import BINANCE_HOST, FakeExchange, SimClock
from tests.support.fake_market.harness import serve
from tests.support.fake_market.script import Market, MarketScript
from tests.support.serve_loop import BTC, ETH, PROJECT_ROOT, events

pytestmark = pytest.mark.integration

SOL = Pair(base=Currency.SOL, quote=Currency.USDT)
START = datetime(2024, 1, 1, tzinfo=UTC)
LIVE_FROM = START + timedelta(days=301)
FIXTURES = PROJECT_ROOT / "tests" / "fixtures"
FX_URL = "https://api.frankfurter.dev/v1"
SETTLE = timedelta(seconds=MarketDataConfig().settle_delay_seconds)


def _breakout_market() -> Market:
    """BTC climbs 100 to 150 over 302 days, breaks out to 156, holds, then crashes to 120.

    ETH and SOL stay flat at 100. With the pre-registered parameters the
    breakout bar satisfies all three entry rules (pre-registration §5.1) and
    the crash bar's low of 115 is far below any chandelier stop (§5.2).
    """
    script = MarketScript(
        start=START, prices={BTC: Decimal(100), ETH: Decimal(100), SOL: Decimal(100)}
    )
    script.ramp(BTC, days=302, to=Decimal(150))
    script.bar(BTC, close=Decimal(156), high=Decimal(157))
    script.hold(BTC, days=2)
    script.bar(BTC, close=Decimal(120), low=Decimal(115))
    script.hold(BTC, days=2)
    return script.build()


def _fixture(relative: str) -> object:
    return json.loads((FIXTURES / relative).read_text(encoding="utf-8"))


def _get(exchange: FakeExchange, url: str, params: dict[str, str] | None = None) -> object:
    with httpx.Client(transport=exchange.transport()) as client:
        response = client.get(url, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def _exchange(
    market: Market, at: datetime, book_outages: list[tuple[datetime, datetime]] | None = None
) -> FakeExchange:
    return FakeExchange(
        market=market, clock=SimClock(at, market.end), book_outages=book_outages or []
    )


# -- contract ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("relative", "check"),
    [
        (
            "binance_public/klines_btcusdt_4h_limit3.json",
            lambda p: schema.klines_problems(p, KlineInterval.H4),
        ),
        (
            "binance_public/klines_btcusdt_1d_head.json",
            lambda p: schema.klines_problems(p, KlineInterval.D1),
        ),
        (
            "binance_public/depth_btcusdt_limit20.json",
            lambda p: schema.depth_problems(p, levels=20),
        ),
        ("binance_public/time.json", schema.time_problems),
        ("frankfurter/eur_usd_2026-01-02.json", schema.fx_problems),
        ("frankfurter/eur_usd_2026-01-03_saturday.json", schema.fx_problems),
    ],
)
def test_recorded_production_responses_meet_the_contract(relative: str, check: object) -> None:
    """Every recorded production response passes the contract the fake is held to."""
    assert callable(check)
    assert check(_fixture(relative)) == []


def test_generated_responses_meet_the_same_contract() -> None:
    """Klines (both intervals), time, depth and FX from the fake pass the production checks."""
    market = _breakout_market()
    exchange = _exchange(market, LIVE_FROM)
    base = f"https://{BINANCE_HOST}/api/v3"
    problems = {
        "4h": schema.klines_problems(
            _get(
                exchange, f"{base}/klines", {"symbol": "BTCUSDT", "interval": "4h", "limit": "1000"}
            ),
            KlineInterval.H4,
        ),
        "1d": schema.klines_problems(
            _get(
                exchange, f"{base}/klines", {"symbol": "BTCUSDT", "interval": "1d", "limit": "1000"}
            ),
            KlineInterval.D1,
        ),
        "time": schema.time_problems(_get(exchange, f"{base}/time")),
        "depth": schema.depth_problems(
            _get(exchange, f"{base}/depth", {"symbol": "ETHUSDT", "limit": str(DEPTH_LEVELS)}),
            levels=DEPTH_LEVELS,
        ),
        "fx": schema.fx_problems(
            _get(exchange, f"{FX_URL}/2024-10-28", {"from": "EUR", "to": "USD"})
        ),
    }
    assert problems == {"4h": [], "1d": [], "time": [], "depth": [], "fx": []}


def _row(bar: Bar) -> list[object]:
    return wire.kline_row(bar)


@pytest.mark.parametrize(
    ("label", "mutate", "expected"),
    [
        (
            "close_time off by 1 ms",
            lambda r: r.__setitem__(6, r[6] + 1),
            "close_time is not open_time + 4h - 1 ms",
        ),
        (
            "price as a JSON number",
            lambda r: r.__setitem__(4, 1.5),
            "price c is not an 8-place decimal string",
        ),
        ("eleven fields", lambda r: r.pop(), "row has 11 fields, not 12"),
        (
            "high below close",
            lambda r: r.__setitem__(2, "1.00000000"),
            "high/low do not bracket open and close",
        ),
    ],
)
def test_the_contract_rejects_a_wrong_kline_field(
    label: str, mutate: object, expected: str
) -> None:
    """A row with one field changed from the Binance convention fails with that field named."""
    row = _row(_breakout_market().h4[BTC][100])
    assert callable(mutate)
    mutate(row)
    assert expected in schema.kline_row_problems(row, KlineInterval.H4), label


def test_the_contract_rejects_a_crossed_book_and_a_bad_fx_date() -> None:
    """A book whose best bid meets the best ask, and an FX date in another format, both fail."""
    book = wire.depth(Decimal(100), levels=2, update_id=1)
    book["bids"] = [["101.00000000", "1.00000000"], ["99.00000000", "1.00000000"]]
    fx = wire.fx_rate("EUR", "USD", date(2024, 10, 28), Decimal("1.1"))
    fx["date"] = "28/10/2024"
    assert (schema.depth_problems(book), schema.fx_problems(fx)) == (
        ["book is crossed: best bid >= best ask"],
        ["date is not YYYY-MM-DD"],
    )


# -- the real adapters read the fake -----------------------------------------


def _klines_adapter(exchange: FakeExchange) -> BinanceLiveKlinesAdapter:
    config = MarketDataConfig()
    return BinanceLiveKlinesAdapter(
        config=config,
        clock=exchange.clock,
        client=httpx.AsyncClient(transport=exchange.transport()),
        limiter=RateLimiter(
            RateLimitConfig(max_weight=600, window_seconds=60), clock=exchange.clock.monotonic
        ),
        sleep=exchange.clock.sleep,
    )


def test_warm_up_through_the_real_adapter_returns_exactly_the_scripted_closed_bars() -> None:
    """Warm-up mid-candle gives the last scripted closed bars; the forming one is dropped."""
    market = _breakout_market()
    at = LIVE_FROM + timedelta(hours=2)
    adapter = _klines_adapter(_exchange(market, at))

    async def warm() -> tuple[list[Bar], list[Bar]]:
        try:
            return (
                await adapter.warm_up(BTC, KlineInterval.H4, 300),
                await adapter.warm_up(BTC, KlineInterval.D1, 300),
            )
        finally:
            await adapter.aclose()

    h4, d1 = asyncio.run(warm())
    closed_h4 = [b for b in market.h4[BTC] if b.close_time < at]
    closed_d1 = [b for b in market.d1[BTC] if b.close_time < at]
    assert (h4, d1) == (closed_h4[-300:], closed_d1[-300:])


def test_the_real_adapter_delivers_each_bar_after_its_close_and_settle_delay() -> None:
    """Streaming two days yields every bar once, in delivery order, never before close + 5 s.

    Bars that closed in the last 5 s before boot are not settled yet, so warm-up
    leaves them to the stream.
    """
    market = _breakout_market()
    exchange = _exchange(market, LIVE_FROM)
    adapter = _klines_adapter(exchange)
    until = LIVE_FROM + timedelta(days=2)
    seen: list[tuple[Bar, datetime]] = []

    async def stream() -> None:
        await adapter.warm_up(BTC, KlineInterval.H4, 300)
        await adapter.warm_up(BTC, KlineInterval.D1, 300)
        async for bar in adapter.stream_closed_bars([BTC], [KlineInterval.H4, KlineInterval.D1]):
            seen.append((bar, exchange.clock.now()))
            if exchange.clock.now() >= until:
                break
        await adapter.aclose()

    asyncio.run(stream())
    bars = [b for b, _ in seen]
    expected = sorted(
        [
            b
            for b in market.h4[BTC] + market.d1[BTC]
            if b.close_time + SETTLE > LIVE_FROM and delivery_key(b) <= delivery_key(bars[-1])
        ],
        key=delivery_key,
    )
    early = [(b.close_time, at) for b, at in seen if at < b.close_time + SETTLE]
    assert (bars, early) == (expected, [])


def test_the_real_depth_adapter_reads_the_book_and_sees_an_outage_as_an_error() -> None:
    """The book's best levels sit 1 bp around the last closed close; an outage raises InfraError."""
    market = _breakout_market()
    at = LIVE_FROM + timedelta(minutes=1)
    exchange = _exchange(
        market, at, book_outages=[(at + timedelta(hours=1), at + timedelta(hours=2))]
    )
    adapter = BinanceDepthSnapshotAdapter(
        config=MarketDataConfig(),
        clock=exchange.clock,
        client=httpx.AsyncClient(transport=exchange.transport()),
        limiter=RateLimiter(
            RateLimitConfig(max_weight=600, window_seconds=60), clock=exchange.clock.monotonic
        ),
        sleep=exchange.clock.sleep,
    )
    last_close = next(b.close for b in reversed(market.h4[BTC]) if b.close_time < at)

    async def read() -> tuple[Decimal, Decimal, str]:
        book = await adapter.snapshot(BTC)
        exchange.clock.current = at + timedelta(hours=1, minutes=30)
        try:
            await adapter.snapshot(BTC)
        except InfraError as exc:
            error = type(exc).__name__
        else:
            error = "none"
        await adapter.aclose()
        return book.bids[0].price, book.asks[0].price, error

    bid, ask, error = asyncio.run(read())
    step = last_close / Decimal(10_000)
    assert (bid, ask, error != "none") == (
        (last_close - step).quantize(Decimal("0.00000001")),
        (last_close + step).quantize(Decimal("0.00000001")),
        True,
    )


def test_the_real_frankfurter_adapter_gets_fridays_rate_on_a_saturday() -> None:
    """A Saturday fill date resolves to Friday's rate, as the ECB publishes on weekdays."""
    exchange = _exchange(_breakout_market(), LIVE_FROM)
    adapter = FrankfurterAdapter(
        config=FrankfurterWireConfig(base_url=FX_URL, wire_codes=FrankfurterWireConfig.USDT_AS_USD),
        client=httpx.Client(base_url=FX_URL, transport=exchange.transport()),
        limiter=RateLimiter(config=RateLimitConfig(max_weight=60, window_seconds=60)),
    )
    rate = adapter.get_for_date(Currency.EUR, Currency.USDT, date(2024, 11, 2))
    adapter.close()
    assert (rate.date, rate.rate) == (date(2024, 11, 1), Decimal("1.1"))


# -- the production serve wiring on the fake ----------------------------------


def _signals(db: Path) -> list[tuple[str, str, str | None]]:
    rows = [json.loads(r["payload_json"]) for r in events(db, "SignalEmitted")]
    return [
        (f"{p['pair']['base']}/{p['pair']['quote']}", p["kind"], p["exit_reason"]) for p in rows
    ]


def _trades(db: Path) -> list[tuple[str, str]]:
    rows = [json.loads(r["payload_json"]) for r in events(db, "TradeExecuted")]
    return [(f"{p['pair']['base']}/{p['pair']['quote']}", p["side"]) for p in rows]


def test_serve_on_the_fake_market_opens_and_closes_one_position_without_leaving_the_fake(
    tmp_path: Path,
) -> None:
    """A scripted breakout then crash gives one BUY and one chandelier SELL, both notified.

    Built from the repository's config with the real klines, depth and
    Frankfurter adapters; every request the run makes is answered by the fake
    exchange at the production hosts.
    """
    db = tmp_path / "ledger.sqlite3"
    run = serve(_breakout_market(), db, live_from=LIVE_FROM)
    hosts = {host for host, _path, _params in run.exchange.requests}
    notified = [
        (str(e.pair), e.kind.value, e.exit_reason)
        for e in run.channel.seen
        if isinstance(e, SignalEmitted)
    ]
    assert {
        "exit_code": run.exit_code,
        "signals": _signals(db),
        "trades": _trades(db),
        "notified": notified,
        "hosts": hosts,
    } == {
        "exit_code": 0,
        "signals": [("BTC/USDT", "entry", None), ("BTC/USDT", "exit", "chandelier")],
        "trades": [("BTC/USDT", "BUY"), ("BTC/USDT", "SELL")],
        "notified": [("BTC/USDT", "entry", None), ("BTC/USDT", "exit", "chandelier")],
        "hosts": {BINANCE_HOST, "api.frankfurter.dev"},
    }
