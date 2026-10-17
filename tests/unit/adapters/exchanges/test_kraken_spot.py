"""Unit tests for the Kraken spot trading adapter (#32).

Every request is answered by respx with the recorded replies in
``tests/fixtures/kraken_spot``; no test reaches the network. Kraken has no
spot testnet, so there is no live contract test: a signed call would touch a
real account.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, parse_qsl

import httpx
import pytest
import respx

from hermes.adapters.exchanges.kraken_public import KrakenPublicAdapter, KrakenPublicConfig
from hermes.adapters.exchanges.kraken_spot import (
    KRAKEN_BASE_URL,
    KrakenSpotAdapter,
    KrakenSpotConfig,
    OrderUnreadError,
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
from tests.support.mainnet_permit import issued_permit

_FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "kraken_spot"
_PRIVATE = f"{KRAKEN_BASE_URL}/0/private"
_BTC = Pair(base=Currency.BTC, quote=Currency.USDT)
_NOW_MS = 1_790_000_000_000
_KEY = "test-key-not-real"  # gitleaks:allow
_SIGNING_MATERIAL = "dGVzdC1zZWNyZXQtbm90LXJlYWw="  # gitleaks:allow
_MARKET_TXID = "OUF4EM-FRGI2-MQMWZD"
_LIMIT_TXID = "OQCLML-BW3P3-BUCMWZ"


def _reply(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((_FIXTURES / f"{name}.json").read_text())
    return data


def _ok(name: str) -> httpx.Response:
    return httpx.Response(200, json=_reply(name))


def _build(*, trading: bool, sleeps: list[float] | None = None) -> KrakenSpotAdapter:
    config = KrakenSpotConfig(
        api_key=_KEY,
        api_secret=_SIGNING_MATERIAL,
        mainnet_permit=issued_permit() if trading else None,
    )
    roomy = RateLimitConfig(max_weight=1000, window_seconds=60)
    public_config = KrakenPublicConfig(rate_limit=roomy)
    public = KrakenPublicAdapter(
        config=public_config,
        client=httpx.Client(base_url=KRAKEN_BASE_URL),
        limiter=RateLimiter(config=roomy),
    )
    record: Callable[[float], None] = (sleeps if sleeps is not None else []).append
    return KrakenSpotAdapter(
        config=config,
        client=httpx.Client(base_url=KRAKEN_BASE_URL),
        limiter=RateLimiter(config=roomy),
        public=public,
        clock_ms=lambda: _NOW_MS,
        sleep=record,
    )


@pytest.fixture
def trading() -> Iterator[KrakenSpotAdapter]:
    """Return an adapter allowed to trade, with a frozen clock and a roomy limiter."""
    adapter = _build(trading=True)
    yield adapter
    adapter.close()


@pytest.fixture
def read_only() -> Iterator[KrakenSpotAdapter]:
    """Return an adapter without a mainnet permit, the default."""
    adapter = _build(trading=False)
    yield adapter
    adapter.close()


def _order(side: OrderSide = OrderSide.BUY, limit: str | None = None) -> Order:
    return Order(
        side=side,
        pair=_BTC,
        qty=Quantity(amount=Decimal("0.01"), asset=Currency.BTC),
        strategy_id=StrategyId("swing-donchian-4h"),
        limit_price=None if limit is None else Decimal(limit),
    )


def _form(request: httpx.Request) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(request.content.decode()).items()}


# ---------------------------------------------------------------------------
# Signing and safety
# ---------------------------------------------------------------------------


def test_signature_matches_the_kraken_documentation_vector() -> None:
    """HMAC-SHA512 over path + SHA256(nonce + body) reproduces Kraken's published example."""
    doc_key_material = (
        "kQH5HW/8p1uGOVjbgWA7FunAmGO8lsSUXNsu3eow76sz84Q18f"  # gitleaks:allow
        "WxnyRzBHCd3pd5nE9qa99HAZtuZuj6F1huXg=="  # gitleaks:allow
    )
    assert sign(
        path="/0/private/AddOrder",
        nonce="1616492376594",
        post_data="nonce=1616492376594&ordertype=limit&pair=XBTUSD&price=37500&type=buy&volume=1.25",
        secret=doc_key_material,
    ) == (
        "4/dpxb3iT4tp/ZCVEwSnEsLxx0bqyhLpdfOpc6fn7OR8+UClSV5n9E6aSS8MPtnRfp32bAb0nmbRn6H8ndwLUQ=="
    )


