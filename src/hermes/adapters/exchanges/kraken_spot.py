"""Kraken spot trading adapter, the cold backup venue (ADR-004).

Implements :class:`~hermes.ports.exchange.ExchangePort` and
:class:`~hermes.ports.exchange.StopLossPort` with signed calls to Kraken's
private REST API:

- ``AddOrder`` for market, IOC limit and stop-loss-limit orders, followed by
  ``QueryOrders`` to read the execution, because Kraken's ``AddOrder`` reply
  carries only the order id;
- ``CancelOrder`` to cancel (idempotent: ``EOrder:Unknown order`` is not an
  error);
- ``BalanceEx`` for free balances;
- ``TradeVolume`` for the account's own maker and taker fee on a pair.

The order book is public data and comes from
:class:`~hermes.adapters.exchanges.kraken_public.KrakenPublicAdapter`. The fee
schedule used to come from there too, until Kraken stopped publishing fee
tiers in the public ``AssetPairs`` reply on 2026-10-04 (#599).

Safety (ADR-003 §00 I-08, AGENTS.md rule 7):

- Kraken has no spot testnet, so every signed call reaches a real account.
  Without a :class:`~hermes.infrastructure.mainnet_permit.MainnetPermit`
  the adapter is read-only: placing or cancelling an order raises before any
  request is built. A permit is issued only in the prod tier with
  ``HermesConfig.allow_mainnet`` set and the strategy LIVE_APPROVED (#382,
  #781, ADR-014). ``hermes serve`` wires no exchange.
- Only ``https://api.kraken.com`` is accepted, so signed requests cannot be
  sent to a mistyped host.
- Requests are signed as Kraken documents: HMAC-SHA512, keyed by the
  base64-decoded secret, over the URI path followed by
  ``SHA256(nonce + POST body)``. The secret never leaves this module and is
  excluded from ``repr``.
- Every signed call takes a token from a bucket sized below Kraken's
  starter-tier counter (15, decaying 0.33 a second). A rate-limit error is
  not retried: the limiter is misconfigured, and hammering earns a lockout.
- Orders ask for fees in the quote currency (``oflags=fciq``), so a fill's
  fee is quote money and the bought quantity is not reduced by a fee.

Losing track of a real order (#858):

- Every ``AddOrder`` carries a random ``userref``. A placement whose reply is
  lost, or whose execution cannot be read back, raises
  :class:`OrderUnreadError` naming the ``userref`` and, once known, the txid,
  so the operator can find the live order instead of placing another.
- With ``nonce_file`` set, the last nonce is kept on disk and every nonce is
  above it, so a restart with the clock stepped back still sends an
  increasing nonce instead of earning ``EAPI:Invalid nonce``.

Reference: issue #32; openspec/changes/32-kraken-spot-adapter/change.md.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import re
import secrets
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation, Overflow
from pathlib import Path
from typing import Any, Final
from urllib.parse import urlencode, urlsplit

import httpx

from hermes.adapters.exchanges.kraken_public import (
    KrakenPublicAdapter,
    KrakenPublicConfig,
    _pair_to_kraken_symbol,
)
from hermes.adapters.exchanges.rate_limiter import RateLimitConfig, RateLimiter
from hermes.domain.errors import ExchangeError, NetworkError
from hermes.domain.fees import FeeSchedule, FeeTier
from hermes.domain.market_data.orderbook import Orderbook
from hermes.domain.trading.fill import Fill, Rejection
from hermes.domain.trading.order import Order, OrderSide
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.quantity import Quantity
from hermes.infrastructure.mainnet_permit import MainnetPermit

__all__ = ["KRAKEN_BASE_URL", "KrakenSpotAdapter", "KrakenSpotConfig", "OrderUnreadError", "sign"]

KRAKEN_BASE_URL: Final = "https://api.kraken.com"
_KRAKEN_HOST: Final = "api.kraken.com"
_PRIVATE_PREFIX: Final = "/0/private/"
_UNKNOWN_ORDER: Final = "EOrder:Unknown order"
_RATE_LIMIT_ERRORS: Final = frozenset({"EAPI:Rate limit exceeded", "EGeneral:Too many requests"})
_TXID: Final = re.compile(r"^[A-Z0-9]{6}-[A-Z0-9]{5}-[A-Z0-9]{6}$")
_FINAL_STATUSES: Final = frozenset({"closed", "canceled", "expired"})
_DEPTH_LEVELS: Final = 100
_ERROR_EXCERPT: Final = 200
_HTTP_ERROR_MIN: Final = 400
_USERREF_MAX: Final = 2**31 - 1
"""Kraken's ``userref`` is a signed 32-bit integer."""

