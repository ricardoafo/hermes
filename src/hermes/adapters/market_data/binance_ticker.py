"""Binance public ticker price, cached, for the operator console.

Implements :class:`~hermes.ports.price_lookup.PriceLookupPort` with
``GET /api/v3/ticker/price?symbol=<SYMBOL>`` on the market-data host from
:class:`~hermes.infrastructure.config.models.MarketDataConfig`.

- **Public and read-only**: GET on one allow-listed path, no key, no
  signature, no account or order endpoint. A client carrying an API key
  header is refused at construction.
- **Cached**: a quote is reused for ``cache_seconds`` (5 s by default), so a
  console refreshing every second sends at most one request per pair per
  window.
- **Budgeted**: every request takes its weight (2) from the same kind of
  token bucket as the klines adapter, without blocking the event loop.
- **Errors**: no retries (the console simply shows the last value or a
  dash). Transport failures raise :class:`~hermes.domain.errors.NetworkError`;
  HTTP errors and malformed bodies raise
  :class:`~hermes.domain.errors.ExchangeError`.

Reference: issue #389; openspec/changes/389-price-lookup/change.md.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from typing import Final

import anyio
import httpx

from hermes.adapters.exchanges.rate_limiter import RateLimitConfig, RateLimiter
from hermes.domain.errors import ExchangeError, NetworkError
from hermes.domain.value_objects.pair import Pair
from hermes.infrastructure.config.models import MarketDataConfig
from hermes.ports.clock import ClockPort
from hermes.ports.price_lookup import PriceQuote

__all__ = ["BinanceTickerPriceAdapter"]

_TICKER_PATH: Final[str] = "/api/v3/ticker/price"
_TICKER_WEIGHT: Final[int] = 2
_API_KEY_HEADER: Final[str] = "x-mbx-apikey"
_HTTP_OK_MAX: Final[int] = 299
_ERROR_EXCERPT_CHARS: Final[int] = 200


def _symbol(pair: Pair) -> str:
    return f"{pair.base.value}{pair.quote.value}"


class BinanceTickerPriceAdapter:
    """Cached last-trade price from Binance's public ticker.

    The adapter owns the injected ``client`` and closes it in :meth:`aclose`.
    """

    def __init__(
        self,
        *,
        config: MarketDataConfig,
        clock: ClockPort,
        client: httpx.AsyncClient,
        limiter: RateLimiter,
        cache_seconds: float = 5.0,
        sleep: Callable[[float], Awaitable[None]] = anyio.sleep,
    ) -> None:
        """Wire the adapter.

        Args:
            config: Market-data host and timeout.
            clock: Time source for the cache and the quote's ``as_of``.
            client: HTTP client. Must not carry an API key header.
            limiter: Token bucket for request weight.
            cache_seconds: How long a quote is reused.
            sleep: Async sleep used while waiting for budget.

        Raises:
            ValueError: ``client`` carries an API key, or ``cache_seconds`` is
                negative.
        """
        if _API_KEY_HEADER in client.headers:
            raise ValueError(
                "BinanceTickerPriceAdapter is public-only: client must not carry an API key"
            )
        if cache_seconds < 0:
            raise ValueError(f"cache_seconds must be non-negative, got {cache_seconds}")
        self._url = f"{config.base_url}{_TICKER_PATH}"
        self._clock = clock
        self._client = client
        self._limiter = limiter
        self._ttl = timedelta(seconds=cache_seconds)
        self._sleep = sleep
        self._cache: dict[Pair, PriceQuote] = {}

    @classmethod
    def create(cls, config: MarketDataConfig, clock: ClockPort) -> BinanceTickerPriceAdapter:
        """Build an adapter with its own client and a limiter sized from ``config``."""
        return cls(
            config=config,
            clock=clock,
            client=httpx.AsyncClient(timeout=config.timeout_sec),
            limiter=RateLimiter(
                RateLimitConfig(
                    max_weight=config.rate_limit_max_weight,
                    window_seconds=config.rate_limit_window_seconds,
                )
            ),
        )

    async def last_price(self, pair: Pair) -> PriceQuote:
        """Return the latest price of ``pair``, from cache when fresh.

        Raises:
            hermes.domain.errors.NetworkError: The request failed in transport.
            hermes.domain.errors.ExchangeError: HTTP error or malformed body.
        """
        now = self._clock.now()
        cached = self._cache.get(pair)
        if cached is not None and now - cached.as_of < self._ttl:
            return cached
        price = await self._fetch(pair)
        quote = PriceQuote(pair=pair, price=price, as_of=now)
        self._cache[pair] = quote
        return quote

    async def aclose(self) -> None:
        """Close the owned HTTP client."""
        await self._client.aclose()

    async def _fetch(self, pair: Pair) -> Decimal:
        while not self._limiter.try_acquire(_TICKER_WEIGHT):
            await self._sleep(_TICKER_WEIGHT / self._limiter.refill_per_second)
        symbol = _symbol(pair)
        try:
            response = await self._client.get(self._url, params={"symbol": symbol})
        except httpx.HTTPError as exc:
            raise NetworkError(f"transport failure reading ticker {symbol}: {exc!r}") from exc
        if response.status_code > _HTTP_OK_MAX:
            raise ExchangeError(
                f"HTTP {response.status_code} from ticker {symbol}: "
                f"{response.text[:_ERROR_EXCERPT_CHARS]}"
            )
        return _parse_price(response, symbol)


def _parse_price(response: httpx.Response, symbol: str) -> Decimal:
    try:
        body: object = response.json()
    except ValueError as exc:
        raise ExchangeError(f"non-JSON ticker body for {symbol}") from exc
    if not isinstance(body, dict) or body.get("symbol") != symbol:
        raise ExchangeError(f"ticker body is not for {symbol}: {str(body)[:_ERROR_EXCERPT_CHARS]}")
    raw = body.get("price")
    if not isinstance(raw, str):
        raise ExchangeError(f"ticker price for {symbol} is not a string: {raw!r}")
    try:
        price = Decimal(raw)
    except InvalidOperation as exc:
        raise ExchangeError(f"ticker price for {symbol} is not a decimal: {raw!r}") from exc
    if not price.is_finite() or price <= 0:
        raise ExchangeError(f"ticker price for {symbol} is not positive: {raw!r}")
    return price
