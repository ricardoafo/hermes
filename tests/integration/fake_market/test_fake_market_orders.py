"""The real Binance spot adapter trades on the fake market with no real key.

Issue #971, part 3. Invariants protected here:

- a market or IOC order fills from the fill kernel's walk of the depth book
  the fake serves at that instant, and pays the taker rate in the asset
  Binance charges it in;
- an IOC limit the book cannot fill whole executes nothing and moves no
  balance;
- a stop-loss sell holds its base asset until it is cancelled or fills, and
  fills at the stop, at a gapped open above its limit, or at its limit once
  a later bar reaches it;
- a refused order (stop above the market, short balance, bad key, bad
  signature, stale timestamp) moves no balance;
- a lost answer to an order that was placed is recovered by the adapter's
  lookup as the same fill or stop; a lost order that never reached the book
  is reported not placed;
- over the loopback service the signature, the API key and ``DELETE`` reach
  the account unchanged.
"""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from urllib.parse import urlencode

import httpx
import pytest

from hermes.adapters._shared.rate_limiter import RateLimitConfig, RateLimiter
from hermes.adapters._shared.wire import binance_sign
from hermes.adapters.exchanges.binance_spot import (
    TESTNET_BASE_URL,
    BinanceSpotAdapter,
    BinanceSpotConfig,
    OrderNotPlacedError,
)
from hermes.domain.errors import ExchangeError
from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.bar_schedule import close_time_of
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.trading.fill import Fill, Rejection
from hermes.domain.trading.order import Order, OrderSide
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.quantity import Quantity
from hermes.domain.value_objects.strategy_id import StrategyId
from tests.support.fake_market import wire
from tests.support.fake_market.exchange import FakeExchange, SimClock
from tests.support.fake_market.script import H4, Market, MarketScript
from tests.support.fake_market.service import FakeMarketService
from tests.support.fake_market.spot import FAKE_API_KEY, FAKE_SIGNING_MATERIAL, Lost
from tests.support.serve_loop import BTC

pytestmark = pytest.mark.integration

START = datetime(2024, 1, 1, tzinfo=UTC)
DAY_ONE = START + timedelta(days=1)
BEST_ASK = Decimal("100.01")
BEST_BID = Decimal("99.99")
STOP = Decimal(96)
STOP_LIMIT = Decimal("95.04")


def _flat_market() -> Market:
    """Two days at 100: the fake book's best ask is 100.01 and its best bid 99.99."""
    return MarketScript(start=START, prices={BTC: Decimal(100)}).hold(BTC, days=2).build()


def _market_after_day_one(*later: tuple[str, str, str, str]) -> Market:
    """Six flat 4h bars at 100, then ``later`` bars as (open, high, low, close)."""
    rows = [("100", "100.2", "99.8", "100")] * 6 + list(later)
    bars = [
        Bar(
            pair=BTC,
            interval=KlineInterval.H4,
            open_time=START + H4 * i,
            open=Decimal(o),
            high=Decimal(h),
            low=Decimal(lo),
            close=Decimal(c),
            volume=Decimal(10),
            close_time=close_time_of(START + H4 * i, KlineInterval.H4),
        )
        for i, (o, h, lo, c) in enumerate(rows)
    ]
    return Market(start=START, h4={BTC: bars}, d1={BTC: []})


def _exchange(market: Market, at: datetime) -> FakeExchange:
    return FakeExchange(market=market, clock=SimClock(at, market.end))


SLEPT: list[float] = []


def _adapter(
    exchange: FakeExchange,
    *,
    client: httpx.Client | None = None,
    secret: str = FAKE_SIGNING_MATERIAL,
    key: str = FAKE_API_KEY,
    skew: timedelta = timedelta(0),
) -> BinanceSpotAdapter:
    """Return the production adapter on the testnet config, its requests answered by the fake."""
    SLEPT.clear()
    return BinanceSpotAdapter(
        config=BinanceSpotConfig(api_key=key, api_secret=secret),
        client=client or httpx.Client(base_url=TESTNET_BASE_URL, transport=exchange.transport()),
        limiter=RateLimiter(config=RateLimitConfig(max_weight=1000, window_seconds=60)),
        clock_ms=lambda: wire.ms(exchange.clock.now() + skew),
        sleep=SLEPT.append,
    )


@pytest.fixture
def flat() -> Iterator[tuple[FakeExchange, BinanceSpotAdapter]]:
    """Return the flat market at the end of day two, with 10,000 USDT and 0.5 BTC free."""
    exchange = _exchange(_flat_market(), START + timedelta(days=2))
    exchange.spot.free["BTC"] = Decimal("0.5")
    adapter = _adapter(exchange)
    yield exchange, adapter
    adapter.close()


