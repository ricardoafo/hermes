"""Unit tests for the Binance spot trading adapter (#148).

Every request is answered by respx with replies shaped like the Binance spot
REST documentation; no test reaches the network. The real-testnet round trip
lives in ``tests/contract/test_binance_spot_testnet.py``.
"""

from collections.abc import Iterator
from decimal import Decimal
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import respx

from hermes.adapters._shared.rate_limiter import RateLimitConfig, RateLimiter
from hermes.adapters.exchanges.binance_spot import (
    TESTNET_BASE_URL,
    BinanceSpotAdapter,
    BinanceSpotConfig,
    OrderNotPlacedError,
    OrderStatusUnknownError,
    sign,
)
from hermes.domain.errors import ExchangeError, NetworkError
from hermes.domain.trading.fill import Fill, Rejection
from hermes.domain.trading.order import Order, OrderSide
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.quantity import Quantity
from hermes.domain.value_objects.strategy_id import StrategyId
from hermes.ports.exchange import ExchangePort, StopLossPort
from tests.support.mainnet_permit import issued_permit

_ORDER_URL = f"{TESTNET_BASE_URL}/api/v3/order"
_BTC = Pair(base=Currency.BTC, quote=Currency.USDT)
_NOW_MS = 1_790_000_000_000
_KEY = "test-key-not-real"  # gitleaks:allow
_SIGNING_MATERIAL = "test-secret-not-real"  # gitleaks:allow
_SLEPT: list[float] = []
_FOUND_FILLED: dict[str, object] = {
    "symbol": "BTCUSDT",
    "orderId": 28,
    "status": "FILLED",
    "executedQty": "0.01",
    "cummulativeQuoteQty": "650.00",
    "updateTime": _NOW_MS,
}
_NO_SUCH = httpx.Response(400, json={"code": -2013, "msg": "Order does not exist."})


@pytest.fixture
def adapter() -> Iterator[BinanceSpotAdapter]:
    """Return an adapter on the testnet host with a fixed clock, a roomy limiter and no real waits.

    Its waits are recorded in ``_SLEPT``, cleared for each test.
    """
    _SLEPT.clear()
    config = BinanceSpotConfig(api_key=_KEY, api_secret=_SIGNING_MATERIAL)
    client = httpx.Client(base_url=config.base_url)
    limiter = RateLimiter(config=RateLimitConfig(max_weight=1000, window_seconds=60))
    yield BinanceSpotAdapter(
        config=config, client=client, limiter=limiter, clock_ms=lambda: _NOW_MS, sleep=_SLEPT.append
    )
    client.close()


def _order(side: OrderSide = OrderSide.BUY, limit: str | None = None) -> Order:
    return Order(
        side=side,
        pair=_BTC,
        qty=Quantity(amount=Decimal("0.01"), asset=Currency.BTC),
        strategy_id=StrategyId("swing-donchian-4h"),
        limit_price=None if limit is None else Decimal(limit),
    )


def _filled(executed: str = "0.01", quote: str = "650.00") -> dict[str, object]:
    return {
        "symbol": "BTCUSDT",
        "orderId": 28,
        "transactTime": _NOW_MS,
        "executedQty": executed,
        "cummulativeQuoteQty": quote,
        "status": "FILLED",
        "fills": [
            {"price": "65000.00", "qty": "0.006", "commission": "0.39", "commissionAsset": "USDT"},
            {"price": "65000.00", "qty": "0.004", "commission": "0.26", "commissionAsset": "USDT"},
        ],
    }


# ---------------------------------------------------------------------------
# Signing and safety
# ---------------------------------------------------------------------------


def test_signature_matches_the_binance_documentation_vector() -> None:
    """HMAC-SHA256 over the query string reproduces Binance's published example."""
    doc_key_material = (
        "NhqPtmdSJYdKjVHjA7PZj4Mge3R5YNiP1e3UZjInClVN65XAbvqqM6A7H5fATj0j"  # gitleaks:allow
    )
    query = (
        "symbol=LTCBTC&side=BUY&type=LIMIT&timeInForce=GTC&quantity=1&price=0.1"
        "&recvWindow=5000&timestamp=1499827319559"
    )
    assert (
        sign(query, doc_key_material)
        == "c8db56825ae71d6d79447849e617115f4a920fa2acdcab2b053c4b2838bd6b71"
    )


