"""Unit tests for the console's read-only Binance ticker price (#389).

Requests are served by respx from a recorded response
(``tests/fixtures/binance_public/ticker_price_btcusdt.json``, captured from
``data-api.binance.vision`` on 2026-10-03). No real network.
"""

from __future__ import annotations

import ast
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import anyio
import httpx
import pytest
import respx

from hermes.adapters.clock import SimulatedClock
from hermes.adapters.exchanges.rate_limiter import RateLimitConfig, RateLimiter
from hermes.adapters.market_data.binance_ticker import BinanceTickerPriceAdapter
from hermes.domain.errors import ExchangeError, NetworkError
from hermes.domain.value_objects.pair import Pair
from hermes.infrastructure.config.models import MarketDataConfig
from hermes.ports.price_lookup import PriceLookupPort, PriceQuote

_ROOT = Path(__file__).resolve().parents[4]
_FIXTURE = json.loads(
    (_ROOT / "tests/fixtures/binance_public/ticker_price_btcusdt.json").read_text(encoding="utf-8")
)
_URL = "https://data-api.binance.vision/api/v3/ticker/price"
_START = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
_BTC = Pair.from_str("BTC/USDT").unwrap()


def _adapter(clock: SimulatedClock) -> BinanceTickerPriceAdapter:
    return BinanceTickerPriceAdapter(
        config=MarketDataConfig(),
        clock=clock,
        client=httpx.AsyncClient(),
        limiter=RateLimiter(RateLimitConfig(max_weight=100, window_seconds=60)),
    )


def _quote(adapter: BinanceTickerPriceAdapter) -> PriceQuote:
    async def _run() -> PriceQuote:
        return await adapter.last_price(_BTC)

    return anyio.run(_run)


def test_satisfies_the_port() -> None:
    """The adapter is a structural PriceLookupPort."""
    assert isinstance(_adapter(SimulatedClock(start=_START)), PriceLookupPort)


@respx.mock
def test_parses_the_recorded_ticker() -> None:
    """The recorded body gives BTC/USDT at 84849.37, stamped with the clock's time."""
    route = respx.get(_URL, params={"symbol": "BTCUSDT"}).mock(
        return_value=httpx.Response(200, json=_FIXTURE)
    )
    quote = _quote(_adapter(SimulatedClock(start=_START)))
    assert quote == PriceQuote(pair=_BTC, price=Decimal("84849.37000000"), as_of=_START)
    assert route.call_count == 1


@respx.mock
def test_repeated_calls_within_five_seconds_hit_the_cache() -> None:
    """A console refreshing every second sends one request per 5-second window."""
    route = respx.get(_URL).mock(return_value=httpx.Response(200, json=_FIXTURE))
    clock = SimulatedClock(start=_START)
    adapter = _adapter(clock)
    _quote(adapter)
    for _ in range(4):
        clock.advance(timedelta(seconds=1))
        _quote(adapter)
    assert route.call_count == 1
    clock.advance(timedelta(seconds=1))
    _quote(adapter)
    assert route.call_count == 2


@respx.mock
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(400, json={"code": -1121, "msg": "Invalid symbol."}),
        httpx.Response(200, text="not json"),
        httpx.Response(200, json={"symbol": "ETHUSDT", "price": "1.0"}),
        httpx.Response(200, json={"symbol": "BTCUSDT", "price": 84849.37}),
        httpx.Response(200, json={"symbol": "BTCUSDT", "price": "0"}),
    ],
)
def test_bad_responses_raise_exchange_error(response: httpx.Response) -> None:
    """HTTP errors, non-JSON, wrong symbol, non-string or non-positive price all fail loudly."""
    respx.get(_URL).mock(return_value=response)
    with pytest.raises(ExchangeError, match="ticker"):
        _quote(_adapter(SimulatedClock(start=_START)))


@respx.mock
def test_transport_failure_raises_network_error() -> None:
    """A connection error is a NetworkError, so the console can show a dash."""
    respx.get(_URL).mock(side_effect=httpx.ConnectError("down"))
    with pytest.raises(NetworkError, match="BTCUSDT"):
        _quote(_adapter(SimulatedClock(start=_START)))


def test_client_with_api_key_is_refused() -> None:
    """The adapter is public-only: an API key header on the client is rejected."""
    client = httpx.AsyncClient(headers={"X-MBX-APIKEY": "not-a-real-key"})  # gitleaks:allow
    with pytest.raises(ValueError, match="public-only"):
        BinanceTickerPriceAdapter(
            config=MarketDataConfig(),
            clock=SimulatedClock(start=_START),
            client=client,
            limiter=RateLimiter(RateLimitConfig(max_weight=10, window_seconds=60)),
        )


def test_strategy_code_cannot_import_the_price_lookup() -> None:
    """No strategy module imports the live price: signals come from closed bars only (§5.1)."""
    banned = {"hermes.ports.price_lookup", "hermes.adapters.market_data.binance_ticker"}
    strategy_files = [
        *sorted((_ROOT / "src/hermes/domain/strategy").rglob("*.py")),
        _ROOT / "src/hermes/ports/strategy.py",
    ]
    offenders: list[str] = []
    for path in strategy_files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            offenders += [f"{path.name}: {n}" for n in names if n in banned]
    assert len(strategy_files) > 5
    assert offenders == []