@pytest.mark.parametrize(
    "url",
    ["https://api.kraken.com.evil.example", "http://api.kraken.com", "https://futures.kraken.com"],
)
def test_only_the_kraken_spot_host_is_accepted(url: str) -> None:
    """Signed requests can only go to https://api.kraken.com."""
    with pytest.raises(ValueError, match="base_url must be"):
        KrakenSpotConfig(api_key=_KEY, api_secret=_SIGNING_MATERIAL, base_url=url)


def test_secret_that_is_not_base64_is_refused() -> None:
    """A pasted secret with a typo fails at construction, not at the first signed call."""
    with pytest.raises(ValueError, match="base64"):
        KrakenSpotConfig(api_key=_KEY, api_secret="not base64!")  # noqa: S106  # gitleaks:allow


def test_secret_is_kept_out_of_repr() -> None:
    """The API secret never appears in a repr or a log line built from the config."""
    assert _SIGNING_MATERIAL not in repr(
        KrakenSpotConfig(api_key=_KEY, api_secret=_SIGNING_MATERIAL)
    )


def test_missing_credentials_are_refused() -> None:
    """from_env without credentials raises instead of sending unsigned requests."""
    with pytest.raises(ValueError, match="required"):
        KrakenSpotConfig.from_env({})


def test_from_env_reads_the_kraken_variables() -> None:
    """HERMES_KRAKEN__API_KEY and HERMES_KRAKEN__API_SECRET populate the config."""
    config = KrakenSpotConfig.from_env(
        {"HERMES_KRAKEN__API_KEY": _KEY, "HERMES_KRAKEN__API_SECRET": _SIGNING_MATERIAL}
    )
    assert config.api_key == _KEY
    assert config.mainnet_permit is None


def test_adapter_satisfies_both_ports(read_only: KrakenSpotAdapter) -> None:
    """The adapter is an ExchangePort and a StopLossPort."""
    assert isinstance(read_only, ExchangePort)
    assert isinstance(read_only, StopLossPort)


# SEV-1: real capital stays behind the live-approval gate (AGENTS.md rule 7, ADR-014)
@pytest.mark.parametrize(
    "action",
    [
        lambda a: a.place_market_order(side=OrderSide.BUY, order=_order()),
        lambda a: a.place_limit_order(order=_order(limit="64000")),
        lambda a: a.place_stop_loss(order=_order(OrderSide.SELL), stop_price=Decimal("60000")),
        lambda a: a.cancel_order(order_id=_MARKET_TXID),
    ],
    ids=["market", "limit", "stop-loss", "cancel"],
)
@respx.mock(assert_all_mocked=True)
def test_writes_are_refused_without_a_permit_before_any_request(
    read_only: KrakenSpotAdapter, action: Callable[[KrakenSpotAdapter], object]
) -> None:
    """Without a mainnet permit no order is placed or cancelled, and nothing is sent."""
    with pytest.raises(ExchangeError, match="read-only without a MainnetPermit"):
        action(read_only)
    assert respx.calls.call_count == 0


@respx.mock
def test_signed_request_carries_key_nonce_and_valid_signature(
    trading: KrakenSpotAdapter,
) -> None:
    """The key goes in a header, and API-Sign covers the exact body sent."""
    route = respx.post(f"{_PRIVATE}/AddOrder").mock(return_value=_ok("add_order_market_buy"))
    respx.post(f"{_PRIVATE}/QueryOrders").mock(return_value=_ok("query_orders_market_closed"))
    trading.place_market_order(side=OrderSide.BUY, order=_order())
    request = route.calls.last.request
    body = request.content.decode()
    nonce = _form(request)["nonce"]
    assert request.headers["API-Key"] == _KEY
    assert request.headers["API-Sign"] == sign(
        path="/0/private/AddOrder", nonce=nonce, post_data=body, secret=_SIGNING_MATERIAL
    )
    assert nonce == str(_NOW_MS)