# SEV-1: real capital stays behind the live-approval gate (AGENTS.md rule 7, ADR-014)
def test_mainnet_host_is_refused_without_permission() -> None:
    """api.binance.com is refused without a MainnetPermit."""
    with pytest.raises(ValueError, match="not the Binance testnet"):
        BinanceSpotConfig(
            api_key=_KEY, api_secret=_SIGNING_MATERIAL, base_url="https://api.binance.com"
        )


def test_mainnet_host_is_accepted_with_permission() -> None:
    """With an issued MainnetPermit the composition root may build a mainnet config."""
    config = BinanceSpotConfig(
        api_key=_KEY,
        api_secret=_SIGNING_MATERIAL,
        base_url="https://api.binance.com",
        mainnet_permit=issued_permit(),
    )
    assert config.base_url == "https://api.binance.com"


# SEV-1: real capital stays behind the live-approval gate (AGENTS.md rule 7, ADR-014)
@pytest.mark.parametrize(
    "base_url",
    ["https://api.binance.co", "https://evil.example", "https://api.binance.com.evil.example"],
)
def test_mainnet_permit_still_refuses_an_undocumented_host(base_url: str) -> None:
    """Even with a permit, only documented Binance spot hosts get the key and orders (#781)."""
    with pytest.raises(ValueError, match="not a documented Binance spot host"):
        BinanceSpotConfig(
            api_key=_KEY,
            api_secret=_SIGNING_MATERIAL,
            base_url=base_url,
            mainnet_permit=issued_permit(),
        )


# SEV-1: real capital stays behind the live-approval gate (AGENTS.md rule 7, ADR-014)
def test_a_truthy_override_is_not_a_permit() -> None:
    """A bare True override no longer opens mainnet, as allow_mainnet could (#781)."""
    with pytest.raises(ValueError, match="needs a MainnetPermit"):
        BinanceSpotConfig.from_env(
            {"HERMES_BINANCE__API_KEY": _KEY, "HERMES_BINANCE__API_SECRET": _SIGNING_MATERIAL},
            base_url="https://api.binance.com",
            mainnet_permit=True,
        )


def test_secret_is_kept_out_of_repr() -> None:
    """The API secret never appears in a repr or a log line built from the config."""
    assert _SIGNING_MATERIAL not in repr(
        BinanceSpotConfig(api_key=_KEY, api_secret=_SIGNING_MATERIAL)
    )


def test_missing_credentials_are_refused() -> None:
    """from_env without credentials raises instead of sending unsigned requests."""
    with pytest.raises(ValueError, match="required"):
        BinanceSpotConfig.from_env({})


def test_adapter_satisfies_both_ports(adapter: BinanceSpotAdapter) -> None:
    """The adapter is an ExchangePort and a StopLossPort."""
    assert isinstance(adapter, ExchangePort)
    assert isinstance(adapter, StopLossPort)


@respx.mock
def test_signed_request_carries_key_timestamp_and_valid_signature(
    adapter: BinanceSpotAdapter,
) -> None:
    """The key goes in the header, and the signature covers the exact query sent."""
    route = respx.post(url__startswith=_ORDER_URL).mock(
        return_value=httpx.Response(200, json=_filled())
    )
    adapter.place_market_order(side=OrderSide.BUY, order=_order())
    request = route.calls.last.request
    query = urlsplit(str(request.url)).query
    unsigned, _, signature = query.rpartition("&signature=")
    params = parse_qs(unsigned)
    assert request.headers["X-MBX-APIKEY"] == _KEY
    assert (params["timestamp"], params["type"], params["side"]) == (
        [str(_NOW_MS)],
        ["MARKET"],
        ["BUY"],
    )
    assert signature == sign(unsigned, _SIGNING_MATERIAL)


