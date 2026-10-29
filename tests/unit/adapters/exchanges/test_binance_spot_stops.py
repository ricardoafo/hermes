"""Unit tests for the Binance spot adapter's exchange-held stop reads (#1080).

Rules under test:

- only stop-loss sells are resting stops; a limit buy, or a stop-loss buy
  someone placed by hand, is never read as the position's protection;
- a resting stop's quantity is what is left to sell, its price the trigger;
- the fate of a stop that left the book: ``FILLED`` sold, any other end or an
  order Binance does not know sold nothing, ``NEW`` and ``PARTIALLY_FILLED``
  still rest;
- a reply the adapter cannot read fails loudly, never as "no stop".

Every reply is served by respx from the shapes in
``tests/fixtures/binance_spot/``; no test reaches the network. The fake
market's spot account answers the same endpoints in
``tests/integration/scenarios/test_exchange_stops.py``.
"""

import json
from collections.abc import Iterator
from decimal import Decimal
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import respx

from hermes.adapters._shared.rate_limiter import RateLimitConfig, RateLimiter
from hermes.adapters.exchanges.binance_spot import (
    TESTNET_BASE_URL,
    BinanceSpotAdapter,
    BinanceSpotConfig,
)
from hermes.domain.errors import ExchangeError
from hermes.domain.trading.exchange_stop import RestingStop, StopFate
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair
from tests.support.paths import REPO_ROOT

_OPEN_ORDERS_URL = f"{TESTNET_BASE_URL}/api/v3/openOrders"
_ORDER_URL = f"{TESTNET_BASE_URL}/api/v3/order"
_FIXTURES = REPO_ROOT / "tests" / "fixtures" / "binance_spot"
_BTC = Pair(base=Currency.BTC, quote=Currency.USDT)
_NOW_MS = 1_790_000_000_000
_KEY = "test-key-not-real"  # gitleaks:allow
_SIGNING_MATERIAL = "test-secret-not-real"  # gitleaks:allow


@pytest.fixture
def adapter() -> Iterator[BinanceSpotAdapter]:
    """Return an adapter on the testnet host with a fixed clock and no real waits."""
    config = BinanceSpotConfig(api_key=_KEY, api_secret=_SIGNING_MATERIAL)
    client = httpx.Client(base_url=config.base_url)
    limiter = RateLimiter(config=RateLimitConfig(max_weight=1000, window_seconds=60))
    yield BinanceSpotAdapter(
        config=config,
        client=client,
        limiter=limiter,
        clock_ms=lambda: _NOW_MS,
        sleep=lambda _: None,
    )
    client.close()


def _open_orders() -> list[dict[str, Any]]:
    loaded: list[dict[str, Any]] = json.loads(
        (_FIXTURES / "open_orders.json").read_text(encoding="utf-8")
    )
    return loaded


@respx.mock
def test_only_stop_loss_sells_are_resting_stops(adapter: BinanceSpotAdapter) -> None:
    """The limit buy and the stop-loss buy are left out; both sells are read exactly."""
    respx.get(url__startswith=_OPEN_ORDERS_URL).mock(
        return_value=httpx.Response(200, json=_open_orders())
    )
    assert adapter.resting_stop_losses(pair=_BTC) == (
        RestingStop("BTCUSDT:4021", _BTC, Decimal("0.50000000"), Decimal("140.12345678")),
        RestingStop("BTCUSDT:4024", _BTC, Decimal("0.18000000"), Decimal("130.00000000")),
    )


@respx.mock
def test_the_listing_asks_for_the_pairs_symbol_only(adapter: BinanceSpotAdapter) -> None:
    """One symbol per call: another pair's stops are never read as this one's."""
    route = respx.get(url__startswith=_OPEN_ORDERS_URL).mock(
        return_value=httpx.Response(200, json=[])
    )
    adapter.resting_stop_losses(pair=_BTC)
    assert parse_qs(urlsplit(str(route.calls.last.request.url)).query)["symbol"] == ["BTCUSDT"]


