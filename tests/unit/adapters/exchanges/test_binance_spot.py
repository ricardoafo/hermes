"""Unit tests for the Binance spot trading adapter (#148).

Every request is answered by respx with replies shaped like the Binance spot
REST documentation; no test reaches the network. The real-testnet round trip
lives in ``tests/contract/test_binance_spot_testnet.py``.
"""

from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import respx

from hermes.adapters.exchanges.binance_spot import (
    TESTNET_BASE_URL,
    BinanceSpotAdapter,
    BinanceSpotConfig,
    sign,
)
from hermes.adapters.exchanges.rate_limiter import RateLimitConfig, RateLimiter
from hermes.domain.errors import ExchangeError, NetworkError
from hermes.domain.trading.fill import Fill, Rejection
from hermes.domain.trading.order import Order, OrderSide
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.quantity import Quantity
from hermes.domain.value_objects.strategy_id import StrategyId
from hermes.ports.exchange import ExchangePort, StopLossPort

_ORDER_URL = f"{TESTNET_BASE_URL}/api/v3/order"
_BTC = Pair(base=Currency.BTC, quote=Currency.USDT)
_NOW_MS = 1_790_000_000_000
_KEY = "test-key-not-real"  # gitleaks:allow
_SIGNING_MATERIAL = "test-secret-not-real"  # gitleaks:allow


@pytest.fixture
def adapter() -> Iterator[BinanceSpotAdapter]:
    """Return an adapter on the testnet host with a fixed clock and a roomy limiter."""
    config = BinanceSpotConfig(api_key=_KEY, api_secret=_SIGNING_MATERIAL)
    client = httpx.Client(base_url=config.base_url)
    limiter = RateLimiter(config=RateLimitConfig(max_weight=1000, window_seconds=60))
    yield BinanceSpotAdapter(
        config=config, client=client, limiter=limiter, clock_ms=lambda: _NOW_MS
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
    """api.binance.com is refused unless allow_mainnet is set."""
    with pytest.raises(ValueError, match="not the Binance testnet"):
        BinanceSpotConfig(
            api_key=_KEY, api_secret=_SIGNING_MATERIAL, base_url="https://api.binance.com"
        )


def test_mainnet_host_is_accepted_with_permission() -> None:
    """With allow_mainnet the composition root may build a mainnet config."""
    config = BinanceSpotConfig(
        api_key=_KEY,
        api_secret=_SIGNING_MATERIAL,
        base_url="https://api.binance.com",
        allow_mainnet=True,
    )
    assert config.base_url == "https://api.binance.com"


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


@respx.mock
def test_third_asset_commission_is_refused(adapter: BinanceSpotAdapter) -> None:
    """A fee in BNB can't be priced without another market, so it is an error."""
    body = _filled()
    body["fills"] = [
        {"price": "65000", "qty": "0.01", "commission": "0.001", "commissionAsset": "BNB"}
    ]
    respx.post(url__startswith=_ORDER_URL).mock(return_value=httpx.Response(200, json=body))
    with pytest.raises(ExchangeError, match="BNB"):
        adapter.place_market_order(side=OrderSide.BUY, order=_order())


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