# ---------------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------------


@respx.mock
def test_market_order_returns_the_volume_weighted_fill(adapter: BinanceSpotAdapter) -> None:
    """The average price is quote spent over quantity, and fees sum the quote commissions."""
    respx.post(url__startswith=_ORDER_URL).mock(return_value=httpx.Response(200, json=_filled()))
    fill = adapter.place_market_order(side=OrderSide.BUY, order=_order())
    assert (fill.executed_qty, fill.avg_price, fill.fee.amount) == (
        Decimal("0.01"),
        Decimal("65000"),
        Decimal("0.65"),
    )


@respx.mock
def test_base_asset_commission_is_priced_in_the_quote(adapter: BinanceSpotAdapter) -> None:
    """A commission charged in BTC is converted at the fill price."""
    body = _filled()
    body["fills"] = [
        {"price": "65000.00", "qty": "0.01", "commission": "0.00001", "commissionAsset": "BTC"}
    ]
    respx.post(url__startswith=_ORDER_URL).mock(return_value=httpx.Response(200, json=body))
    fill = adapter.place_market_order(side=OrderSide.BUY, order=_order())
    assert fill.fee.amount == Decimal("0.65000")


_TICKER_URL = f"{TESTNET_BASE_URL}/api/v3/ticker/price"
_TRADES_URL = f"{TESTNET_BASE_URL}/api/v3/myTrades"


def _bnb_fill() -> dict[str, object]:
    body = _filled()
    body["fills"] = [
        {"price": "65000", "qty": "0.01", "commission": "0.001", "commissionAsset": "BNB"}
    ]
    return body


@respx.mock
def test_third_asset_commission_is_priced_at_its_ticker(adapter: BinanceSpotAdapter) -> None:
    """A fee paid in BNB is a filled order with its fee in the quote, not an error (#858)."""
    respx.post(url__startswith=_ORDER_URL).mock(return_value=httpx.Response(200, json=_bnb_fill()))
    ticker = respx.get(url__startswith=_TICKER_URL).mock(
        return_value=httpx.Response(200, json={"symbol": "BNBUSDT", "price": "600.00"})
    )

    fill = adapter.place_market_order(side=OrderSide.BUY, order=_order())

    assert (fill.fee.amount, fill.fee.currency, ticker.calls.last.request.url.params["symbol"]) == (
        Decimal("0.6"),
        Currency.USDT,
        "BNBUSDT",
    )


@respx.mock
def test_third_asset_commission_falls_back_to_the_taker_rate(adapter: BinanceSpotAdapter) -> None:
    """With the ticker down the fill still returns, its fee at the pre-registered 10 bps."""
    respx.post(url__startswith=_ORDER_URL).mock(return_value=httpx.Response(200, json=_bnb_fill()))
    respx.get(url__startswith=_TICKER_URL).mock(side_effect=httpx.ConnectError("down"))

    fill = adapter.place_market_order(side=OrderSide.BUY, order=_order())

    assert (fill.executed_qty, fill.fee.amount) == (Decimal("0.01"), Decimal("0.65"))


@respx.mock
@pytest.mark.parametrize(
    ("ticker", "estimated"),
    [(httpx.Response(200, json={"symbol": "BNBUSDT", "price": "600.00"}), False), (None, True)],
    ids=["priced-at-ticker", "taker-rate"],
)
def test_a_fee_priced_at_the_taker_rate_is_marked_estimated(
    adapter: BinanceSpotAdapter, ticker: httpx.Response | None, *, estimated: bool
) -> None:
    """Only a fee charged at the 10 bps fallback is marked estimated on the fill (#1014).

    The mark is what lets cost basis and tax list the fee for correction.
    """
    respx.post(url__startswith=_ORDER_URL).mock(return_value=httpx.Response(200, json=_bnb_fill()))
    route = respx.get(url__startswith=_TICKER_URL)
    if ticker is None:
        route.mock(side_effect=httpx.ConnectError("down"))
    else:
        route.mock(return_value=ticker)

    fill = adapter.place_market_order(side=OrderSide.BUY, order=_order())

    assert fill.fee_estimated is estimated