def _order(side: OrderSide, qty: str = "0.01", limit: str | None = None) -> Order:
    return Order(
        side=side,
        pair=BTC,
        qty=Quantity(amount=Decimal(qty), asset=Currency.BTC),
        strategy_id=StrategyId("swing-donchian-4h"),
        limit_price=None if limit is None else Decimal(limit),
    )


def _free(adapter: BinanceSpotAdapter, currency: Currency) -> Decimal:
    return adapter.get_balance(currency=currency).amount


def test_a_market_buy_fills_at_the_best_ask_and_pays_the_taker_rate_in_btc(
    flat: tuple[FakeExchange, BinanceSpotAdapter],
) -> None:
    """A market buy walks the served asks; Binance charges a buy's commission in the base asset."""
    _, adapter = flat

    fill = adapter.place_market_order(side=OrderSide.BUY, order=_order(OrderSide.BUY))

    assert fill == Fill(
        order=_order(OrderSide.BUY),
        executed_qty=Decimal("0.01"),
        avg_price=BEST_ASK,
        fee=Money(amount=Decimal("0.0010001"), currency=Currency.USDT),
        timestamp=START + timedelta(days=2),
    )


def test_a_market_buy_moves_the_balances_by_its_cost_and_commission(
    flat: tuple[FakeExchange, BinanceSpotAdapter],
) -> None:
    """USDT falls by the notional; BTC rises by the quantity less the commission kept in BTC."""
    _, adapter = flat

    adapter.place_market_order(side=OrderSide.BUY, order=_order(OrderSide.BUY))

    assert (_free(adapter, Currency.USDT), _free(adapter, Currency.BTC)) == (
        Decimal("9998.9999"),
        Decimal("0.50999"),
    )


def test_a_market_sell_fills_at_the_best_bid_and_pays_the_taker_rate_in_usdt(
    flat: tuple[FakeExchange, BinanceSpotAdapter],
) -> None:
    """A sell's commission is charged in the quote asset and credited net."""
    _, adapter = flat

    fill = adapter.place_market_order(side=OrderSide.SELL, order=_order(OrderSide.SELL))

    assert (fill.avg_price, fill.fee.amount, _free(adapter, Currency.USDT)) == (
        BEST_BID,
        Decimal("0.0009999"),
        Decimal("10000.9989001"),
    )


def test_an_ioc_limit_walks_every_level_up_to_its_price(
    flat: tuple[FakeExchange, BinanceSpotAdapter],
) -> None:
    """One BTC at a 100.02 limit takes 0.5 at 100.01 and 0.5 at 100.02."""
    _, adapter = flat

    fill = adapter.place_limit_order(order=_order(OrderSide.BUY, qty="1", limit="100.02"))

    assert isinstance(fill, Fill)
    assert fill.avg_price == Decimal("100.015")


def test_an_ioc_limit_under_the_best_ask_expires_and_moves_nothing(
    flat: tuple[FakeExchange, BinanceSpotAdapter],
) -> None:
    """An IOC the book cannot fill whole is a rejection, and the account is untouched."""
    _, adapter = flat

    result = adapter.place_limit_order(order=_order(OrderSide.BUY, limit="100"))

    assert isinstance(result, Rejection)
    assert (result.reason, _free(adapter, Currency.USDT)) == (
        "IOC limit not filled: EXPIRED",
        Decimal(10_000),
    )


def test_a_buy_the_account_cannot_pay_for_is_refused_and_moves_nothing(
    flat: tuple[FakeExchange, BinanceSpotAdapter],
) -> None:
    """200 BTC costs about 20,000 USDT against 10,000 free: Binance's -2010."""
    _, adapter = flat

    with pytest.raises(ExchangeError, match="code -2010"):
        adapter.place_market_order(side=OrderSide.BUY, order=_order(OrderSide.BUY, qty="200"))

    assert _free(adapter, Currency.USDT) == Decimal(10_000)


def _signed_get(exchange: FakeExchange, path: str, params: dict[str, str]) -> object:
    query = urlencode({**params, "timestamp": str(wire.ms(exchange.clock.now()))})
    with httpx.Client(base_url=TESTNET_BASE_URL, transport=exchange.transport()) as client:
        response = client.get(
            f"{path}?{query}&signature={binance_sign(query, FAKE_SIGNING_MATERIAL)}",
            headers={"X-MBX-APIKEY": FAKE_API_KEY},
        )
    return response.json()


