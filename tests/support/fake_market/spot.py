"""Binance spot order endpoints on the fake market, so the real order adapter runs with no key.

Issue #971, part 3. :class:`SpotAccount` answers the signed endpoints
:class:`~hermes.adapters.exchanges.binance_spot.BinanceSpotAdapter` calls on
``testnet.binance.vision``, in the shapes of Binance's spot REST documentation:

- ``POST /api/v3/order``: ``MARKET``, ``LIMIT`` with ``IOC`` and
  ``STOP_LOSS_LIMIT`` with ``GTC``; ``ACK`` or ``FULL`` replies;
- ``GET`` and ``DELETE /api/v3/order``, ``GET /api/v3/openOrders``,
  ``GET /api/v3/myTrades`` and ``GET /api/v3/account``.

Fills come from the fill kernel the backtests use
(:class:`~hermes.adapters.fills.orderbook_walk.OrderbookWalkFillModel`)
against the depth book the fake serves at that moment, so a fill is all or
nothing: an IOC limit the book cannot fill whole ends ``EXPIRED`` with
nothing executed, where Binance could fill part of it. Every fill pays the
taker rate, in the base asset on a buy and the quote asset on a sell.

A resting stop-loss-limit sell triggers on the first closed 4h bar, opened
after it was placed, whose low reaches the stop. It fills at the stop, or at
that bar's open when the bar gapped below it, but never under its limit
price: when the limit is out of reach it keeps working until a later bar's
high reaches the limit, and then fills at the limit.

Requests are checked as Binance checks them: the API key header, the HMAC
signature over the exact query string, and the timestamp against the
scripted clock and ``recvWindow``.
"""

import hashlib
import hmac
import itertools
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Final, Protocol

import httpx

from hermes.adapters.fills.orderbook_walk import OrderbookWalkFillModel
from hermes.domain.fees.fee_schedule import FeeSchedule, FeeTier
from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.orderbook import Orderbook, OrderbookLevel
from hermes.domain.trading.fill import Rejection
from hermes.domain.trading.order import Order, OrderSide
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.quantity import Quantity
from hermes.domain.value_objects.strategy_id import StrategyId
from tests.support.fake_market import wire

SPOT_HOST: Final = "testnet.binance.vision"
SPOT_PATHS: Final = frozenset(
    {"/api/v3/order", "/api/v3/openOrders", "/api/v3/myTrades", "/api/v3/account"}
)
FAKE_API_KEY: Final = "fake-market-key-not-real"  # gitleaks:allow
FAKE_SIGNING_MATERIAL: Final = "fake-market-secret-not-real"  # gitleaks:allow
_BOOK_LEVELS: Final = 100
_FIRST_ORDER_ID: Final = 1000
_TAKER_RATE: Final = Decimal("0.001")
_BPS: Final = Decimal(10_000)
_FILL_OWNER: Final = StrategyId("fake-market")


class Lost(StrEnum):
    """How the next order submission is lost: Binance answers 503 either way."""

    PLACED = "placed"
    """The order reached the book; only the answer was lost."""
    DROPPED = "dropped"
    """The order never reached the book."""


class Venue(Protocol):
    """What the account reads from the fake market it trades on."""

    book_step_bps: Decimal

    def now(self) -> datetime:
        """Return the scripted instant."""
        ...

    def pair_for(self, symbol: str) -> Pair | None:
        """Return the scripted pair Binance names ``symbol``, if any."""
        ...

    def last_close(self, pair: Pair) -> Decimal | None:
        """Return the close of the latest closed 4h bar, if any has closed."""
        ...

    def closed_h4(self, pair: Pair, opened_from: datetime) -> list[Bar]:
        """Return the closed 4h bars opened at or after ``opened_from``, oldest first."""
        ...


@dataclass
class SpotOrder:
    """One order as the fake matching engine holds it."""

    order_id: int
    client_id: str
    pair: Pair
    side: OrderSide
    kind: str
    time_in_force: str | None
    qty: Decimal
    price: Decimal | None
    stop_price: Decimal | None
    placed_at: datetime
    status: str = "NEW"
    executed: Decimal = Decimal(0)
    quote: Decimal = Decimal(0)
    updated_at: datetime | None = None

    @property
    def symbol(self) -> str:
        """Return the Binance symbol."""
        return f"{self.pair.base.value}{self.pair.quote.value}"