def test_an_exact_quote_fee_is_not_marked_estimated(adapter: BinanceSpotAdapter) -> None:
    """A fee Binance charged in the quote currency is exact: the fill carries no mark."""
    with respx.mock:
        respx.post(url__startswith=_ORDER_URL).mock(
            return_value=httpx.Response(200, json=_filled())
        )
        fill = adapter.place_market_order(side=OrderSide.BUY, order=_order())

    assert (fill.fee.amount, fill.fee_estimated) == (Decimal("0.65"), False)


def test_every_order_carries_a_fresh_client_order_id(adapter: BinanceSpotAdapter) -> None:
    """Two orders are sent with two different ``hermes-`` client ids, the key of any lookup."""
    with respx.mock:
        route = respx.post(url__startswith=_ORDER_URL).mock(
            return_value=httpx.Response(200, json=_filled())
        )
        adapter.place_market_order(side=OrderSide.BUY, order=_order())
        adapter.place_market_order(side=OrderSide.BUY, order=_order())
    ids = [call.request.url.params["newClientOrderId"] for call in route.calls]

    assert (len(set(ids)), [i.startswith("hermes-") and len(i) == 27 for i in ids]) == (
        2,
        [True, True],
    )


# SEV-1: no rubric row matches; an order of unknown status must never be retried as refused
@respx.mock
@pytest.mark.parametrize(
    "failure",
    [httpx.Response(503, text="<html>busy</html>"), httpx.ReadTimeout("slow")],
    ids=["5xx", "timeout"],
)
def test_an_order_whose_reply_was_lost_returns_the_fill_found_by_its_client_id(
    adapter: BinanceSpotAdapter, failure: httpx.Response | Exception
) -> None:
    """A 5xx or timeout on submission is looked up, and a filled order returns its fill.

    Binance documents 5xx as "execution status unknown": calling it a refusal
    would invite a retry that doubles the position (#858 item 1).
    """
    post = respx.post(url__startswith=_ORDER_URL)
    if isinstance(failure, Exception):
        post.mock(side_effect=failure)
    else:
        post.mock(return_value=failure)
    lookup = respx.get(url__startswith=_ORDER_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "symbol": "BTCUSDT",
                "orderId": 28,
                "status": "FILLED",
                "executedQty": "0.01",
                "cummulativeQuoteQty": "650.00",
                "updateTime": _NOW_MS,
            },
        )
    )
    respx.get(url__startswith=_TRADES_URL).mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "price": "65000.00",
                    "qty": "0.01",
                    "commission": "0.65",
                    "commissionAsset": "USDT",
                }
            ],
        )
    )

    fill = adapter.place_market_order(side=OrderSide.BUY, order=_order())

    sent = post.calls.last.request.url.params["newClientOrderId"]
    asked = lookup.calls.last.request.url.params["origClientOrderId"]
    assert (fill.executed_qty, fill.avg_price, fill.fee.amount, asked) == (
        Decimal("0.01"),
        Decimal("65000"),
        Decimal("0.65"),
        sent,
    )


@respx.mock
def test_an_order_binance_never_saw_is_not_placed(adapter: BinanceSpotAdapter) -> None:
    """Lookup code -2013 means the order does not exist: a distinct, retry-safe error."""
    respx.post(url__startswith=_ORDER_URL).mock(return_value=httpx.Response(502, text="bad"))
    respx.get(url__startswith=_ORDER_URL).mock(
        return_value=httpx.Response(400, json={"code": -2013, "msg": "Order does not exist."})
    )

    with pytest.raises(OrderNotPlacedError, match="was not placed") as caught:
        adapter.place_market_order(side=OrderSide.BUY, order=_order())

    assert caught.value.client_order_id.startswith("hermes-")