# Kraken's legacy asset codes: X for crypto, Z for fiat. Newer listings
# (USDT, USDC, SOL) are reported unprefixed in balances.
_BALANCE_CODES: Final[dict[Currency, tuple[str, ...]]] = {
    Currency.BTC: ("XXBT", "XBT"),
    Currency.ETH: ("XETH", "ETH"),
    Currency.EUR: ("ZEUR", "EUR"),
    Currency.USDT: ("USDT",),
    Currency.USDC: ("USDC",),
    Currency.SOL: ("SOL",),
}


def sign(*, path: str, nonce: str, post_data: str, secret: str) -> str:
    """Return Kraken's ``API-Sign`` header value.

    Args:
        path: URI path, e.g. ``/0/private/AddOrder``.
        nonce: The nonce sent in ``post_data``.
        post_data: The exact URL-encoded body that will be sent.
        secret: The base64 API secret.

    Raises:
        ValueError: ``secret`` is not valid base64.
    """
    try:
        key = base64.b64decode(secret, validate=True)
    except binascii.Error as exc:
        raise ValueError("Kraken API secret is not valid base64") from exc
    digest = hashlib.sha256((nonce + post_data).encode()).digest()
    mac = hmac.new(key, path.encode() + digest, hashlib.sha512)
    return base64.b64encode(mac.digest()).decode()


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


def _fee_percent(result: Mapping[str, Any], side: str, symbol: str) -> Decimal:
    """Return the ``fee`` percent of ``symbol`` in a TradeVolume ``side`` map.

    Kraken may key the reply by its own pair name rather than the one asked
    for; a reply holding a single pair is that pair.
    """
    by_pair = result.get(side)
    if not isinstance(by_pair, dict) or not by_pair:
        raise ExchangeError(msg=f"Kraken TradeVolume has no {side!r} for {symbol}: {result!r}")
    entry = by_pair.get(symbol)
    if entry is None and len(by_pair) == 1:
        entry = next(iter(by_pair.values()))
    if not isinstance(entry, dict):
        raise ExchangeError(msg=f"Kraken TradeVolume {side!r} lacks {symbol}: {by_pair!r}")
    return _decimal(entry.get("fee"), f"{symbol} {side} fee")


def _percent_to_bps(percent: Decimal) -> int:
    """Convert Kraken's percent (0.26 = 0.26%) to whole basis points.

    Raises:
        ExchangeError: ``percent`` is too large for the decimal context.
    """
    try:
        return int((percent * 100).to_integral_value())
    except Overflow as exc:
        raise ExchangeError(msg=f"Kraken fee percent is out of range: {percent}") from exc