@dataclass
class SpotAccount:
    """A Binance spot account the fake market matches orders for.

    Attributes:
        api_key: The key every signed request must carry in ``X-MBX-APIKEY``.
        api_secret: The secret requests are signed with.
        free: Free balance per asset.
        locked: Balance held by resting orders, per asset.
        orders: Every order accepted, by order id.
        trades: Every trade, as ``GET /api/v3/myTrades`` rows.
        lost: How each of the next order submissions is lost, oldest first.

    """

    api_key: str = FAKE_API_KEY
    api_secret: str = field(default=FAKE_SIGNING_MATERIAL, repr=False)
    free: dict[str, Decimal] = field(default_factory=lambda: {"USDT": Decimal(10_000)})
    locked: dict[str, Decimal] = field(default_factory=dict)
    orders: dict[int, SpotOrder] = field(default_factory=dict)
    trades: list[dict[str, object]] = field(default_factory=list)
    lost: list[Lost] = field(default_factory=list)
    _ids: itertools.count[int] = field(default_factory=lambda: itertools.count(_FIRST_ORDER_ID))

    def lose_next_order(self, how: Lost) -> None:
        """Answer the next order submission with HTTP 503, after placing it or not."""
        self.lost.append(how)

    def handle(self, request: httpx.Request, venue: Venue) -> httpx.Response:
        """Answer one signed request at ``venue``'s scripted instant."""
        query = request.url.query.decode()
        refused = self._authenticate(request, query, venue.now())
        if refused is not None:
            return refused
        params = {k: v for k, v in httpx.QueryParams(query).items() if k != "signature"}
        self._settle_stops(venue)
        routes: dict[tuple[str, str], Callable[[dict[str, str], Venue], httpx.Response]] = {
            ("POST", "/api/v3/order"): self._new_order,
            ("GET", "/api/v3/order"): self._query_order,
            ("DELETE", "/api/v3/order"): self._cancel_order,
            ("GET", "/api/v3/openOrders"): self._open_orders,
            ("GET", "/api/v3/myTrades"): self._my_trades,
            ("GET", "/api/v3/account"): self._account,
        }
        route = routes.get((request.method, request.url.path))
        if route is None:
            return _error(404, -1, f"unknown path {request.method} {request.url.path}")
        try:
            return route(params, venue)
        except _RefusedError as exc:
            return _error(400, exc.code, str(exc))

    # -- checks ------------------------------------------------------------

    def _authenticate(
        self, request: httpx.Request, query: str, now: datetime
    ) -> httpx.Response | None:
        if request.headers.get("X-MBX-APIKEY") != self.api_key:
            return _error(401, -2015, "Invalid API-key, IP, or permissions for action.")
        payload, sep, signature = query.rpartition("&signature=")
        expected = hmac.new(self.api_secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not sep or not hmac.compare_digest(signature, expected):
            return _error(400, -1022, "Signature for this request is not valid.")
        params = httpx.QueryParams(payload)
        try:
            sent = datetime.fromtimestamp(int(params["timestamp"]) / 1000, tz=UTC)
            window = timedelta(milliseconds=int(params.get("recvWindow", "5000")))
        except KeyError, ValueError:
            return _error(400, -1102, "Mandatory parameter 'timestamp' was not sent.")
        if sent > now + timedelta(seconds=1) or now - sent > window:
            return _error(400, -1021, "Timestamp for this request is outside of the recvWindow.")
        return None

    # -- orders ------------------------------------------------------------

    def _new_order(self, params: dict[str, str], venue: Venue) -> httpx.Response:
        order = self._order_from(params, venue)
        lost = self.lost.pop(0) if self.lost else None
        if lost is Lost.DROPPED:
            return _unavailable()
        if order.kind == "STOP_LOSS_LIMIT":
            self._rest_stop(order, venue)
        else:
            self._match(order, venue)
        order.updated_at = venue.now()
        self.orders[order.order_id] = order
        if lost is Lost.PLACED:
            return _unavailable()
        if params.get("newOrderRespType", "FULL") == "ACK":
            return _json(_ack(order))
        fills = [t for t in self.trades if t["orderId"] == order.order_id]
        return _json(
            {
                **_ack(order),
                **_shape(order),
                "fills": [
                    {k: t[k] for k in ("price", "qty", "commission", "commissionAsset")}
                    | {"tradeId": t["id"]}
                    for t in fills
                ],
            }
        )

    def _order_from(self, params: dict[str, str], venue: Venue) -> SpotOrder:
        """Return the order ``params`` describe, not yet matched or held."""
        order_id = next(self._ids)
        order = SpotOrder(
            order_id=order_id,
            client_id=params.get("newClientOrderId") or f"fake-{order_id}",
            pair=_pair(params, venue),
            side=_side(params),
            kind=_required(params, "type"),
            time_in_force=params.get("timeInForce"),
            qty=_positive(params, "quantity"),
            price=None,
            stop_price=None,
            placed_at=venue.now(),
        )
        if any(o.client_id == order.client_id and o.status == "NEW" for o in self.orders.values()):
            raise _RefusedError(-2010, "Duplicate order sent.")
        match order.kind, order.time_in_force:
            case "MARKET", None:
                pass
            case "LIMIT", "IOC":
                order.price = _positive(params, "price")
            case "STOP_LOSS_LIMIT", "GTC" if order.side is OrderSide.SELL:
                order.price = _positive(params, "price")
                order.stop_price = _positive(params, "stopPrice")
            case _:
                raise _RefusedError(
                    -1116,
                    f"The fake market does not serve {order.side} {order.kind} "
                    f"with timeInForce {order.time_in_force}.",
                )
        return order

    def _match(self, order: SpotOrder, venue: Venue) -> None:
        """Fill ``order`` against the book now, whole or not at all."""
        mid = venue.last_close(order.pair)
        if mid is None:
            raise _RefusedError(-1013, "Market is closed.")
        result = OrderbookWalkFillModel().fill_order(
            Order(
                side=order.side,
                pair=order.pair,
                qty=Quantity(amount=order.qty, asset=order.pair.base),
                strategy_id=_FILL_OWNER,
                limit_price=order.price,
            ),
            _book(order.pair, mid, venue),
            venue.now(),
            _taker_schedule(order.pair),
        )
        if isinstance(result, Rejection):
            order.status = "EXPIRED"
            return
        if order.side is OrderSide.BUY:
            self._require(order.pair.quote.value, result.executed_qty * result.avg_price)
        else:
            self._require(order.pair.base.value, order.qty)
        self._trade(order, result.executed_qty, result.avg_price, venue.now())

    def _rest_stop(self, order: SpotOrder, venue: Venue) -> None:
        last = venue.last_close(order.pair)
        if last is None or order.stop_price is None or order.stop_price >= last:
            raise _RefusedError(-2010, "Stop price would trigger immediately.")
        base = order.pair.base.value
        self._require(base, order.qty)
        self.free[base] -= order.qty
        self.locked[base] = self.locked.get(base, Decimal(0)) + order.qty

    def _require(self, asset: str, amount: Decimal) -> None:
        if self.free.get(asset, Decimal(0)) < amount:
            raise _RefusedError(-2010, "Account has insufficient balance for requested action.")

    def _settle_stops(self, venue: Venue) -> None:
        """Fill every resting stop that a closed bar has reached since it was placed."""
        for order in self.orders.values():
            if order.kind != "STOP_LOSS_LIMIT" or order.status != "NEW":
                continue
            if order.stop_price is None or order.price is None:
                continue
            triggered = False
            for bar in venue.closed_h4(order.pair, order.placed_at):
                if not triggered and bar.low <= order.stop_price:
                    triggered = True
                    price = min(order.stop_price, bar.open)
                    if price < order.price and bar.high < order.price:
                        continue
                    self._fill_stop(order, max(price, order.price), bar.close_time)
                    break
                if triggered and bar.high >= order.price:
                    self._fill_stop(order, order.price, bar.close_time)
                    break

    def _fill_stop(self, order: SpotOrder, price: Decimal, at: datetime) -> None:
        base = order.pair.base.value
        self.locked[base] -= order.qty
        self.free[base] = self.free.get(base, Decimal(0)) + order.qty
        self._trade(order, order.qty, price, at)
        order.updated_at = at

    def _trade(self, order: SpotOrder, qty: Decimal, price: Decimal, at: datetime) -> None:
        base, quote = order.pair.base.value, order.pair.quote.value
        notional = qty * price
        if order.side is OrderSide.BUY:
            commission, asset = qty * _TAKER_RATE, base
            self.free[quote] = self.free.get(quote, Decimal(0)) - notional
            self.free[base] = self.free.get(base, Decimal(0)) + qty - commission
        else:
            commission, asset = notional * _TAKER_RATE, quote
            self.free[base] = self.free.get(base, Decimal(0)) - qty
            self.free[quote] = self.free.get(quote, Decimal(0)) + notional - commission
        order.executed, order.quote, order.status = qty, notional, "FILLED"
        self.trades.append(
            {
                "symbol": order.symbol,
                "id": len(self.trades) + 1,
                "orderId": order.order_id,
                "orderListId": -1,
                "price": wire.decimal_text(price),
                "qty": wire.decimal_text(qty),
                "quoteQty": wire.decimal_text(notional),
                "commission": wire.decimal_text(commission),
                "commissionAsset": asset,
                "time": wire.ms(at),
                "isBuyer": order.side is OrderSide.BUY,
                "isMaker": False,
                "isBestMatch": True,
            }
        )

    def _find(self, params: dict[str, str], venue: Venue) -> SpotOrder | None:
        pair = _pair(params, venue)
        if "orderId" in params:
            found = self.orders.get(_integer(params, "orderId"))
        elif "origClientOrderId" in params:
            found = next(
                (o for o in self.orders.values() if o.client_id == params["origClientOrderId"]),
                None,
            )
        else:
            raise _RefusedError(-1102, "Param 'origClientOrderId' or 'orderId' must be sent.")
        return found if found is not None and found.pair == pair else None

    def _query_order(self, params: dict[str, str], venue: Venue) -> httpx.Response:
        order = self._find(params, venue)
        if order is None:
            return _error(400, -2013, "Order does not exist.")
        return _json(_shape(order))

    def _cancel_order(self, params: dict[str, str], venue: Venue) -> httpx.Response:
        order = self._find(params, venue)
        if order is None or order.status != "NEW":
            return _error(400, -2011, "Unknown order sent.")
        if order.kind == "STOP_LOSS_LIMIT":
            base = order.pair.base.value
            self.locked[base] -= order.qty
            self.free[base] = self.free.get(base, Decimal(0)) + order.qty
        order.status, order.updated_at = "CANCELED", venue.now()
        return _json({**_shape(order), "origClientOrderId": order.client_id})

    def _open_orders(self, params: dict[str, str], venue: Venue) -> httpx.Response:
        pair = _pair(params, venue) if "symbol" in params else None
        return _json(
            [
                _shape(o)
                for o in self.orders.values()
                if o.status == "NEW" and (pair is None or o.pair == pair)
            ]
        )

    def _my_trades(self, params: dict[str, str], venue: Venue) -> httpx.Response:
        symbol = _pair(params, venue)
        order_id = _integer(params, "orderId") if "orderId" in params else None
        return _json(
            [
                t
                for t in self.trades
                if t["symbol"] == f"{symbol.base.value}{symbol.quote.value}"
                and (order_id is None or t["orderId"] == order_id)
            ]
        )

    def _account(self, _params: dict[str, str], venue: Venue) -> httpx.Response:
        assets = sorted(self.free.keys() | self.locked.keys())
        rate = wire.decimal_text(_TAKER_RATE)
        return _json(
            {
                "makerCommission": 10,
                "takerCommission": 10,
                "buyerCommission": 0,
                "sellerCommission": 0,
                "commissionRates": {
                    "maker": rate,
                    "taker": rate,
                    "buyer": "0.00000000",
                    "seller": "0.00000000",
                },
                "canTrade": True,
                "canWithdraw": False,
                "canDeposit": False,
                "brokered": False,
                "requireSelfTradePrevention": False,
                "preventSor": False,
                "updateTime": wire.ms(venue.now()),
                "accountType": "SPOT",
                "balances": [
                    {
                        "asset": a,
                        "free": wire.decimal_text(self.free.get(a, Decimal(0))),
                        "locked": wire.decimal_text(self.locked.get(a, Decimal(0))),
                    }
                    for a in assets
                ],
                "permissions": ["SPOT"],
                "uid": 100000001,
            }
        )


class _RefusedError(ValueError):
    """A request Binance answers with HTTP 400 and an error code."""

    def __init__(self, code: int, msg: str) -> None:
        super().__init__(msg)
        self.code = code


def _required(params: Mapping[str, str], name: str) -> str:
    value = params.get(name)
    if not value:
        raise _RefusedError(-1102, f"Mandatory parameter '{name}' was not sent.")
    return value


def _positive(params: Mapping[str, str], name: str) -> Decimal:
    try:
        value = Decimal(_required(params, name))
    except InvalidOperation as exc:
        raise _RefusedError(-1100, f"Illegal characters found in parameter '{name}'.") from exc
    if not value.is_finite() or value <= 0:
        raise _RefusedError(-1013, f"Invalid {name}.")
    return value


def _integer(params: Mapping[str, str], name: str) -> int:
    raw = _required(params, name)
    if not raw.isdigit():
        raise _RefusedError(-1100, f"Illegal characters found in parameter '{name}'.")
    return int(raw)


def _pair(params: Mapping[str, str], venue: Venue) -> Pair:
    pair = venue.pair_for(_required(params, "symbol"))
    if pair is None:
        raise _RefusedError(-1121, "Invalid symbol.")
    return pair


def _side(params: Mapping[str, str]) -> OrderSide:
    try:
        return OrderSide(_required(params, "side"))
    except ValueError as exc:
        raise _RefusedError(-1100, "Illegal characters found in parameter 'side'.") from exc


def _book(pair: Pair, mid: Decimal, venue: Venue) -> Orderbook:
    depth = wire.depth(mid, levels=_BOOK_LEVELS, update_id=0, step_bps=venue.book_step_bps)

    def levels(side: object) -> tuple[OrderbookLevel, ...]:
        if not isinstance(side, list):
            raise TypeError(f"depth side is not a list: {side!r}")
        return tuple(OrderbookLevel(price=Decimal(p), quantity=Decimal(q)) for p, q in side)

    return Orderbook(
        pair=pair, bids=levels(depth["bids"]), asks=levels(depth["asks"]), snapshot_time=venue.now()
    )


def _taker_schedule(pair: Pair) -> FeeSchedule:
    bps = int(_TAKER_RATE * _BPS)
    return FeeSchedule(
        pair=pair,
        tiers=(FeeTier(volume_threshold_usd=Decimal(0), maker_bps=bps, taker_bps=bps),),
    )


def _ack(order: SpotOrder) -> dict[str, object]:
    return {
        "symbol": order.symbol,
        "orderId": order.order_id,
        "orderListId": -1,
        "clientOrderId": order.client_id,
        "transactTime": wire.ms(order.updated_at or order.placed_at),
    }


def _shape(order: SpotOrder) -> dict[str, object]:
    """Return ``order`` the way ``GET /api/v3/order`` prints it."""
    return {
        "symbol": order.symbol,
        "orderId": order.order_id,
        "orderListId": -1,
        "clientOrderId": order.client_id,
        "price": wire.decimal_text(order.price or Decimal(0)),
        "origQty": wire.decimal_text(order.qty),
        "executedQty": wire.decimal_text(order.executed),
        "cummulativeQuoteQty": wire.decimal_text(order.quote),
        "status": order.status,
        "timeInForce": order.time_in_force or "GTC",
        "type": order.kind,
        "side": order.side.value,
        "stopPrice": wire.decimal_text(order.stop_price or Decimal(0)),
        "time": wire.ms(order.placed_at),
        "updateTime": wire.ms(order.updated_at or order.placed_at),
        "isWorking": order.kind != "STOP_LOSS_LIMIT",
        "origQuoteOrderQty": "0.00000000",
    }


def _json(body: object, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=body)


def _error(status: int, code: int, msg: str) -> httpx.Response:
    return _json({"code": code, "msg": msg}, status=status)


def _unavailable() -> httpx.Response:
    return httpx.Response(503, text="Service Unavailable")