@respx.mock
def test_an_order_binance_never_saw_is_looked_up_twice_a_wait_apart(
    adapter: BinanceSpotAdapter,
) -> None:
    """-2013 is believed only when a second lookup after the recheck wait says it too (#1027).

    A 5xx can leave the order in flight inside Binance; one -2013 straight
    after it would call a live order not placed and invite a doubling retry.
    """
    respx.post(url__startswith=_ORDER_URL).mock(return_value=httpx.Response(502, text="bad"))
    lookup = respx.get(url__startswith=_ORDER_URL).mock(return_value=_NO_SUCH)

    with pytest.raises(OrderNotPlacedError, match="two lookups 2 s apart"):
        adapter.place_market_order(side=OrderSide.BUY, order=_order())

    assert (lookup.call_count, _SLEPT) == (2, [2.0])


@respx.mock
def test_an_order_in_flight_at_the_first_lookup_returns_the_fill_found_on_the_recheck(
    adapter: BinanceSpotAdapter,
) -> None:
    """A -2013 then the order on the recheck returns that fill, not "not placed" (#1027)."""
    respx.post(url__startswith=_ORDER_URL).mock(return_value=httpx.Response(503))
    lookup = respx.get(url__startswith=_ORDER_URL).mock(
        side_effect=[_NO_SUCH, httpx.Response(200, json=_FOUND_FILLED)]
    )
    respx.get(url__startswith=_TRADES_URL).mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "price": "65000.00",
                    "qty": "0.01",
                    "commission": "0.65",
                    "commissionAsset": "USDT",
                }
            ],
        )
    )

    fill = adapter.place_market_order(side=OrderSide.BUY, order=_order())

    assert (fill.executed_qty, fill.fee.amount, lookup.call_count, _SLEPT) == (
        Decimal("0.01"),
        Decimal("0.65"),
        2,
        [2.0],
    )


@respx.mock
@pytest.mark.parametrize("missing", ["cummulativeQuoteQty", "updateTime"])
def test_a_found_order_whose_reply_fails_to_parse_names_both_ids(
    adapter: BinanceSpotAdapter, missing: str
) -> None:
    """Once a lookup found the order, a reply the fill parser rejects is unknown status (#1027).

    The error carries the client order id and Binance's order id, so the
    operator can reconcile the live order instead of retrying it.
    """
    post = respx.post(url__startswith=_ORDER_URL).mock(return_value=httpx.Response(503))
    found = {key: value for key, value in _FOUND_FILLED.items() if key != missing}
    respx.get(url__startswith=_ORDER_URL).mock(return_value=httpx.Response(200, json=found))
    respx.get(url__startswith=_TRADES_URL).mock(return_value=httpx.Response(200, json=[]))

    with pytest.raises(OrderStatusUnknownError, match="Binance order 28") as caught:
        adapter.place_market_order(side=OrderSide.BUY, order=_order())

    sent = post.calls.last.request.url.params["newClientOrderId"]
    assert (caught.value.client_order_id, caught.value.order_id) == (sent, 28)


@respx.mock
def test_a_failed_lookup_names_the_client_id_to_reconcile(adapter: BinanceSpotAdapter) -> None:
    """When the lookup fails too, the error says the status is unknown and names the order."""
    post = respx.post(url__startswith=_ORDER_URL).mock(return_value=httpx.Response(500))
    respx.get(url__startswith=_ORDER_URL).mock(side_effect=httpx.ConnectError("down"))

    with pytest.raises(OrderStatusUnknownError, match="reconcile") as caught:
        adapter.place_market_order(side=OrderSide.BUY, order=_order())

    assert caught.value.client_order_id == post.calls.last.request.url.params["newClientOrderId"]


@respx.mock
def test_a_found_order_whose_trades_cannot_be_read_names_its_binance_id(
    adapter: BinanceSpotAdapter,
) -> None:
    """A placed order whose fees can't be read is unknown, and the error carries its order id."""
    respx.post(url__startswith=_ORDER_URL).mock(return_value=httpx.Response(504))
    respx.get(url__startswith=_ORDER_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "orderId": 4242,
                "status": "FILLED",
                "executedQty": "0.01",
                "cummulativeQuoteQty": "650.00",
                "updateTime": _NOW_MS,
            },
        )
    )
    respx.get(url__startswith=_TRADES_URL).mock(return_value=httpx.Response(503))

    with pytest.raises(OrderStatusUnknownError, match="Binance order 4242"):
        adapter.place_market_order(side=OrderSide.BUY, order=_order())