@respx.mock
def test_nonce_increases_even_when_the_clock_does_not(trading: KrakenSpotAdapter) -> None:
    """Two calls in one millisecond still send strictly increasing nonces."""
    route = respx.post(f"{_PRIVATE}/BalanceEx").mock(return_value=_ok("balance_ex"))
    trading.get_balance(currency=Currency.BTC)
    trading.get_balance(currency=Currency.BTC)
    first, second = (int(_form(call.request)["nonce"]) for call in route.calls)
    assert second == first + 1


# ---------------------------------------------------------------------------
# Market orders
# ---------------------------------------------------------------------------


@respx.mock
def test_market_order_sends_pair_side_volume_and_quote_fees(trading: KrakenSpotAdapter) -> None:
    """AddOrder names Kraken's pair, the side, the volume, and asks for fees in the quote."""
    route = respx.post(f"{_PRIVATE}/AddOrder").mock(return_value=_ok("add_order_market_buy"))
    respx.post(f"{_PRIVATE}/QueryOrders").mock(return_value=_ok("query_orders_market_closed"))
    trading.place_market_order(side=OrderSide.BUY, order=_order())
    form = _form(route.calls.last.request)
    assert {k: form[k] for k in ("pair", "type", "ordertype", "volume", "oflags")} == {
        "pair": "XBTUSDT",
        "type": "buy",
        "ordertype": "market",
        "volume": "0.01",
        "oflags": "fciq",
    }


@respx.mock
def test_market_order_returns_the_settled_execution(trading: KrakenSpotAdapter) -> None:
    """The fill carries Kraken's executed volume, cost/volume price, quote fee and close time."""
    respx.post(f"{_PRIVATE}/AddOrder").mock(return_value=_ok("add_order_market_buy"))
    query = respx.post(f"{_PRIVATE}/QueryOrders").mock(
        return_value=_ok("query_orders_market_closed")
    )
    fill = trading.place_market_order(side=OrderSide.BUY, order=_order())
    assert _form(query.calls.last.request)["txid"] == _MARKET_TXID
    assert (fill.executed_qty, fill.avg_price, fill.fee.amount, fill.fee.currency) == (
        Decimal("0.01"),
        Decimal("65000"),
        Decimal("2.6"),
        Currency.USDT,
    )
    assert fill.timestamp == datetime.fromtimestamp(1790000000.4567, tz=UTC)


@respx.mock
def test_market_order_waits_for_a_pending_order_to_close() -> None:
    """A pending order is read back again after a pause until it closes."""
    sleeps: list[float] = []
    adapter = _build(trading=True, sleeps=sleeps)
    respx.post(f"{_PRIVATE}/AddOrder").mock(return_value=_ok("add_order_market_buy"))
    respx.post(f"{_PRIVATE}/QueryOrders").mock(
        side_effect=[_ok("query_orders_market_pending"), _ok("query_orders_market_closed")]
    )
    fill = adapter.place_market_order(side=OrderSide.BUY, order=_order())
    adapter.close()
    assert isinstance(fill, Fill)
    assert sleeps == [0.5]


@respx.mock
def test_market_order_that_never_settles_names_its_txid(trading: KrakenSpotAdapter) -> None:
    """After settle_checks reads the error names the txid so the operator can reconcile."""
    respx.post(f"{_PRIVATE}/AddOrder").mock(return_value=_ok("add_order_market_buy"))
    query = respx.post(f"{_PRIVATE}/QueryOrders").mock(
        return_value=_ok("query_orders_market_pending")
    )
    with pytest.raises(ExchangeError, match=f"order {_MARKET_TXID} still pending after 5 checks"):
        trading.place_market_order(side=OrderSide.BUY, order=_order())
    assert query.call_count == 5