@respx.mock
def test_a_stop_without_its_trigger_price_is_an_exchange_error(
    adapter: BinanceSpotAdapter,
) -> None:
    """A row the adapter cannot read fails the listing, never drops out of it."""
    broken = _open_orders()[0]
    del broken["stopPrice"]
    respx.get(url__startswith=_OPEN_ORDERS_URL).mock(
        return_value=httpx.Response(200, json=[broken])
    )
    with pytest.raises(ExchangeError, match="malformed open order"):
        adapter.resting_stop_losses(pair=_BTC)


@respx.mock
def test_a_listing_that_is_not_a_list_is_an_exchange_error(adapter: BinanceSpotAdapter) -> None:
    """An object where a list belongs is unreadable, not an empty book."""
    respx.get(url__startswith=_OPEN_ORDERS_URL).mock(
        return_value=httpx.Response(200, json={"orders": []})
    )
    with pytest.raises(ExchangeError, match="expected a JSON list"):
        adapter.resting_stop_losses(pair=_BTC)


@pytest.mark.parametrize(
    ("status", "fate"),
    [
        ("NEW", StopFate.RESTING),
        ("PARTIALLY_FILLED", StopFate.RESTING),
        ("FILLED", StopFate.FILLED),
        ("CANCELED", StopFate.GONE),
        ("EXPIRED", StopFate.GONE),
        ("REJECTED", StopFate.GONE),
    ],
)
@respx.mock
def test_a_stops_status_maps_to_its_fate(
    adapter: BinanceSpotAdapter, status: str, fate: StopFate
) -> None:
    """Only ``FILLED`` sold; the working statuses still rest; every other end sold nothing."""
    respx.get(url__startswith=_ORDER_URL).mock(
        return_value=httpx.Response(200, json={"orderId": 4021, "status": status})
    )
    assert adapter.stop_loss_fate(order_id="BTCUSDT:4021") is fate


@respx.mock
def test_an_order_binance_does_not_know_is_gone(adapter: BinanceSpotAdapter) -> None:
    """-2013: nothing by that id rests or sold."""
    respx.get(url__startswith=_ORDER_URL).mock(
        return_value=httpx.Response(400, json={"code": -2013, "msg": "Order does not exist."})
    )
    assert adapter.stop_loss_fate(order_id="BTCUSDT:4021") is StopFate.GONE


@respx.mock
def test_the_fate_asks_for_the_stops_symbol_and_id(adapter: BinanceSpotAdapter) -> None:
    """The query names the order it was given, split back into symbol and id."""
    route = respx.get(url__startswith=_ORDER_URL).mock(
        return_value=httpx.Response(200, json={"orderId": 4021, "status": "NEW"})
    )
    adapter.stop_loss_fate(order_id="BTCUSDT:4021")
    query = parse_qs(urlsplit(str(route.calls.last.request.url)).query)
    assert (query["symbol"], query["orderId"]) == (["BTCUSDT"], ["4021"])


@respx.mock
def test_another_refusal_of_the_fate_is_an_exchange_error(adapter: BinanceSpotAdapter) -> None:
    """A refusal other than "no such order" is not read as gone: nothing is known."""
    respx.get(url__startswith=_ORDER_URL).mock(
        return_value=httpx.Response(400, json={"code": -1021, "msg": "Timestamp outside window."})
    )
    with pytest.raises(ExchangeError, match="-1021"):
        adapter.stop_loss_fate(order_id="BTCUSDT:4021")


@respx.mock
def test_a_fate_reply_without_a_status_is_an_exchange_error(adapter: BinanceSpotAdapter) -> None:
    """No status, no fate: never guessed as gone."""
    respx.get(url__startswith=_ORDER_URL).mock(
        return_value=httpx.Response(200, json={"orderId": 4021})
    )
    with pytest.raises(ExchangeError, match="has no status"):
        adapter.stop_loss_fate(order_id="BTCUSDT:4021")


def test_a_fate_for_an_id_without_its_symbol_is_refused(adapter: BinanceSpotAdapter) -> None:
    """An id that is not ``SYMBOL:ORDERID`` is refused before any request."""
    with pytest.raises(ValueError, match="SYMBOL:ORDERID"):
        adapter.stop_loss_fate(order_id="4021")