@respx.mock
def test_a_malformed_lookup_reply_still_names_the_client_id(adapter: BinanceSpotAdapter) -> None:
    """A lookup reply with a bad executedQty is unknown status, and names both order ids."""
    post = respx.post(url__startswith=_ORDER_URL).mock(return_value=httpx.Response(503))
    respx.get(url__startswith=_ORDER_URL).mock(
        return_value=httpx.Response(
            200, json={"orderId": 77, "status": "FILLED", "executedQty": None}
        )
    )

    with pytest.raises(OrderStatusUnknownError, match="Binance order 77") as caught:
        adapter.place_market_order(side=OrderSide.BUY, order=_order())

    assert caught.value.client_order_id == post.calls.last.request.url.params["newClientOrderId"]


@respx.mock
@pytest.mark.parametrize("status", ["NEW", "PARTIALLY_FILLED"])
def test_a_lost_ioc_limit_found_still_working_is_unknown_not_rejected(
    adapter: BinanceSpotAdapter, status: str
) -> None:
    """An IOC order found still working may yet fill, so it is unknown status, never a Rejection."""
    post = respx.post(url__startswith=_ORDER_URL).mock(return_value=httpx.Response(503))
    respx.get(url__startswith=_ORDER_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "orderId": 91,
                "status": status,
                "executedQty": "0",
                "cummulativeQuoteQty": "0",
                "updateTime": _NOW_MS,
            },
        )
    )

    with pytest.raises(
        OrderStatusUnknownError, match=f"Binance order 91.*still {status}"
    ) as caught:
        adapter.place_limit_order(order=_order(limit="65000"))

    assert caught.value.client_order_id == post.calls.last.request.url.params["newClientOrderId"]


@respx.mock
def test_a_stop_loss_whose_reply_was_lost_returns_the_found_order_id(
    adapter: BinanceSpotAdapter,
) -> None:
    """The resting stop is found by client id, so it is not placed twice."""
    respx.post(url__startswith=_ORDER_URL).mock(side_effect=httpx.ReadTimeout("slow"))
    respx.get(url__startswith=_ORDER_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "orderId": 77,
                "status": "NEW",
                "executedQty": "0",
                "cummulativeQuoteQty": "0",
                "time": _NOW_MS,
            },
        )
    )

    order_id = adapter.place_stop_loss(order=_order(OrderSide.SELL), stop_price=Decimal("60000"))

    assert order_id == "BTCUSDT:77"


@respx.mock
def test_a_stop_loss_in_flight_at_the_first_lookup_returns_the_order_found_on_the_recheck(
    adapter: BinanceSpotAdapter,
) -> None:
    """A lost stop-loss read -2013 then resting returns its order id, not "not placed" (#1027).

    Calling it not placed would let the caller place a second stop for the same position.
    """
    respx.post(url__startswith=_ORDER_URL).mock(side_effect=httpx.ReadTimeout("slow"))
    lookup = respx.get(url__startswith=_ORDER_URL).mock(
        side_effect=[
            _NO_SUCH,
            httpx.Response(
                200,
                json={
                    "orderId": 79,
                    "status": "NEW",
                    "executedQty": "0",
                    "cummulativeQuoteQty": "0",
                    "time": _NOW_MS,
                },
            ),
        ]
    )

    order_id = adapter.place_stop_loss(order=_order(OrderSide.SELL), stop_price=Decimal("60000"))

    assert (order_id, lookup.call_count, _SLEPT) == ("BTCUSDT:79", 2, [2.0])