@respx.mock
def test_market_order_that_executed_nothing_is_an_error(trading: KrakenSpotAdapter) -> None:
    """A market order closed with zero volume is not a fill."""
    reply = _reply("query_orders_market_closed")
    reply["result"][_MARKET_TXID].update(status="canceled", vol_exec="0.00000000")
    respx.post(f"{_PRIVATE}/AddOrder").mock(return_value=_ok("add_order_market_buy"))
    respx.post(f"{_PRIVATE}/QueryOrders").mock(return_value=httpx.Response(200, json=reply))
    with pytest.raises(ExchangeError, match="executed nothing: status canceled"):
        trading.place_market_order(side=OrderSide.BUY, order=_order())


# ---------------------------------------------------------------------------
# Limit orders
# ---------------------------------------------------------------------------


@respx.mock
def test_limit_order_is_immediate_or_cancel_at_the_limit(trading: KrakenSpotAdapter) -> None:
    """AddOrder carries ordertype=limit, the price and timeinforce=IOC."""
    route = respx.post(f"{_PRIVATE}/AddOrder").mock(return_value=_ok("add_order_limit_ioc"))
    respx.post(f"{_PRIVATE}/QueryOrders").mock(return_value=_ok("query_orders_limit_partial"))
    trading.place_limit_order(order=_order(limit="64000"))
    form = _form(route.calls.last.request)
    assert (form["ordertype"], form["price"], form["timeinforce"]) == ("limit", "64000", "IOC")


@respx.mock
def test_partly_filled_limit_order_returns_the_partial_fill(trading: KrakenSpotAdapter) -> None:
    """An IOC limit cancelled with some volume executed is a fill for that volume."""
    respx.post(f"{_PRIVATE}/AddOrder").mock(return_value=_ok("add_order_limit_ioc"))
    respx.post(f"{_PRIVATE}/QueryOrders").mock(return_value=_ok("query_orders_limit_partial"))
    fill = trading.place_limit_order(order=_order(limit="64000"))
    assert isinstance(fill, Fill)
    assert (fill.executed_qty, fill.avg_price) == (Decimal("0.004"), Decimal("63900"))


@respx.mock
def test_unfilled_limit_order_is_a_rejection(trading: KrakenSpotAdapter) -> None:
    """An IOC limit with nothing executed is a business Rejection, not an error."""
    respx.post(f"{_PRIVATE}/AddOrder").mock(return_value=_ok("add_order_limit_ioc"))
    respx.post(f"{_PRIVATE}/QueryOrders").mock(return_value=_ok("query_orders_limit_unfilled"))
    result = trading.place_limit_order(order=_order(limit="60000"))
    assert isinstance(result, Rejection)
    assert _LIMIT_TXID in result.reason


def test_limit_order_without_a_price_is_refused(trading: KrakenSpotAdapter) -> None:
    """place_limit_order on a market order is a programming error."""
    with pytest.raises(ValueError, match="limit_price"):
        trading.place_limit_order(order=_order())


# ---------------------------------------------------------------------------
# Stop-loss
# ---------------------------------------------------------------------------


@respx.mock
def test_stop_loss_rests_a_stop_loss_limit_sell(trading: KrakenSpotAdapter) -> None:
    """The stop triggers at stop_price on the last trade with its limit 1% below."""
    route = respx.post(f"{_PRIVATE}/AddOrder").mock(return_value=_ok("add_order_stop_loss"))
    txid = trading.place_stop_loss(order=_order(OrderSide.SELL), stop_price=Decimal("60000"))
    form = _form(route.calls.last.request)
    assert txid == "OSTOPL-OSS12-ABCDEF"
    assert {k: form[k] for k in ("type", "ordertype", "price", "price2", "trigger")} == {
        "type": "sell",
        "ordertype": "stop-loss-limit",
        "price": "60000",
        "price2": "59400",
        "trigger": "last",
    }