def test_a_stop_loss_rests_on_the_book_and_holds_its_btc(
    flat: tuple[FakeExchange, BinanceSpotAdapter],
) -> None:
    """The stop is listed open with its trigger and limit, and its BTC is locked, not free."""
    exchange, adapter = flat

    order_id = adapter.place_stop_loss(order=_order(OrderSide.SELL, qty="0.5"), stop_price=STOP)

    open_orders = _signed_get(exchange, "/api/v3/openOrders", {"symbol": "BTCUSDT"})
    assert isinstance(open_orders, list)
    assert [
        (f"BTCUSDT:{o['orderId']}", o["type"], o["stopPrice"], o["price"]) for o in open_orders
    ] == [(order_id, "STOP_LOSS_LIMIT", "96.00000000", "95.04000000")]
    assert _free(adapter, Currency.BTC) == Decimal(0)


def test_a_cancelled_stop_frees_its_btc_and_a_second_cancel_is_quiet(
    flat: tuple[FakeExchange, BinanceSpotAdapter],
) -> None:
    """Cancel frees the locked BTC; a second cancel is Binance's -2011, which the adapter allows."""
    _, adapter = flat
    order_id = adapter.place_stop_loss(order=_order(OrderSide.SELL, qty="0.5"), stop_price=STOP)

    adapter.cancel_order(order_id=order_id)
    adapter.cancel_order(order_id=order_id)

    assert _free(adapter, Currency.BTC) == Decimal("0.5")


def test_a_stop_above_the_market_is_refused(
    flat: tuple[FakeExchange, BinanceSpotAdapter],
) -> None:
    """A sell stop at or above the last price would trigger at once: Binance refuses it."""
    _, adapter = flat

    with pytest.raises(ExchangeError, match="would trigger immediately"):
        adapter.place_stop_loss(order=_order(OrderSide.SELL, qty="0.5"), stop_price=Decimal(101))

    assert _free(adapter, Currency.BTC) == Decimal("0.5")


def _stopped(*later: tuple[str, str, str, str]) -> tuple[FakeExchange, BinanceSpotAdapter]:
    """Rest a 0.5 BTC stop at 96 after day one, then let the ``later`` bars close."""
    exchange = _exchange(_market_after_day_one(*later), DAY_ONE)
    exchange.spot.free["BTC"] = Decimal("0.5")
    adapter = _adapter(exchange)
    adapter.place_stop_loss(order=_order(OrderSide.SELL, qty="0.5"), stop_price=STOP)
    exchange.clock.current = DAY_ONE + H4 * len(later)
    return exchange, adapter


def _sold_at(exchange: FakeExchange) -> list[str]:
    """Return the prices ``GET /api/v3/myTrades`` lists, as Binance prints them."""
    trades = _signed_get(exchange, "/api/v3/myTrades", {"symbol": "BTCUSDT"})
    assert isinstance(trades, list)
    return [str(t["price"]) for t in trades]


def test_a_stop_fills_at_its_trigger_when_a_closed_bar_reaches_it() -> None:
    """A bar that trades down through 96 from above sells the 0.5 BTC at 96, net of commission."""
    exchange, adapter = _stopped(("100", "100.5", "99", "99.5"), ("99.5", "99.6", "94", "95"))

    assert (_free(adapter, Currency.USDT), _sold_at(exchange)) == (
        Decimal("10047.952"),
        ["96.00000000"],
    )


def test_a_stop_gapped_through_fills_at_the_open_when_that_is_above_its_limit() -> None:
    """A bar opening at 95.50, under the stop and over the 95.04 limit, sells at 95.50."""
    exchange, _ = _stopped(("95.5", "95.8", "95.2", "95.6"))

    assert _sold_at(exchange) == ["95.50000000"]


def test_a_stop_gapped_under_its_limit_keeps_working_until_a_bar_reaches_the_limit() -> None:
    """Opening at 93 leaves the 95.04 limit out of reach; the next bar up to 95.5 fills it there."""
    exchange, _ = _stopped(("93", "93.5", "92", "93"), ("93", "95.5", "92.5", "95"))

    assert _sold_at(exchange) == ["95.04000000"]


def test_a_stop_still_working_after_a_gap_holds_its_btc() -> None:
    """While its limit is out of reach the stop stays open and nothing is sold."""
    exchange, adapter = _stopped(("93", "93.5", "92", "93"))

    assert (_free(adapter, Currency.BTC), _sold_at(exchange)) == (Decimal(0), [])