# SEV-1: no rubric row matches; a dead stop reported as resting leaves a position unprotected
@respx.mock
@pytest.mark.parametrize("status", ["CANCELED", "EXPIRED", "REJECTED"])
def test_a_lost_stop_loss_found_dead_is_not_reported_as_resting(
    adapter: BinanceSpotAdapter, status: str
) -> None:
    """A lost stop-loss found cancelled, expired or rejected raises: it is no cover."""
    respx.post(url__startswith=_ORDER_URL).mock(side_effect=httpx.ReadTimeout("slow"))
    respx.get(url__startswith=_ORDER_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "orderId": 78,
                "status": status,
                "executedQty": "0",
                "cummulativeQuoteQty": "0",
                "time": _NOW_MS,
            },
        )
    )

    with pytest.raises(ExchangeError, match=f"Binance order 78.*found {status}.*not on the book"):
        adapter.place_stop_loss(order=_order(OrderSide.SELL), stop_price=Decimal("60000"))


@respx.mock
def test_ioc_limit_that_fills_returns_a_fill(adapter: BinanceSpotAdapter) -> None:
    """A filled IOC limit is a Fill, and the request is IOC at the given price."""
    route = respx.post(url__startswith=_ORDER_URL).mock(
        return_value=httpx.Response(200, json=_filled())
    )
    result = adapter.place_limit_order(order=_order(limit="65100"))
    params = parse_qs(urlsplit(str(route.calls.last.request.url)).query)
    assert isinstance(result, Fill)
    assert (params["timeInForce"], params["price"]) == (["IOC"], ["65100"])


@respx.mock
def test_ioc_limit_that_misses_is_a_rejection(adapter: BinanceSpotAdapter) -> None:
    """Nothing executed at the limit is a business Rejection, not an error."""
    body = {**_filled(executed="0.00000000", quote="0.00000000"), "status": "EXPIRED", "fills": []}
    respx.post(url__startswith=_ORDER_URL).mock(return_value=httpx.Response(200, json=body))
    result = adapter.place_limit_order(order=_order(limit="60000"))
    assert isinstance(result, Rejection)
    assert "EXPIRED" in result.reason


def test_limit_order_without_price_is_a_caller_error(adapter: BinanceSpotAdapter) -> None:
    """place_limit_order needs a limit price."""
    with pytest.raises(ValueError, match="limit_price"):
        adapter.place_limit_order(order=_order())


# ---------------------------------------------------------------------------
# Stop-loss and cancel
# ---------------------------------------------------------------------------


@respx.mock
def test_stop_loss_rests_a_sell_with_a_buffered_limit(adapter: BinanceSpotAdapter) -> None:
    """The stop triggers at stop_price and may fill down to 1% below it."""
    route = respx.post(url__startswith=_ORDER_URL).mock(
        return_value=httpx.Response(200, json={"symbol": "BTCUSDT", "orderId": 77})
    )
    order_id = adapter.place_stop_loss(order=_order(OrderSide.SELL), stop_price=Decimal("60000"))
    params = parse_qs(urlsplit(str(route.calls.last.request.url)).query)
    assert order_id == "BTCUSDT:77"
    assert (params["type"], params["stopPrice"], params["price"]) == (
        ["STOP_LOSS_LIMIT"],
        ["60000"],
        ["5.94E+4"],
    )


def test_stop_loss_must_be_a_sell(adapter: BinanceSpotAdapter) -> None:
    """A stop protects a long position, so a BUY is refused before any request."""
    with pytest.raises(ValueError, match="SELL"):
        adapter.place_stop_loss(order=_order(OrderSide.BUY), stop_price=Decimal("60000"))


@respx.mock
def test_cancelling_a_gone_order_is_not_an_error(adapter: BinanceSpotAdapter) -> None:
    """Binance -2011 (unknown order) means it already filled or was cancelled: idempotent."""
    respx.delete(url__startswith=_ORDER_URL).mock(
        return_value=httpx.Response(400, json={"code": -2011, "msg": "Unknown order sent."})
    )
    adapter.cancel_order(order_id="BTCUSDT:77")


@respx.mock
def test_other_cancel_refusals_raise(adapter: BinanceSpotAdapter) -> None:
    """Any other refusal is surfaced."""
    respx.delete(url__startswith=_ORDER_URL).mock(
        return_value=httpx.Response(400, json={"code": -1102, "msg": "Mandatory parameter"})
    )
    with pytest.raises(ExchangeError, match="-1102"):
        adapter.cancel_order(order_id="BTCUSDT:77")