@pytest.mark.parametrize(
    ("side", "stop"), [(OrderSide.BUY, "60000"), (OrderSide.SELL, "0")], ids=["buy", "zero"]
)
def test_stop_loss_must_be_a_positive_sell(
    trading: KrakenSpotAdapter, side: OrderSide, stop: str
) -> None:
    """A stop-loss protects a long position, so only a SELL at a positive stop is accepted."""
    with pytest.raises(ValueError, match=r"SELL|positive"):
        trading.place_stop_loss(order=_order(side), stop_price=Decimal(stop))


# ---------------------------------------------------------------------------
# Cancel
# ---------------------------------------------------------------------------


@respx.mock
def test_cancel_sends_the_txid(trading: KrakenSpotAdapter) -> None:
    """CancelOrder carries the Kraken txid."""
    route = respx.post(f"{_PRIVATE}/CancelOrder").mock(return_value=_ok("cancel_order"))
    trading.cancel_order(order_id=_MARKET_TXID)
    assert _form(route.calls.last.request)["txid"] == _MARKET_TXID


@respx.mock
def test_cancelling_an_unknown_order_is_not_an_error(trading: KrakenSpotAdapter) -> None:
    """EOrder:Unknown order means the order is already gone, so the cancel succeeds."""
    respx.post(f"{_PRIVATE}/CancelOrder").mock(return_value=_ok("cancel_order_unknown"))
    trading.cancel_order(order_id=_MARKET_TXID)


@respx.mock
def test_cancel_refused_for_another_reason_raises(trading: KrakenSpotAdapter) -> None:
    """Any Kraken error other than an unknown order still raises."""
    respx.post(f"{_PRIVATE}/CancelOrder").mock(return_value=_ok("error_insufficient_funds"))
    with pytest.raises(ExchangeError, match="Insufficient funds"):
        trading.cancel_order(order_id=_MARKET_TXID)


def test_cancel_refuses_an_id_that_is_not_a_txid(trading: KrakenSpotAdapter) -> None:
    """A Binance-style SYMBOL:ID never reaches Kraken."""
    with pytest.raises(ValueError, match="Kraken txid"):
        trading.cancel_order(order_id="BTCUSDT:28")


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.parametrize(
    ("currency", "free"),
    [(Currency.BTC, "0.04"), (Currency.USDT, "1200"), (Currency.EUR, "15"), (Currency.SOL, "0")],
)
def test_balance_is_free_balance_under_kraken_asset_codes(
    read_only: KrakenSpotAdapter, currency: Currency, free: str
) -> None:
    """BTC is XXBT and EUR is ZEUR; funds held in orders are excluded; unheld is zero."""
    respx.post(f"{_PRIVATE}/BalanceEx").mock(return_value=_ok("balance_ex"))
    assert read_only.get_balance(currency=currency) == Quantity(
        amount=Decimal(free), asset=currency
    )


@respx.mock
def test_orderbook_comes_from_public_depth(read_only: KrakenSpotAdapter) -> None:
    """The book is the public Depth endpoint, 100 levels deep."""
    route = respx.get(f"{KRAKEN_BASE_URL}/0/public/Depth").mock(return_value=_ok("depth_xbtusdt"))
    book = read_only.get_orderbook(pair=_BTC)
    assert route.calls.last.request.url.params["count"] == "100"
    assert (book.bids[0].price, book.asks[0].price) == (Decimal("65000.0"), Decimal("65010.0"))


@respx.mock
def test_fees_are_the_accounts_trade_volume_fees(read_only: KrakenSpotAdapter) -> None:
    """Fees come from the signed TradeVolume call for the pair, percent to bps (#599).

    Kraken stopped publishing fee tiers in the public AssetPairs reply, so the
    account's own fee is the source; the call is read-only.
    """
    route = respx.post(f"{_PRIVATE}/TradeVolume").mock(return_value=_ok("trade_volume_xbtusdt"))
    schedule = read_only.get_fees(pair=_BTC)
    sent = dict(parse_qsl(route.calls.last.request.content.decode()))
    assert (sent["pair"], [(t.maker_bps, t.taker_bps) for t in schedule.tiers]) == (
        "XBTUSDT",
        [(25, 40)],
    )