@dataclass(frozen=True, slots=True)
class KrakenSpotConfig:
    """Wire settings for :class:`KrakenSpotAdapter`.

    Attributes:
        api_key: Kraken API key (sent as a header, not secret by itself).
        api_secret: Base64 Kraken API secret, used only to sign. Not in ``repr``.
        base_url: REST root; only ``https://api.kraken.com`` is accepted.
        mainnet_permit: Lets the adapter place and cancel orders. Kraken has
            no spot testnet, so without it the adapter is read-only. Only
            :func:`~hermes.infrastructure.mainnet_permit.issue_mainnet_permit`
            makes one, after the tier, kill-switch and live-approval checks.
        timeout_sec: Per-request timeout.
        rate_limit: Token bucket for signed calls, below Kraken's starter
            tier (a counter of 15 decaying 0.33 a second).
        stop_limit_buffer: How far below the stop trigger a stop-loss-limit
            sell may fill, as a fraction (0.01 = 1%). A safety net for an
            outage, not a strategy parameter.
        settle_checks: How many times to read an order back before giving up
            on it reaching a final state.
        settle_interval_sec: Pause between those reads.
        nonce_file: Where the last nonce is kept across restarts, or ``None``
            to keep it in memory only. Give each API key its own file.
    """

    api_key: str
    api_secret: str = field(repr=False)
    base_url: str = KRAKEN_BASE_URL
    mainnet_permit: MainnetPermit | None = None
    timeout_sec: float = 10.0
    rate_limit: RateLimitConfig = field(
        default_factory=lambda: RateLimitConfig(max_weight=15, window_seconds=45)
    )
    stop_limit_buffer: Decimal = Decimal("0.01")
    settle_checks: int = 5
    settle_interval_sec: float = 0.5
    nonce_file: Path | None = None

    def __post_init__(self) -> None:
        """Refuse another host, empty keys, a non-base64 secret or bad tuning.

        Raises:
            ValueError: On any of those.
        """
        parts = urlsplit(self.base_url)
        if parts.scheme != "https" or parts.hostname != _KRAKEN_HOST:
            raise ValueError(f"base_url must be {KRAKEN_BASE_URL}, got {self.base_url!r}")
        if not self.api_key or not self.api_secret:
            raise ValueError("api_key and api_secret are required")
        try:
            base64.b64decode(self.api_secret, validate=True)
        except binascii.Error as exc:
            raise ValueError("api_secret must be the base64 secret Kraken issued") from exc
        if not Decimal("0") < self.stop_limit_buffer < Decimal("0.1"):
            raise ValueError("stop_limit_buffer must be between 0 and 0.1")
        if self.settle_checks < 1:
            raise ValueError("settle_checks must be at least 1")

    @classmethod
    def from_env(cls, env: Mapping[str, str], **overrides: Any) -> KrakenSpotConfig:
        """Build from ``HERMES_KRAKEN__API_KEY`` and ``HERMES_KRAKEN__API_SECRET``.

        Raises:
            ValueError: A credential is missing or malformed.
        """
        key = env.get("HERMES_KRAKEN__API_KEY", "").strip()
        secret = env.get("HERMES_KRAKEN__API_SECRET", "").strip()
        return cls(api_key=key, api_secret=secret, **overrides)


