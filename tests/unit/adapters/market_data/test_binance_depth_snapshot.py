"""Tests for the Binance public depth snapshot adapter.

Invariants protected here:

- the recorded ``/api/v3/depth`` response (``tests/fixtures/binance_public/
  depth_btcusdt_limit20.json``, recorded 2026-10-04 with a keyless GET to
  ``data-api.binance.vision``) parses into a valid book, stamped with the
  injected clock at receipt;
- the adapter is public-only: a client with an API key is refused, and the
  only request it sends is a GET to ``/api/v3/depth`` on the configured host;
- a book younger than the cache window is reused, an older one is refetched;
- every failure surfaces as ``NetworkError`` or ``ExchangeError`` so the
  caller can fall back, never as a half-built book.

``respx`` replaces the network; everything above it is the real adapter.
The live shape is checked by ``tests/contract/test_binance_public_depth.py``.

Reference: openspec/changes/353-fill-kernel-in-serve/change.md D-1, R-6.
"""

from __future__ import annotations

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
from hermes.adapters.market_data.binance_depth_snapshot import BinanceDepthSnapshotAdapter
from hermes.domain.errors import ExchangeError, NetworkError
from hermes.domain.market_data.orderbook import Orderbook
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair
from hermes.infrastructure.config.models import MarketDataConfig

_FIXTURE = Path(__file__).parents[3] / "fixtures" / "binance_public" / "depth_btcusdt_limit20.json"
_DEPTH_URL = "https://data-api.binance.vision/api/v3/depth"
_BTC = Pair(base=Currency.BTC, quote=Currency.USDT)
_START = datetime(2026, 10, 4, 4, 0, 5, tzinfo=UTC)


def _recorded() -> object:
    """Return the recorded depth body."""
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


def _adapter(
    clock: SimulatedClock,
    *,
    client: httpx.AsyncClient | None = None,
    cache_seconds: float = 1.0,
    max_weight: int = 600,
) -> BinanceDepthSnapshotAdapter:
    """Build an adapter over the default config with a deterministic bucket."""
    return BinanceDepthSnapshotAdapter(
        config=MarketDataConfig(),
        clock=clock,
        client=client if client is not None else httpx.AsyncClient(),
        limiter=RateLimiter.for_testing(
            RateLimitConfig(max_weight=max_weight, window_seconds=60),
            clock=lambda: 0.0,
            sleep=lambda _s: None,
        ),
        cache_seconds=cache_seconds,
    )


def _snapshot(adapter: BinanceDepthSnapshotAdapter) -> Orderbook:
    """Run one snapshot call to completion."""

    async def _go() -> Orderbook:
        return await adapter.snapshot(_BTC)

    return anyio.run(_go)


@respx.mock
def test_recorded_response_parses_into_the_book_it_describes() -> None:
    """The touch of the recorded body becomes the touch of the book."""
    respx.get(_DEPTH_URL).mock(return_value=httpx.Response(200, json=_recorded()))

    book = _snapshot(_adapter(SimulatedClock(start=_START)))

    assert (book.bids[0].price, book.asks[0].price, len(book.bids), len(book.asks)) == (
        Decimal("84833.11000000"),
        Decimal("84833.12000000"),
        20,
        20,
    )


@respx.mock
def test_book_is_stamped_with_the_injected_clock() -> None:
    """RO-04 measures age on the loop's clock, so the stamp is that clock's."""
    respx.get(_DEPTH_URL).mock(return_value=httpx.Response(200, json=_recorded()))

    assert _snapshot(_adapter(SimulatedClock(start=_START))).snapshot_time == _START


@respx.mock
def test_request_is_a_keyless_get_for_the_pair_symbol() -> None:
    """One GET to the allow-listed host and path, symbol and limit only."""
    route = respx.get(_DEPTH_URL).mock(return_value=httpx.Response(200, json=_recorded()))

    _snapshot(_adapter(SimulatedClock(start=_START)))

    request = route.calls.last.request
    assert (dict(request.url.params), "x-mbx-apikey" in request.headers) == (
        {"symbol": "BTCUSDT", "limit": "20"},
        False,
    )