@respx.mock
def test_trade_volume_reply_without_fees_raises(read_only: KrakenSpotAdapter) -> None:
    """A TradeVolume reply with no fee for the pair is an ExchangeError, not a zero fee."""
    respx.post(f"{_PRIVATE}/TradeVolume").mock(return_value=_ok("trade_volume_no_fees"))
    with pytest.raises(ExchangeError, match="TradeVolume has no 'fees'"):
        read_only.get_fees(pair=_BTC)


# ---------------------------------------------------------------------------
# Wire failures
# ---------------------------------------------------------------------------


@respx.mock
def test_kraken_error_list_raises_with_the_kraken_text(trading: KrakenSpotAdapter) -> None:
    """A non-empty error array is an ExchangeError carrying Kraken's own words."""
    respx.post(f"{_PRIVATE}/AddOrder").mock(return_value=_ok("error_insufficient_funds"))
    with pytest.raises(ExchangeError, match="EOrder:Insufficient funds"):
        trading.place_market_order(side=OrderSide.BUY, order=_order())


@respx.mock
def test_rate_limit_error_is_not_retried(trading: KrakenSpotAdapter) -> None:
    """EAPI:Rate limit exceeded raises at once, after a single request."""
    route = respx.post(f"{_PRIVATE}/BalanceEx").mock(return_value=_ok("error_rate_limit"))
    with pytest.raises(ExchangeError, match="limiter is misconfigured"):
        trading.get_balance(currency=Currency.BTC)
    assert route.call_count == 1


@respx.mock
@pytest.mark.parametrize(
    ("response", "match"),
    [
        (httpx.Response(502, text="bad gateway"), "HTTP 502"),
        (httpx.Response(200, text="<html>"), "non-JSON"),
        (httpx.Response(200, json=["x"]), "JSON object"),
        (httpx.Response(200, json={"error": "x"}), "not a list"),
        (httpx.Response(200, json={"error": []}), "no result"),
    ],
    ids=["http-error", "non-json", "array", "error-not-list", "no-result"],
)
def test_malformed_replies_raise_exchange_error(
    read_only: KrakenSpotAdapter, response: httpx.Response, match: str
) -> None:
    """Every malformed reply surfaces as an ExchangeError, never a raw parse error."""
    respx.post(f"{_PRIVATE}/BalanceEx").mock(return_value=response)
    with pytest.raises(ExchangeError, match=match):
        read_only.get_balance(currency=Currency.BTC)


@respx.mock
def test_add_order_without_a_txid_is_an_error(trading: KrakenSpotAdapter) -> None:
    """An AddOrder reply without exactly one txid cannot be tracked, so it raises."""
    respx.post(f"{_PRIVATE}/AddOrder").mock(
        return_value=httpx.Response(200, json={"error": [], "result": {"descr": {}}})
    )
    with pytest.raises(ExchangeError, match="no single txid"):
        trading.place_market_order(side=OrderSide.BUY, order=_order())


@respx.mock
def test_transport_failure_is_a_network_error(read_only: KrakenSpotAdapter) -> None:
    """A dropped connection is a NetworkError naming the path, not the signed body."""
    respx.post(f"{_PRIVATE}/BalanceEx").mock(side_effect=httpx.ConnectError("reset"))
    with pytest.raises(NetworkError, match="/0/private/BalanceEx"):
        read_only.get_balance(currency=Currency.BTC)


def test_sign_refuses_a_secret_that_is_not_base64() -> None:
    """Signing with a malformed secret fails loudly instead of signing with garbage."""
    with pytest.raises(ValueError, match="base64"):
        sign(path="/0/private/Balance", nonce="1", post_data="nonce=1", secret="%%%")  # noqa: S106


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"stop_limit_buffer": Decimal("0.2")}, "stop_limit_buffer"),
        ({"settle_checks": 0}, "settle_checks"),
    ],
    ids=["buffer", "settle-checks"],
)
def test_out_of_range_tuning_is_refused(overrides: dict[str, Any], match: str) -> None:
    """A stop buffer of 10% or more, or zero read-backs, is refused at construction."""
    with pytest.raises(ValueError, match=match):
        KrakenSpotConfig(api_key=_KEY, api_secret=_SIGNING_MATERIAL, **overrides)