@dataclass(slots=True)
class KrakenSpotAdapter:
    """Signed Kraken spot trading, read and write.

    Attributes:
        config: Wire settings and credentials.
        client: HTTP client bound to ``config.base_url``.
        limiter: Token bucket for signed calls.
        public: Public-data adapter for the order book and fee schedule.
        clock_ms: Source of the nonce, in epoch milliseconds.
        sleep: Pause between order read-backs; injected so tests do not wait.
    """

    config: KrakenSpotConfig
    client: httpx.Client
    limiter: RateLimiter
    public: KrakenPublicAdapter
    clock_ms: Callable[[], int] = field(default=lambda: int(time.time() * 1000))
    sleep: Callable[[float], None] = field(default=time.sleep)
    _last_nonce: int = field(default=0, init=False)

    @classmethod
    def create(cls, config: KrakenSpotConfig) -> KrakenSpotAdapter:
        """Build an adapter with its own client, limiter and public adapter."""
        client = httpx.Client(base_url=config.base_url, timeout=config.timeout_sec)
        public = KrakenPublicAdapter.create(
            KrakenPublicConfig(base_url=config.base_url, timeout_sec=config.timeout_sec)
        )
        return cls(
            config=config,
            client=client,
            limiter=RateLimiter(config=config.rate_limit),
            public=public,
        )

    def close(self) -> None:
        """Release both HTTP clients."""
        self.client.close()
        self.public.close()

    # ------------------------------------------------------------------
    # ExchangePort
    # ------------------------------------------------------------------

    def place_market_order(self, *, side: OrderSide, order: Order) -> Fill:
        """Submit a market order and return the confirmed fill.

        Raises:
            OrderUnreadError: Placed, or possibly placed, but not read back.
            ExchangeError: Trading not allowed, rejected order, nothing
                executed, an order that did not settle, or a malformed reply.
            NetworkError: Transport failure before the order was sent.
        """
        txid, userref = self._add_order(
            {
                "ordertype": "market",
                "type": side.value.lower(),
                "volume": str(order.qty.amount),
            },
            order,
        )
        status, info = self._settle(txid, userref)
        fill = self._fill(order, info)
        if fill is None:
            raise ExchangeError(msg=f"market order {txid} executed nothing: status {status}")
        return fill

    def place_limit_order(self, *, order: Order) -> Fill | Rejection:
        """Submit an immediate-or-cancel limit order.

        Returns:
            The fill, or a :class:`Rejection` when nothing executed at the limit.

        Raises:
            ValueError: ``order.limit_price`` is missing.
            OrderUnreadError: Placed, or possibly placed, but not read back.
            ExchangeError: Trading not allowed, rejected order or malformed reply.
            NetworkError: Transport failure before the order was sent.
        """
        if order.limit_price is None:
            raise ValueError("place_limit_order needs order.limit_price")
        txid, userref = self._add_order(
            {
                "ordertype": "limit",
                "type": order.side.value.lower(),
                "volume": str(order.qty.amount),
                "price": str(order.limit_price),
                "timeinforce": "IOC",
            },
            order,
        )
        status, info = self._settle(txid, userref)
        fill = self._fill(order, info)
        if fill is None:
            return Rejection(order=order, reason=f"IOC limit {txid} not filled: {status}")
        return fill

    def cancel_order(self, *, order_id: str) -> None:
        """Cancel a Kraken order id; an order that is already gone is not an error.

        Raises:
            ValueError: ``order_id`` is not a Kraken transaction id.
            ExchangeError: Trading not allowed, or any other refusal.
            NetworkError: Transport failure.
        """
        if not _TXID.fullmatch(order_id):
            raise ValueError(
                f"order_id must be a Kraken txid like OABCDE-FGHIJ-KLMNOP, got {order_id!r}"
            )
        self._require_trading("cancel")
        try:
            self._private("CancelOrder", {"txid": order_id})
        except _KrakenRefusalError as refusal:
            if _UNKNOWN_ORDER not in refusal.errors:
                raise

    def get_orderbook(self, *, pair: Pair) -> Orderbook:
        """Return the top 100 levels of ``pair``'s book from the public API.

        Raises:
            hermes.domain.errors.KrakenPublicError: Wire or schema failure.
        """
        return self.public.get_orderbook(pair, depth=_DEPTH_LEVELS)

    def get_balance(self, *, currency: Currency) -> Quantity:
        """Return the free balance of ``currency`` (held in orders excluded).

        Returns:
            Zero when the currency is not held.

        Raises:
            ExchangeError: Malformed reply or refusal.
            NetworkError: Transport failure.
        """
        balances = self._private("BalanceEx", {})
        for code in _BALANCE_CODES.get(currency, (currency.value,)):
            entry = balances.get(code)
            if entry is None:
                continue
            if not isinstance(entry, dict):
                raise ExchangeError(msg=f"malformed balance for {code}: {entry!r}")
            total = _decimal(entry.get("balance"), f"{code} balance")
            held = _decimal(entry.get("hold_trade", "0"), f"{code} hold_trade")
            return Quantity(amount=max(total - held, Decimal("0")), asset=currency)
        return Quantity(amount=Decimal("0"), asset=currency)

    def get_fees(self, *, pair: Pair) -> FeeSchedule:
        """Return the account's current maker and taker fee for ``pair``.

        Read-only, so it works without a ``mainnet_permit``. ``TradeVolume``
        answers with the fee the account pays now, given its 30-day volume, so
        the schedule has one tier that applies at any volume.

        Raises:
            hermes.domain.errors.ExchangeError: Wire failure, a refusal, or a
                reply without a fee for ``pair``.
        """
        symbol = _pair_to_kraken_symbol(pair)
        result = self._private("TradeVolume", {"pair": symbol})
        taker = _fee_percent(result, "fees", symbol)
        maker = _fee_percent(result, "fees_maker", symbol)
        try:
            tier = FeeTier(
                volume_threshold_usd=Decimal("0"),
                maker_bps=_percent_to_bps(maker),
                taker_bps=_percent_to_bps(taker),
            )
        except ValueError as exc:
            raise ExchangeError(
                msg=f"Kraken TradeVolume fee for {symbol} is invalid: {exc}"
            ) from exc
        return FeeSchedule.from_tiers(pair=pair, tiers=(tier,))

    # ------------------------------------------------------------------
    # StopLossPort
    # ------------------------------------------------------------------

    def place_stop_loss(self, *, order: Order, stop_price: Decimal) -> str:
        """Rest a stop-loss-limit SELL on Kraken and return its txid.

        The sell triggers when the last trade reaches ``stop_price`` and may
        fill down to ``stop_price * (1 - stop_limit_buffer)``. It protects an
        open position while hermes is down; the strategy's own exits still run.

        Raises:
            ValueError: ``order`` is not a SELL or ``stop_price`` is not positive.
            OrderUnreadError: Possibly placed, but the reply was lost.
            ExchangeError: Trading not allowed, refusal or malformed reply.
            NetworkError: Transport failure before the order was sent.
        """
        if order.side is not OrderSide.SELL:
            raise ValueError("a stop-loss protects a long position, so it must be a SELL")
        if stop_price <= 0:
            raise ValueError("stop_price must be positive")
        limit = stop_price * (Decimal("1") - self.config.stop_limit_buffer)
        txid, _userref = self._add_order(
            {
                "ordertype": "stop-loss-limit",
                "type": "sell",
                "volume": str(order.qty.amount),
                "price": str(stop_price),
                "price2": f"{limit.normalize():f}",
                "trigger": "last",
            },
            order,
        )
        return txid

    # ------------------------------------------------------------------
    # Orders
    # ------------------------------------------------------------------

    def _require_trading(self, action: str) -> None:
        if not isinstance(self.config.mainnet_permit, MainnetPermit):
            raise ExchangeError(
                msg=f"refusing to {action} on Kraken: the adapter is read-only without "
                "a MainnetPermit (issued only after live approval, ADR-014)"
            )

    def _add_order(self, params: dict[str, str], order: Order) -> tuple[str, int]:
        """Place an order under a fresh ``userref``; return its txid and that ``userref``."""
        self._require_trading("place an order")
        userref = secrets.randbelow(_USERREF_MAX) + 1
        fields = {
            "pair": _pair_to_kraken_symbol(order.pair),
            "oflags": "fciq",
            "userref": str(userref),
            **params,
        }
        # A bad nonce file fails here, before anything is sent, so it is not an unread order.
        nonce = self._nonce()
        try:
            result = self._private("AddOrder", fields, nonce=nonce)
        except _KrakenRefusalError:
            raise
        except NetworkError as exc:
            if isinstance(exc.__cause__, httpx.ConnectError | httpx.ConnectTimeout):
                raise  # never connected, so never sent: safe to retry
            raise OrderUnreadError(
                msg=f"AddOrder reply lost (userref {userref}): the order may be live; find it "
                f"by userref on Kraken before placing another ({exc})",
                userref=userref,
            ) from exc
        except ExchangeError as exc:
            raise OrderUnreadError(
                msg=f"AddOrder reply lost (userref {userref}): the order may be live; find it "
                f"by userref on Kraken before placing another ({exc})",
                userref=userref,
            ) from exc
        txids = result.get("txid")
        if not isinstance(txids, list) or len(txids) != 1 or not isinstance(txids[0], str):
            raise OrderUnreadError(
                msg=f"AddOrder reply has no single txid (userref {userref}): {result!r}",
                userref=userref,
            )
        return txids[0], userref

    def _settle(self, txid: str, userref: int) -> tuple[str, dict[str, Any]]:
        """Read ``txid`` back until it is closed, cancelled or expired.

        Raises:
            OrderUnreadError: The read-back failed, or the order is still
                live after ``settle_checks`` reads. The error carries the
                txid and ``userref`` so the operator can reconcile it.
        """
        status = "unknown"
        for attempt in range(self.config.settle_checks):
            if attempt:
                self.sleep(self.config.settle_interval_sec)
            try:
                info = self._private("QueryOrders", {"txid": txid}).get(txid)
            except (ExchangeError, NetworkError) as exc:
                raise OrderUnreadError(
                    msg=f"order {txid} (userref {userref}) was placed but could not be read "
                    f"back; reconcile it on Kraken before placing another ({exc})",
                    txid=txid,
                    userref=userref,
                ) from exc
            if not isinstance(info, dict):
                raise OrderUnreadError(
                    msg=f"QueryOrders reply has no entry for {txid} (userref {userref})",
                    txid=txid,
                    userref=userref,
                )
            status = str(info.get("status"))
            if status in _FINAL_STATUSES:
                return status, info
        raise OrderUnreadError(
            msg=f"order {txid} still {status} after {self.config.settle_checks} checks "
            f"(userref {userref}); reconcile it on Kraken before placing another",
            txid=txid,
            userref=userref,
        )

    def _fill(self, order: Order, info: dict[str, Any]) -> Fill | None:
        executed = _decimal(info.get("vol_exec"), "vol_exec")
        if executed <= 0:
            return None
        cost = _decimal(info.get("cost"), "cost")
        fee = _decimal(info.get("fee"), "fee")
        closed_at = _decimal(info.get("closetm"), "closetm")
        try:
            return Fill(
                order=order,
                executed_qty=executed,
                avg_price=cost / executed,
                fee=Money(amount=fee, currency=order.pair.quote),
                timestamp=datetime.fromtimestamp(float(closed_at), tz=UTC),
            )
        except ValueError as exc:
            raise ExchangeError(msg=f"fill fails domain invariants: {exc}") from exc

    # ------------------------------------------------------------------
    # Wire
    # ------------------------------------------------------------------

    def _nonce(self) -> str:
        # Kraken rejects a nonce that does not increase, even within one millisecond.
        floor = self._last_nonce
        if self.config.nonce_file is not None and floor == 0:
            floor = _read_nonce(self.config.nonce_file)
        self._last_nonce = max(self.clock_ms(), floor + 1)
        if self.config.nonce_file is not None:
            _write_nonce(self.config.nonce_file, self._last_nonce)
        return str(self._last_nonce)

    def _private(
        self, method: str, params: dict[str, str], *, nonce: str | None = None
    ) -> dict[str, Any]:
        self.limiter.acquire(1)
        path = f"{_PRIVATE_PREFIX}{method}"
        nonce = nonce or self._nonce()
        body = urlencode({"nonce": nonce, **params})
        headers = {
            "API-Key": self.config.api_key,
            "API-Sign": sign(path=path, nonce=nonce, post_data=body, secret=self.config.api_secret),
            "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
        }
        try:
            response = self.client.post(path, content=body, headers=headers)
        except httpx.HTTPError as exc:
            raise NetworkError(msg=f"transport failure on {path}: {exc!r}") from exc
        if response.status_code >= _HTTP_ERROR_MIN:
            raise ExchangeError(
                msg=f"HTTP {response.status_code} from Kraken {path}: "
                f"{response.text[:_ERROR_EXCERPT]}"
            )
        try:
            data: object = response.json()
        except ValueError as exc:
            raise ExchangeError(
                msg=f"non-JSON reply from Kraken {path}: {response.text[:_ERROR_EXCERPT]}"
            ) from exc
        if not isinstance(data, dict):
            raise ExchangeError(
                msg=f"expected a JSON object from {path}, got {str(data)[:_ERROR_EXCERPT]}"
            )
        errors = data.get("error", [])
        if not isinstance(errors, list):
            raise ExchangeError(msg=f"Kraken {path} 'error' is not a list: {errors!r}")
        if errors:
            texts = tuple(str(e) for e in errors)
            if _RATE_LIMIT_ERRORS.intersection(texts):
                raise ExchangeError(msg=f"rate limited on {path}; limiter is misconfigured")
            raise _KrakenRefusalError(msg=f"Kraken refused {path}: {list(texts)}", errors=texts)
        result = data.get("result")
        if not isinstance(result, dict):
            raise ExchangeError(msg=f"Kraken {path} reply has no result object")
        return result


@dataclass(frozen=True, slots=True)
class _KrakenRefusalError(ExchangeError):
    """Kraken answered with a non-empty ``error`` list."""

    errors: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class OrderUnreadError(ExchangeError):
    """An order was, or may have been, placed on Kraken but was not read back.

    Never place another before finding this one: look it up on Kraken by
    ``txid``, or by ``userref`` when the txid never arrived.

    Attributes:
        txid: Kraken's order id, or ``""`` when the AddOrder reply was lost.
        userref: The reference the order was placed with.
    """

    txid: str = ""
    userref: int = 0


def _read_nonce(path: Path) -> int:
    """Return the nonce kept in ``path``, or 0 when there is none yet."""
    try:
        text = path.read_text(encoding="ascii").strip()
    except FileNotFoundError:
        return 0
    if not text.isdigit():
        raise ExchangeError(msg=f"Kraken nonce file {path} does not hold a nonce: {text[:40]!r}")
    return int(text)


def _write_nonce(path: Path, nonce: int) -> None:
    """Keep ``nonce`` in ``path``, atomically, before the request that uses it goes out."""
    staging = path.with_name(f"{path.name}.tmp")
    staging.write_text(str(nonce), encoding="ascii")
    staging.replace(path)