def test_malformed_order_id_is_a_caller_error(adapter: BinanceSpotAdapter) -> None:
    """Order ids are SYMBOL:ORDERID."""
    with pytest.raises(ValueError, match="SYMBOL:ORDERID"):
        adapter.cancel_order(order_id="77")


# ---------------------------------------------------------------------------
# Reads and errors
# ---------------------------------------------------------------------------


@respx.mock
def test_orderbook_keeps_levels_in_order(adapter: BinanceSpotAdapter) -> None:
    """Depth levels become an Orderbook with the exchange's bid and ask order."""
    respx.get(url__startswith=f"{TESTNET_BASE_URL}/api/v3/depth").mock(
        return_value=httpx.Response(
            200,
            json={
                "lastUpdateId": 1,
                "bids": [["64999.00", "0.5"], ["64998.00", "1.0"]],
                "asks": [["65001.00", "0.4"]],
            },
        )
    )
    book = adapter.get_orderbook(pair=_BTC)
    assert ([lvl.price for lvl in book.bids], book.asks[0].quantity) == (
        [Decimal("64999.00"), Decimal("64998.00")],
        Decimal("0.4"),
    )


_ACCOUNT = {
    "commissionRates": {"maker": "0.00100000", "taker": "0.00100000"},
    "balances": [
        {"asset": "BTC", "free": "1.25000000", "locked": "0.10000000"},
        {"asset": "USDT", "free": "10000.00000000", "locked": "0.00000000"},
    ],
}


@respx.mock
def test_balance_is_the_free_amount(adapter: BinanceSpotAdapter) -> None:
    """Locked funds in open orders are not available."""
    respx.get(url__startswith=f"{TESTNET_BASE_URL}/api/v3/account").mock(
        return_value=httpx.Response(200, json=_ACCOUNT)
    )
    assert adapter.get_balance(currency=Currency.BTC).amount == Decimal("1.25")


@respx.mock
def test_unheld_currency_is_zero(adapter: BinanceSpotAdapter) -> None:
    """A currency absent from the account has a zero balance, not an error."""
    respx.get(url__startswith=f"{TESTNET_BASE_URL}/api/v3/account").mock(
        return_value=httpx.Response(200, json=_ACCOUNT)
    )
    assert adapter.get_balance(currency=Currency.SOL) == Quantity(
        amount=Decimal("0"), asset=Currency.SOL
    )


@respx.mock
def test_fees_come_from_account_commission_rates(adapter: BinanceSpotAdapter) -> None:
    """0.1% maker and taker are 10 bps each, in one tier."""
    respx.get(url__startswith=f"{TESTNET_BASE_URL}/api/v3/account").mock(
        return_value=httpx.Response(200, json=_ACCOUNT)
    )
    tier = adapter.get_fees(pair=_BTC).tiers[0]
    assert (tier.maker_bps, tier.taker_bps) == (10, 10)


@respx.mock
def test_rate_limit_reply_is_not_retried(adapter: BinanceSpotAdapter) -> None:
    """A 429 surfaces at once: retrying would risk an IP ban."""
    route = respx.get(url__startswith=f"{TESTNET_BASE_URL}/api/v3/account").mock(
        return_value=httpx.Response(429, json={"code": -1003, "msg": "Too many requests"})
    )
    with pytest.raises(ExchangeError, match="rate limited"):
        adapter.get_balance(currency=Currency.BTC)
    assert route.call_count == 1


@respx.mock
def test_transport_failure_is_a_network_error(adapter: BinanceSpotAdapter) -> None:
    """A connection error is a NetworkError and never leaks the signed query."""
    respx.get(url__startswith=f"{TESTNET_BASE_URL}/api/v3/account").mock(
        side_effect=httpx.ConnectError("down")
    )
    with pytest.raises(NetworkError) as caught:
        adapter.get_balance(currency=Currency.BTC)
    assert "signature" not in str(caught.value)