def test_a_lost_answer_to_a_placed_market_order_is_recovered_as_the_same_fill(
    flat: tuple[FakeExchange, BinanceSpotAdapter],
) -> None:
    """A 503 after placing: the lookup by client id returns the fill, and one order exists."""
    exchange, adapter = flat
    exchange.spot.lose_next_order(Lost.PLACED)

    fill = adapter.place_market_order(side=OrderSide.BUY, order=_order(OrderSide.BUY))

    assert (fill.avg_price, fill.fee.amount, len(exchange.spot.orders)) == (
        BEST_ASK,
        Decimal("0.0010001"),
        1,
    )


def test_a_lost_answer_to_a_placed_stop_is_recovered_as_the_resting_stop(
    flat: tuple[FakeExchange, BinanceSpotAdapter],
) -> None:
    """The stop's lookup finds it NEW, so the adapter returns the id of the order on the book."""
    exchange, adapter = flat
    exchange.spot.lose_next_order(Lost.PLACED)

    order_id = adapter.place_stop_loss(order=_order(OrderSide.SELL, qty="0.5"), stop_price=STOP)

    assert [f"BTCUSDT:{i}" for i in exchange.spot.orders] == [order_id]


def test_an_order_lost_before_the_book_is_reported_not_placed(
    flat: tuple[FakeExchange, BinanceSpotAdapter],
) -> None:
    """Two lookups a wait apart find nothing: safe to retry, and the account is untouched."""
    exchange, adapter = flat
    exchange.spot.lose_next_order(Lost.DROPPED)

    with pytest.raises(OrderNotPlacedError):
        adapter.place_market_order(side=OrderSide.BUY, order=_order(OrderSide.BUY))

    assert (exchange.spot.orders, len(SLEPT), _free(adapter, Currency.USDT)) == (
        {},
        1,
        Decimal(10_000),
    )


@pytest.mark.parametrize(
    ("secret", "key", "skew", "code"),
    [
        ("another-secret-not-real", FAKE_API_KEY, timedelta(0), "-1022"),  # gitleaks:allow
        (FAKE_SIGNING_MATERIAL, "another-key-not-real", timedelta(0), "-2015"),  # gitleaks:allow
        (FAKE_SIGNING_MATERIAL, FAKE_API_KEY, timedelta(seconds=-10), "-1021"),
    ],
)
def test_a_request_binance_would_not_authenticate_is_refused(
    secret: str, key: str, skew: timedelta, code: str
) -> None:
    """A wrong secret, a wrong key, or a timestamp outside recvWindow never reaches the account."""
    exchange = _exchange(_flat_market(), START + timedelta(days=2))
    adapter = _adapter(exchange, secret=secret, key=key, skew=skew)

    with pytest.raises(ExchangeError, match=f"code {code}"):
        adapter.place_market_order(side=OrderSide.BUY, order=_order(OrderSide.BUY))

    assert exchange.spot.orders == {}


@pytest.fixture
def served() -> Iterator[tuple[FakeMarketService, BinanceSpotAdapter]]:
    """Serve the flat market on a socket, with an adapter whose client points at it."""
    exchange = _exchange(_flat_market(), START + timedelta(days=2))
    exchange.spot.free["BTC"] = Decimal("0.5")
    with FakeMarketService(exchange) as service:
        adapter = _adapter(exchange, client=httpx.Client(base_url=service.binance_spot_base))
        yield service, adapter
        adapter.close()


def test_the_service_passes_a_signed_order_through_unchanged(
    served: tuple[FakeMarketService, BinanceSpotAdapter],
) -> None:
    """Over loopback the signature still matches, so the order fills as in process."""
    _, adapter = served

    fill = adapter.place_market_order(side=OrderSide.BUY, order=_order(OrderSide.BUY))

    assert fill.avg_price == BEST_ASK


def test_the_service_serves_a_cancel(
    served: tuple[FakeMarketService, BinanceSpotAdapter],
) -> None:
    """``DELETE /api/v3/order`` reaches the account and frees the stop's BTC."""
    _, adapter = served
    order_id = adapter.place_stop_loss(order=_order(OrderSide.SELL, qty="0.5"), stop_price=STOP)

    adapter.cancel_order(order_id=order_id)

    assert _free(adapter, Currency.BTC) == Decimal("0.5")


def test_the_control_api_loses_the_next_order_answer(
    served: tuple[FakeMarketService, BinanceSpotAdapter],
) -> None:
    """``order_lost`` from the control API is the 503 the adapter recovers by lookup."""
    service, adapter = served
    httpx.post(
        f"{service.url}/_fake/faults", json={"kind": "order_lost", "how": "placed"}, timeout=5
    ).raise_for_status()

    fill = adapter.place_market_order(side=OrderSide.BUY, order=_order(OrderSide.BUY))

    assert (fill.avg_price, len(service.exchange.spot.orders)) == (BEST_ASK, 1)
