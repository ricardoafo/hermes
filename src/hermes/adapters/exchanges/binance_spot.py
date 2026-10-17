"""Binance spot trading adapter — testnet by default, mainnet only with a permit.

Implements :class:`~hermes.ports.exchange.ExchangePort` and
:class:`~hermes.ports.exchange.StopLossPort` with signed REST calls:

- ``POST /api/v3/order`` for market, IOC limit and stop-loss-limit orders;
- ``DELETE /api/v3/order`` to cancel (idempotent: an order that is already
  gone is not an error);
- ``GET /api/v3/depth`` for the order book, ``GET /api/v3/account`` for
  balances and commission rates.

Safety (ADR-003 §00 I-08, AGENTS.md rule 7):

- The default host is ``testnet.binance.vision``. Any other host needs a
  :class:`~hermes.infrastructure.mainnet_permit.MainnetPermit`, which is
  issued only in the prod tier with ``HermesConfig.allow_mainnet`` set and
  the strategy LIVE_APPROVED (#382, #781), and must be one of Binance's
  documented spot hosts. ``hermes serve`` stays shadow and wires no exchange.
- Requests are signed with HMAC-SHA256 over the exact query string, as
  Binance documents. The secret never leaves this module and is excluded
  from ``repr``.
- Every request takes its weight from a token bucket sized below Binance's
  limits. A 429 or 418 is not retried: the limiter is misconfigured, and
  hammering the exchange would earn an IP ban.

Order IDs returned by :meth:`BinanceSpotAdapter.place_stop_loss` are
``SYMBOL:ORDERID``, because Binance cancels by symbol and order id.

Unknown execution status (#858): Binance documents a 5xx on an order as
"execution status unknown", and a timeout leaves the same doubt. Every order
therefore carries a ``newClientOrderId``, and such a failure is answered by a
lookup on that id instead of a refusal: an order Binance never saw is an
:class:`OrderNotPlacedError` (safe to retry), a placed one returns its fill,
and a lookup that fails too is an :class:`OrderStatusUnknownError` naming the
client order id for the operator to reconcile. A commission charged in a third
asset (BNB) is priced at that asset's ticker, or at the pre-registered taker
rate when the ticker cannot be read, so a filled order always returns its fill.

Reference: issue #148; openspec/changes/148-binance-testnet-spot/change.md.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Final
from urllib.parse import urlencode, urlsplit

import httpx
import structlog

from hermes.adapters.exchanges.rate_limiter import RateLimitConfig, RateLimiter
from hermes.domain.errors import ExchangeError, NetworkError
from hermes.domain.fees import FeeSchedule, FeeTier
from hermes.domain.market_data.orderbook import Orderbook, OrderbookLevel
from hermes.domain.trading.fill import Fill, Rejection
from hermes.domain.trading.order import Order, OrderSide
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.quantity import Quantity
from hermes.infrastructure.mainnet_permit import MainnetPermit

__all__ = [
    "TESTNET_BASE_URL",
    "BinanceSpotAdapter",
    "BinanceSpotConfig",
    "OrderNotPlacedError",
    "OrderStatusUnknownError",
    "sign",
]

_log = structlog.get_logger(__name__)

TESTNET_BASE_URL: Final = "https://testnet.binance.vision"
_TESTNET_HOST: Final = "testnet.binance.vision"
# Binance's documented spot REST hosts. With a permit only these are accepted,
# so a typo or an injected base_url cannot send the key and signed orders
# off-exchange (#781).
_MAINNET_HOSTS: Final = frozenset(
    {
        "api.binance.com",
        "api-gcp.binance.com",
        "api1.binance.com",
        "api2.binance.com",
        "api3.binance.com",
        "api4.binance.com",
    }
)
_API_KEY_HEADER: Final = "X-MBX-APIKEY"
_UNKNOWN_ORDER: Final = -2011
"""Binance error code for cancelling an order that no longer exists."""
_NO_SUCH_ORDER: Final = -2013
"""Binance error code for querying an order that does not exist."""
_RATE_LIMITED: Final = frozenset({418, 429})
_SERVER_ERROR_MIN: Final = 500
_WEIGHTS: Final[dict[str, int]] = {
    "order": 1,
    "cancel": 1,
    "depth": 5,
    "account": 20,
    "query": 4,
    "trades": 20,
    "ticker": 2,
}
_CLIENT_ID_PREFIX: Final = "hermes-"
_FALLBACK_TAKER_RATE: Final = Decimal("0.001")
"""The pre-registered 10 bps taker fee, used when a third-asset commission cannot be priced."""
_DEPTH_LIMIT: Final = 100
_BPS: Final = Decimal("10000")
_ERROR_EXCERPT: Final = 200
_HTTP_ERROR_MIN: Final = 400


def sign(query: str, secret: str) -> str:
    """Return Binance's HMAC-SHA256 signature of ``query`` as lower-case hex.

    Args:
        query: The exact URL-encoded parameter string that will be sent.
        secret: The API secret.
    """
    return hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()


def _symbol(pair: Pair) -> str:
    return f"{pair.base.value}{pair.quote.value}"


def _decimal(raw: object, field_name: str) -> Decimal:
    if isinstance(raw, bool) or not isinstance(raw, (str, int, float)):
        raise ExchangeError(msg=f"{field_name} is not numeric: {raw!r}")
    try:
        value = Decimal(str(raw))
    except InvalidOperation as exc:
        raise ExchangeError(msg=f"{field_name} is not a decimal: {raw!r}") from exc
    if not value.is_finite():
        raise ExchangeError(msg=f"{field_name} is not a finite number: {raw!r}")
    return value


@dataclass(frozen=True, slots=True)
class BinanceSpotConfig:
    """Wire settings for :class:`BinanceSpotAdapter`.

    Attributes:
        api_key: Binance API key (sent as a header, not secret by itself).
        api_secret: Binance API secret, used only to sign. Not in ``repr``.
        base_url: REST root. Testnet unless a ``mainnet_permit`` is given,
            and then only one of Binance's documented spot hosts.
        mainnet_permit: Lets the config name a mainnet host. Only
            :func:`~hermes.infrastructure.mainnet_permit.issue_mainnet_permit`
            makes one, after the tier, kill-switch and live-approval checks.
        recv_window_ms: Server-side tolerance for clock skew.
        timeout_sec: Per-request timeout.
        rate_limit: Token-bucket sizing, below Binance's 6000 weight a minute.
        stop_limit_buffer: How far below the stop trigger a stop-loss-limit
            sell may fill, as a fraction (0.01 = 1%). A safety net for an
            outage, not a strategy parameter.
    """

    api_key: str
    api_secret: str = field(repr=False)
    base_url: str = TESTNET_BASE_URL
    mainnet_permit: MainnetPermit | None = None
    recv_window_ms: int = 5000
    timeout_sec: float = 10.0
    rate_limit: RateLimitConfig = field(
        default_factory=lambda: RateLimitConfig(max_weight=600, window_seconds=60)
    )
    stop_limit_buffer: Decimal = Decimal("0.01")

    def __post_init__(self) -> None:
        """Refuse a non-HTTPS URL, a mainnet host without a permit or off the list, or empty keys.

        Raises:
            ValueError: On any of those.
        """
        parts = urlsplit(self.base_url)
        if parts.scheme != "https":
            raise ValueError(f"base_url must be https, got {self.base_url!r}")
        if parts.hostname != _TESTNET_HOST:
            if not isinstance(self.mainnet_permit, MainnetPermit):
                raise ValueError(
                    f"{parts.hostname} is not the Binance testnet; mainnet needs a MainnetPermit "
                    "(issued only after live approval, ADR-014)"
                )
            if parts.hostname not in _MAINNET_HOSTS:
                raise ValueError(f"{parts.hostname} is not a documented Binance spot host")
        if not self.api_key or not self.api_secret:
            raise ValueError("api_key and api_secret are required")
        if not Decimal("0") < self.stop_limit_buffer < Decimal("0.1"):
            raise ValueError("stop_limit_buffer must be between 0 and 0.1")

    @classmethod
    def from_env(cls, env: Mapping[str, str], **overrides: Any) -> BinanceSpotConfig:
        """Build from ``HERMES_BINANCE__API_KEY`` and ``HERMES_BINANCE__API_SECRET``.

        Raises:
            ValueError: A credential is missing.
        """
        key = env.get("HERMES_BINANCE__API_KEY", "").strip()
        secret = env.get("HERMES_BINANCE__API_SECRET", "").strip()
        return cls(api_key=key, api_secret=secret, **overrides)


@dataclass(slots=True)
class BinanceSpotAdapter:
    """Signed Binance spot trading, read and write.

    Attributes:
        config: Wire settings and credentials.
        client: HTTP client bound to ``config.base_url``.
        limiter: Token bucket for request weight.
        clock_ms: Source of the request timestamp in epoch milliseconds.
    """

    config: BinanceSpotConfig
    client: httpx.Client
    limiter: RateLimiter
    clock_ms: Callable[[], int] = field(default=lambda: int(time.time() * 1000))

    @classmethod
    def create(cls, config: BinanceSpotConfig) -> BinanceSpotAdapter:
        """Build an adapter with its own client and limiter."""
        client = httpx.Client(base_url=config.base_url, timeout=config.timeout_sec)
        return cls(config=config, client=client, limiter=RateLimiter(config=config.rate_limit))

    def close(self) -> None:
        """Release the HTTP client."""
        self.client.close()

    # ------------------------------------------------------------------
    # ExchangePort
    # ------------------------------------------------------------------

    def place_market_order(self, *, side: OrderSide, order: Order) -> Fill:
        """Submit a market order and return the confirmed fill.

        Raises:
            OrderNotPlacedError: The submission failed and Binance has no such order.
            OrderStatusUnknownError: The submission failed and the lookup did too.
            ExchangeError: Rejected order, nothing executed, or a malformed reply.
            NetworkError: Transport failure outside the submission.
        """
        body = self._submit(
            {
                "symbol": _symbol(order.pair),
                "side": side.value,
                "type": "MARKET",
                "quantity": str(order.qty.amount),
                "newOrderRespType": "FULL",
            }
        )
        fill = self._fill(order, body)
        if fill is None:
            raise ExchangeError(msg=f"market order executed nothing: status {body.get('status')}")
        return fill

    def place_limit_order(self, *, order: Order) -> Fill | Rejection:
        """Submit an immediate-or-cancel limit order.

        Returns:
            The fill, or a :class:`Rejection` when nothing executed at the limit.

        Raises:
            ValueError: ``order.limit_price`` is missing.
            OrderNotPlacedError: The submission failed and Binance has no such order.
            OrderStatusUnknownError: The submission failed and the lookup did too.
            ExchangeError: Rejected order or malformed reply.
            NetworkError: Transport failure outside the submission.
        """
        if order.limit_price is None:
            raise ValueError("place_limit_order needs order.limit_price")
        body = self._submit(
            {
                "symbol": _symbol(order.pair),
                "side": order.side.value,
                "type": "LIMIT",
                "timeInForce": "IOC",
                "quantity": str(order.qty.amount),
                "price": str(order.limit_price),
                "newOrderRespType": "FULL",
            }
        )
        fill = self._fill(order, body)
        if fill is None:
            return Rejection(order=order, reason=f"IOC limit not filled: {body.get('status')}")
        return fill

    def cancel_order(self, *, order_id: str) -> None:
        """Cancel ``SYMBOL:ORDERID``; an order that is already gone is not an error.

        Raises:
            ValueError: ``order_id`` is not ``SYMBOL:ORDERID``.
            ExchangeError: Any other refusal.
            NetworkError: Transport failure.
        """
        symbol, sep, binance_id = order_id.partition(":")
        if not sep or not symbol or not binance_id.isdigit():
            raise ValueError(f"order_id must be SYMBOL:ORDERID, got {order_id!r}")
        try:
            self._signed(
                "DELETE",
                "/api/v3/order",
                {"symbol": symbol, "orderId": binance_id},
                weight=_WEIGHTS["cancel"],
            )
        except _BinanceRefusalError as refusal:
            if refusal.code != _UNKNOWN_ORDER:
                raise ExchangeError(msg=str(refusal)) from refusal

    def get_orderbook(self, *, pair: Pair) -> Orderbook:
        """Return the top 100 levels of ``pair``'s book.

        Raises:
            ExchangeError: Malformed reply or refusal.
            NetworkError: Transport failure.
        """
        body = self._public(
            "/api/v3/depth",
            {"symbol": _symbol(pair), "limit": str(_DEPTH_LIMIT)},
            weight=_WEIGHTS["depth"],
        )
        try:
            bids = tuple(
                OrderbookLevel(price=_decimal(p, "bid price"), quantity=_decimal(q, "bid qty"))
                for p, q in body["bids"]
            )
            asks = tuple(
                OrderbookLevel(price=_decimal(p, "ask price"), quantity=_decimal(q, "ask qty"))
                for p, q in body["asks"]
            )
            return Orderbook(
                pair=pair,
                bids=bids,
                asks=asks,
                snapshot_time=datetime.fromtimestamp(self.clock_ms() / 1000, tz=UTC),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ExchangeError(msg=f"malformed depth reply: {exc}") from exc

    def get_balance(self, *, currency: Currency) -> Quantity:
        """Return the free balance of ``currency``; zero when not held.

        Raises:
            ExchangeError: Malformed reply or refusal.
            NetworkError: Transport failure.
        """
        account = self._account()
        for balance in account.get("balances", []):
            if isinstance(balance, dict) and balance.get("asset") == currency.value:
                return Quantity(amount=_decimal(balance.get("free"), "free"), asset=currency)
        return Quantity(amount=Decimal("0"), asset=currency)

    def get_fees(self, *, pair: Pair) -> FeeSchedule:
        """Return the account's maker and taker rates as a one-tier schedule.

        Raises:
            ExchangeError: Malformed reply or refusal.
            NetworkError: Transport failure.
        """
        rates = self._account().get("commissionRates")
        if not isinstance(rates, dict):
            raise ExchangeError(msg="account reply has no commissionRates")
        maker = _decimal(rates.get("maker"), "maker") * _BPS
        taker = _decimal(rates.get("taker"), "taker") * _BPS
        return FeeSchedule(
            pair=pair,
            tiers=(
                FeeTier(
                    volume_threshold_usd=Decimal("0"),
                    maker_bps=int(maker),
                    taker_bps=int(taker),
                ),
            ),
        )

    # ------------------------------------------------------------------
    # StopLossPort
    # ------------------------------------------------------------------

    def place_stop_loss(self, *, order: Order, stop_price: Decimal) -> str:
        """Rest a stop-loss-limit SELL on the exchange and return its order id.

        The sell triggers at ``stop_price`` and may fill down to
        ``stop_price * (1 - stop_limit_buffer)``. It protects an open position
        while hermes is down; the strategy's own exits still run.

        Raises:
            ValueError: ``order`` is not a SELL or ``stop_price`` is not positive.
            OrderNotPlacedError: The submission failed and Binance has no such order.
            OrderStatusUnknownError: The submission failed and the lookup did too.
            ExchangeError: Refusal or malformed reply.
        """
        if order.side is not OrderSide.SELL:
            raise ValueError("a stop-loss protects a long position, so it must be a SELL")
        if stop_price <= 0:
            raise ValueError("stop_price must be positive")
        limit = stop_price * (Decimal("1") - self.config.stop_limit_buffer)
        symbol = _symbol(order.pair)
        body = self._submit(
            {
                "symbol": symbol,
                "side": "SELL",
                "type": "STOP_LOSS_LIMIT",
                "timeInForce": "GTC",
                "quantity": str(order.qty.amount),
                "price": str(limit.normalize()),
                "stopPrice": str(stop_price),
                "newOrderRespType": "ACK",
            }
        )
        order_id = body.get("orderId")
        if not isinstance(order_id, int):
            raise ExchangeError(msg=f"stop-loss reply has no orderId: {body!r}")
        return f"{symbol}:{order_id}"

    # ------------------------------------------------------------------
    # Wire
    # ------------------------------------------------------------------

    def _submit(self, params: dict[str, str]) -> dict[str, Any]:
        """POST an order under a fresh client id; on a 5xx or timeout, look it up."""
        client_id = f"{_CLIENT_ID_PREFIX}{secrets.token_hex(10)}"
        try:
            return self._signed(
                "POST",
                "/api/v3/order",
                {**params, "newClientOrderId": client_id},
                weight=_WEIGHTS["order"],
            )
        except (_ServerError, NetworkError) as exc:
            _log.warning(
                "binance.order.status_unknown",
                symbol=params["symbol"],
                client_order_id=client_id,
                error=str(exc),
            )
            return self._look_up(params["symbol"], client_id, cause=exc)

    def _look_up(self, symbol: str, client_id: str, *, cause: Exception) -> dict[str, Any]:
        """Return the order sent as ``client_id`` in the shape of a ``FULL`` order reply."""
        try:
            found = self._signed(
                "GET",
                "/api/v3/order",
                {"symbol": symbol, "origClientOrderId": client_id},
                weight=_WEIGHTS["query"],
            )
        except _BinanceRefusalError as refusal:
            if refusal.code == _NO_SUCH_ORDER:
                raise OrderNotPlacedError(
                    msg=f"order {client_id} on {symbol} was not placed (after: {cause})",
                    client_order_id=client_id,
                ) from cause
            raise self._unknown(symbol, client_id, refusal) from refusal
        except (ExchangeError, NetworkError) as exc:
            raise self._unknown(symbol, client_id, exc) from exc
        order_id = found.get("orderId")
        if not isinstance(order_id, int) or isinstance(order_id, bool):
            raise self._unknown(symbol, client_id, f"lookup has no orderId: {found!r}")
        reply: dict[str, Any] = {
            "orderId": order_id,
            "status": found.get("status"),
            "executedQty": found.get("executedQty"),
            "cummulativeQuoteQty": found.get("cummulativeQuoteQty"),
            "transactTime": found.get("updateTime", found.get("time")),
            "fills": [],
        }
        if _decimal(found.get("executedQty"), "executedQty") > 0:
            try:
                trades = self._signed_rows(
                    "/api/v3/myTrades",
                    {"symbol": symbol, "orderId": str(order_id)},
                    weight=_WEIGHTS["trades"],
                )
            except (ExchangeError, NetworkError) as exc:
                raise self._unknown(symbol, client_id, exc, order_id=order_id) from exc
            reply["fills"] = trades
        return reply

    @staticmethod
    def _unknown(
        symbol: str, client_id: str, cause: object, *, order_id: int | None = None
    ) -> OrderStatusUnknownError:
        placed = f" (Binance order {order_id})" if order_id is not None else ""
        return OrderStatusUnknownError(
            msg=(
                f"order {client_id}{placed} on {symbol}: execution status unknown, reconcile "
                f"it on Binance before retrying ({cause})"
            ),
            client_order_id=client_id,
        )

    def _quote_fee(self, part: Mapping[str, Any], asset: str, quote: str) -> Decimal:
        """Price a commission charged in a third asset, at its ticker or the pre-registered rate."""
        commission = _decimal(part.get("commission"), "commission")
        try:
            ticker = self._public(
                "/api/v3/ticker/price", {"symbol": f"{asset}{quote}"}, weight=_WEIGHTS["ticker"]
            )
            return commission * _decimal(ticker.get("price"), "ticker price")
        except (ExchangeError, NetworkError) as exc:
            notional = _decimal(part.get("price"), "price") * _decimal(part.get("qty"), "qty")
            _log.warning(
                "binance.fee.priced_at_taker_rate",
                commission_asset=asset,
                commission=str(commission),
                error=str(exc),
            )
            return notional * _FALLBACK_TAKER_RATE

    def _account(self) -> dict[str, Any]:
        return self._signed("GET", "/api/v3/account", {}, weight=_WEIGHTS["account"])

    def _public(self, path: str, params: dict[str, str], *, weight: int) -> dict[str, Any]:
        self.limiter.acquire(weight)
        return self._send("GET", path, params=params)

    def _signed(
        self, method: str, path: str, params: dict[str, str], *, weight: int
    ) -> dict[str, Any]:
        self.limiter.acquire(weight)
        signed = self._sign_query(params)
        return self._send(
            method,
            f"{path}?{signed}",
            headers={_API_KEY_HEADER: self.config.api_key},
        )

    def _signed_rows(
        self, path: str, params: dict[str, str], *, weight: int
    ) -> list[dict[str, Any]]:
        self.limiter.acquire(weight)
        signed = self._sign_query(params)
        body = self._send_any(
            "GET", f"{path}?{signed}", headers={_API_KEY_HEADER: self.config.api_key}
        )
        if not isinstance(body, list) or not all(isinstance(row, dict) for row in body):
            raise ExchangeError(
                msg=f"expected a JSON list of objects, got {str(body)[:_ERROR_EXCERPT]}"
            )
        return body

    def _sign_query(self, params: dict[str, str]) -> str:
        query = urlencode(
            {
                **params,
                "recvWindow": str(self.config.recv_window_ms),
                "timestamp": str(self.clock_ms()),
            }
        )
        return f"{query}&signature={sign(query, self.config.api_secret)}"

    def _send(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        body = self._send_any(method, url, params=params, headers=headers)
        if not isinstance(body, dict):
            raise ExchangeError(msg=f"expected a JSON object, got {str(body)[:_ERROR_EXCERPT]}")
        return body

    def _send_any(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> object:
        try:
            response = self.client.request(method, url, params=params, headers=headers)
        except httpx.HTTPError as exc:
            raise NetworkError(
                msg=f"transport failure on {url.split('?', maxsplit=1)[0]}: {exc!r}"
            ) from exc
        if response.status_code in _RATE_LIMITED:
            raise ExchangeError(
                msg=f"rate limited (HTTP {response.status_code}); limiter is misconfigured"
            )
        if response.status_code >= _SERVER_ERROR_MIN:
            raise _ServerError(
                msg=f"Binance HTTP {response.status_code}: {response.text[:_ERROR_EXCERPT]}"
            )
        try:
            body: object = response.json()
        except ValueError as exc:
            raise ExchangeError(
                msg=f"non-JSON reply (HTTP {response.status_code}): "
                f"{response.text[:_ERROR_EXCERPT]}"
            ) from exc
        if response.status_code >= _HTTP_ERROR_MIN:
            code = body.get("code") if isinstance(body, dict) else None
            text = body.get("msg") if isinstance(body, dict) else str(body)
            raise _BinanceRefusalError(
                msg=f"Binance refused (HTTP {response.status_code}, code {code}): {text}",
                code=code if isinstance(code, int) else 0,
            )
        return body

    def _fill(self, order: Order, body: dict[str, Any]) -> Fill | None:
        executed = _decimal(body.get("executedQty"), "executedQty")
        if executed <= 0:
            return None
        quote_spent = _decimal(body.get("cummulativeQuoteQty"), "cummulativeQuoteQty")
        avg_price = quote_spent / executed
        fee = Decimal("0")
        for part in body.get("fills", []):
            if not isinstance(part, dict):
                raise ExchangeError(msg=f"malformed fill: {part!r}")
            commission = _decimal(part.get("commission"), "commission")
            asset = part.get("commissionAsset")
            if asset == order.pair.quote.value:
                fee += commission
            elif asset == order.pair.base.value:
                fee += commission * _decimal(part.get("price"), "price")
            elif commission != 0:
                fee += self._quote_fee(part, str(asset), order.pair.quote.value)
        try:
            return Fill(
                order=order,
                executed_qty=executed,
                avg_price=avg_price,
                fee=Money(amount=fee, currency=order.pair.quote),
                timestamp=datetime.fromtimestamp(
                    float(_decimal(body.get("transactTime"), "transactTime")) / 1000, tz=UTC
                ),
            )
        except ValueError as exc:
            raise ExchangeError(msg=f"fill fails domain invariants: {exc}") from exc


@dataclass(frozen=True, slots=True)
class _BinanceRefusalError(ExchangeError):
    """Binance answered with an HTTP error; ``code`` is its error code."""

    code: int = 0


@dataclass(frozen=True, slots=True)
class _ServerError(ExchangeError):
    """Binance answered 5xx: for an order, its execution status is unknown."""


@dataclass(frozen=True, slots=True)
class OrderNotPlacedError(ExchangeError):
    """An order whose submission failed is confirmed absent on Binance: safe to retry.

    Attributes:
        client_order_id: The ``newClientOrderId`` the lookup used.
    """

    client_order_id: str = ""


@dataclass(frozen=True, slots=True)
class OrderStatusUnknownError(ExchangeError):
    """Neither the submission nor the lookup told whether the order exists.

    Never retry blindly: the order may be live. Reconcile it on Binance by
    ``client_order_id`` first.

    Attributes:
        client_order_id: The ``newClientOrderId`` the order was sent with.
    """

    client_order_id: str = ""