def test_a_client_with_an_api_key_is_refused() -> None:
    """The adapter can never send a key, so it can never reach an account."""
    keyed = httpx.AsyncClient(headers={"X-MBX-APIKEY": "fake"})  # gitleaks:allow
    with pytest.raises(ValueError, match="public-only"):
        _adapter(SimulatedClock(start=_START), client=keyed)


@respx.mock
def test_a_book_younger_than_the_cache_window_is_reused() -> None:
    """Two reads half a second apart send one request."""
    route = respx.get(_DEPTH_URL).mock(return_value=httpx.Response(200, json=_recorded()))
    clock = SimulatedClock(start=_START)
    adapter = _adapter(clock)

    _snapshot(adapter)
    clock.advance(timedelta(milliseconds=500))
    _snapshot(adapter)

    assert route.call_count == 1


@respx.mock
def test_a_book_older_than_the_cache_window_is_refetched() -> None:
    """After the window the next read is a fresh request with a fresh stamp."""
    route = respx.get(_DEPTH_URL).mock(return_value=httpx.Response(200, json=_recorded()))
    clock = SimulatedClock(start=_START)
    adapter = _adapter(clock)

    _snapshot(adapter)
    clock.advance(timedelta(seconds=1))
    book = _snapshot(adapter)

    assert (route.call_count, book.snapshot_time) == (2, _START + timedelta(seconds=1))


@respx.mock
def test_an_http_error_raises_exchange_error() -> None:
    """A 429 (or any 4xx/5xx) is reported, not retried, so the caller falls back."""
    respx.get(_DEPTH_URL).mock(return_value=httpx.Response(429, text="too many"))

    with pytest.raises(ExchangeError, match="HTTP 429"):
        _snapshot(_adapter(SimulatedClock(start=_START)))


@respx.mock
def test_a_transport_failure_raises_network_error() -> None:
    """A dropped connection is a NetworkError."""
    respx.get(_DEPTH_URL).mock(side_effect=httpx.ConnectError("down"))

    with pytest.raises(NetworkError, match="transport failure"):
        _snapshot(_adapter(SimulatedClock(start=_START)))


@pytest.mark.parametrize(
    "body",
    [
        {"lastUpdateId": 1, "bids": [], "asks": []},
        {"lastUpdateId": 1, "bids": [["x", "1"]], "asks": []},
        {"lastUpdateId": 1, "bids": [["2", "1"]], "asks": [["1", "1"]]},
        {"lastUpdateId": 1, "bids": "nope", "asks": []},
        [1, 2],
    ],
    ids=["empty", "non-numeric", "crossed", "not-a-list", "not-an-object"],
)
@respx.mock
def test_a_malformed_body_raises_exchange_error(body: object) -> None:
    """An empty, non-numeric, crossed or mis-shaped book is refused whole."""
    respx.get(_DEPTH_URL).mock(return_value=httpx.Response(200, json=body))

    with pytest.raises(ExchangeError):
        _snapshot(_adapter(SimulatedClock(start=_START)))


@respx.mock
def test_an_empty_bucket_waits_instead_of_sending() -> None:
    """With the bucket drained, the adapter sleeps before the request."""
    respx.get(_DEPTH_URL).mock(return_value=httpx.Response(200, json=_recorded()))
    slept: list[float] = []
    ticks = iter([0.0, 0.0, 0.0, 60.0, 60.0])

    async def _fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    adapter = BinanceDepthSnapshotAdapter(
        config=MarketDataConfig(),
        clock=SimulatedClock(start=_START),
        client=httpx.AsyncClient(),
        limiter=RateLimiter.for_testing(
            RateLimitConfig(max_weight=5, window_seconds=60),
            clock=lambda: next(ticks),
            sleep=lambda _s: None,
        ),
        cache_seconds=0,
        sleep=_fake_sleep,
    )
    _snapshot(adapter)
    _snapshot(adapter)

    assert slept == [60.0]