def test_create_builds_a_read_only_adapter_on_the_kraken_host() -> None:
    """create() owns both clients on api.kraken.com, and close() releases them."""
    adapter = KrakenSpotAdapter.create(KrakenSpotConfig(api_key=_KEY, api_secret=_SIGNING_MATERIAL))
    assert str(adapter.client.base_url) == f"{KRAKEN_BASE_URL}"
    assert adapter.config.mainnet_permit is None
    adapter.close()
    assert adapter.client.is_closed
    assert adapter.public.client.is_closed


@respx.mock
def test_query_without_the_order_is_an_error(trading: KrakenSpotAdapter) -> None:
    """A QueryOrders reply that does not mention the txid cannot be settled."""
    respx.post(f"{_PRIVATE}/AddOrder").mock(return_value=_ok("add_order_market_buy"))
    respx.post(f"{_PRIVATE}/QueryOrders").mock(
        return_value=httpx.Response(200, json={"error": [], "result": {}})
    )
    with pytest.raises(ExchangeError, match=f"no entry for {_MARKET_TXID}"):
        trading.place_market_order(side=OrderSide.BUY, order=_order())


@respx.mock
@pytest.mark.parametrize(
    ("patch", "match"),
    [
        ({"cost": None}, "cost is not numeric"),
        ({"fee": "n/a"}, "fee is not a decimal"),
        ({"vol_exec": "0.02"}, "domain invariants"),
    ],
    ids=["missing-cost", "bad-fee", "overfilled"],
)
def test_malformed_execution_raises_exchange_error(
    trading: KrakenSpotAdapter, patch: dict[str, object], match: str
) -> None:
    """A settled order with unusable numbers, or more volume than ordered, is not a fill."""
    reply = _reply("query_orders_market_closed")
    reply["result"][_MARKET_TXID].update(patch)
    respx.post(f"{_PRIVATE}/AddOrder").mock(return_value=_ok("add_order_market_buy"))
    respx.post(f"{_PRIVATE}/QueryOrders").mock(return_value=httpx.Response(200, json=reply))
    with pytest.raises(ExchangeError, match=match):
        trading.place_market_order(side=OrderSide.BUY, order=_order())


@respx.mock
def test_malformed_balance_entry_raises(read_only: KrakenSpotAdapter) -> None:
    """A balance that is not an object is an ExchangeError, not a crash."""
    respx.post(f"{_PRIVATE}/BalanceEx").mock(
        return_value=httpx.Response(200, json={"error": [], "result": {"XXBT": "1.0"}})
    )
    with pytest.raises(ExchangeError, match="malformed balance for XXBT"):
        read_only.get_balance(currency=Currency.BTC)


# ---------------------------------------------------------------------------
# Never losing track of a real order (#858)
# ---------------------------------------------------------------------------


@respx.mock
def test_every_order_carries_a_userref(trading: KrakenSpotAdapter) -> None:
    """AddOrder sends a positive 32-bit ``userref``: the key to an order whose reply is lost."""
    add = respx.post(f"{_PRIVATE}/AddOrder").mock(return_value=_ok("add_order_market_buy"))
    respx.post(f"{_PRIVATE}/QueryOrders").mock(return_value=_ok("query_orders_market_closed"))

    trading.place_market_order(side=OrderSide.BUY, order=_order())

    userref = int(_form(add.calls.last.request)["userref"])
    assert 0 < userref < 2**31


# SEV-1: no rubric row matches; a placed real order must never be lost without its id
@respx.mock
def test_a_failed_read_back_after_addorder_carries_the_txid(trading: KrakenSpotAdapter) -> None:
    """QueryOrders failing after AddOrder succeeded raises with the txid and the userref.

    Issue #858 item 3: before, the error dropped the txid, so the live order
    could not be traced.
    """
    add = respx.post(f"{_PRIVATE}/AddOrder").mock(return_value=_ok("add_order_market_buy"))
    respx.post(f"{_PRIVATE}/QueryOrders").mock(side_effect=httpx.ReadTimeout("slow"))

    with pytest.raises(OrderUnreadError, match=f"order {_MARKET_TXID}") as caught:
        trading.place_market_order(side=OrderSide.BUY, order=_order())

    sent = int(_form(add.calls.last.request)["userref"])
    assert (caught.value.txid, caught.value.userref) == (_MARKET_TXID, sent)


@respx.mock
def test_a_lost_addorder_reply_names_the_userref(trading: KrakenSpotAdapter) -> None:
    """A reply lost after the request went out may hide a live order: the error names it."""
    add = respx.post(f"{_PRIVATE}/AddOrder").mock(side_effect=httpx.ReadTimeout("slow"))

    with pytest.raises(OrderUnreadError, match="find it by userref") as caught:
        trading.place_market_order(side=OrderSide.BUY, order=_order())

    assert (caught.value.txid, caught.value.userref) == (
        "",
        int(_form(add.calls.last.request)["userref"]),
    )


@respx.mock
def test_an_addorder_that_never_connected_is_a_plain_network_error(
    trading: KrakenSpotAdapter,
) -> None:
    """No connection means no order: a retry-safe NetworkError, not an unread order."""
    respx.post(f"{_PRIVATE}/AddOrder").mock(side_effect=httpx.ConnectError("refused"))

    with pytest.raises(NetworkError) as caught:
        trading.place_market_order(side=OrderSide.BUY, order=_order())

    assert type(caught.value) is NetworkError


def _with_nonce_file(path: Path, clock_ms: int) -> KrakenSpotAdapter:
    adapter = _build(trading=True)
    return dataclasses.replace(
        adapter,
        config=dataclasses.replace(adapter.config, nonce_file=path),
        clock_ms=lambda: clock_ms,
    )


@respx.mock
def test_a_restart_with_the_clock_stepped_back_still_sends_a_higher_nonce(
    tmp_path: Path,
) -> None:
    """Issue #858 item 4: the kept nonce outlives the process, so the next one is above it."""
    route = respx.post(f"{_PRIVATE}/BalanceEx").mock(return_value=_ok("balance_ex"))
    nonce_file = tmp_path / "kraken.nonce"
    before = _with_nonce_file(nonce_file, _NOW_MS)
    before.get_balance(currency=Currency.BTC)
    before.close()

    after = _with_nonce_file(nonce_file, _NOW_MS - 60_000)
    after.get_balance(currency=Currency.BTC)
    after.close()

    first, second = (int(_form(call.request)["nonce"]) for call in route.calls)
    assert (first, second, nonce_file.read_text(encoding="ascii")) == (
        _NOW_MS,
        _NOW_MS + 1,
        str(_NOW_MS + 1),
    )


def test_a_nonce_file_that_holds_no_nonce_is_refused(tmp_path: Path) -> None:
    """A corrupted nonce file stops signing instead of guessing a nonce Kraken may reject."""
    nonce_file = tmp_path / "kraken.nonce"
    nonce_file.write_text("garbage", encoding="ascii")
    adapter = _with_nonce_file(nonce_file, _NOW_MS)

    with pytest.raises(ExchangeError, match="does not hold a nonce"):
        adapter.get_balance(currency=Currency.BTC)
    adapter.close()


@respx.mock
def test_a_bad_nonce_file_fails_an_order_before_sending_it(tmp_path: Path) -> None:
    """A corrupted nonce file stops AddOrder unsent, so it is not reported as a maybe-live order."""
    add = respx.post(f"{_PRIVATE}/AddOrder").mock(return_value=_ok("add_order_market_buy"))
    nonce_file = tmp_path / "kraken.nonce"
    nonce_file.write_text("garbage", encoding="ascii")
    adapter = _with_nonce_file(nonce_file, _NOW_MS)

    with pytest.raises(ExchangeError, match="does not hold a nonce") as caught:
        adapter.place_market_order(side=OrderSide.BUY, order=_order())
    adapter.close()

    assert (type(caught.value), add.call_count) == (ExchangeError, 0)
