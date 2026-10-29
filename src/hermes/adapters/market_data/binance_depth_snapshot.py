"""Binance public depth snapshot adapter (REST, keyless, read-only).

Implements :class:`~hermes.ports.orderbook_snapshot.OrderbookSnapshotPort` with
``GET /api/v3/depth`` on the market-data host from
:class:`~hermes.infrastructure.config.models.MarketDataConfig` (an allow-list of
one, ``https://data-api.binance.vision``).

- **Public only**: GET, one path, no key, signature or account code. A client
  that carries an ``X-MBX-APIKEY`` header is refused at construction.
- **Clock**: the book is stamped with the injected clock when the response
  arrives, so its age (risk rule RO-04) is measured on the same clock as the
  loop's.
- **Frozen upstream** (#859 item 1, option A): a book whose ``lastUpdateId``
  has not moved since an earlier response is as old as that response, so it
  keeps the earlier stamp and RO-04 halts entries on it. A cached or frozen
  upstream book no longer passes as fresh. The maintainer approved this new reason to
  halt as a kill-rule change on 17 Oct 2026.
- **Cache**: the last book per pair is reused for ``cache_seconds`` (1 s by
  default, below RO-04's 2 s limit), so a burst of reads costs one request.
- **Budget**: a depth request of up to 100 levels weighs 5; a token bucket
  sized from config gates every request without blocking the event loop.
- **Errors**: no retry. Transport failures raise ``NetworkError``; HTTP errors
  and malformed bodies raise ``ExchangeError``. The caller falls back to the
  pre-registration §6.2 book and records that it did.

Reference: openspec/archive/353-fill-kernel-in-serve/change.md D-1;
docs/decisions/2026-10-02-binance-public-market-data-host.md.
"""

from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Final

import anyio
import httpx
import structlog

from hermes.adapters._shared.rate_limiter import RateLimitConfig, RateLimiter
from hermes.adapters._shared.wire import binance_symbol, wire_decimal
from hermes.domain.errors import ExchangeError, NetworkError
from hermes.domain.market_data.orderbook import Orderbook, OrderbookLevel
from hermes.domain.value_objects.pair import Pair
from hermes.infrastructure.config.models import MarketDataConfig
from hermes.ports.clock import ClockPort

__all__ = ["DEPTH_LEVELS", "BinanceDepthSnapshotAdapter"]

_DEPTH_PATH: Final[str] = "/api/v3/depth"
_API_KEY_HEADER: Final[str] = "x-mbx-apikey"
DEPTH_LEVELS: Final[int] = 20
"""Levels requested per side: far deeper than a pre-registered order needs."""
_DEPTH_WEIGHT: Final[int] = 5
_HTTP_ERROR_MIN: Final[int] = 400
_ERROR_EXCERPT_CHARS: Final[int] = 200
_LEVEL_FIELDS: Final[int] = 2

_log = structlog.get_logger(__name__)


def _decimal(raw: object, *, field: str) -> Decimal:
    return wire_decimal(raw, field=f"depth {field}", error=ExchangeError, numbers=False)


def _levels(raw: object, *, side: str) -> tuple[OrderbookLevel, ...]:
    """Parse one side of the book: a list of ``[price, quantity]`` string pairs."""
    if not isinstance(raw, list):
        raise ExchangeError(f"depth {side} is not a list: {str(raw)[:_ERROR_EXCERPT_CHARS]}")
    levels: list[OrderbookLevel] = []
    for row in raw:
        if not isinstance(row, list) or len(row) < _LEVEL_FIELDS:
            raise ExchangeError(f"depth {side} level is malformed: {row!r}")
        try:
            levels.append(
                OrderbookLevel(
                    price=_decimal(row[0], field=f"{side} price"),
                    quantity=_decimal(row[1], field=f"{side} quantity"),
                )
            )
        except ValueError as exc:
            raise ExchangeError(f"depth {side} level is invalid: {exc}") from exc
    return tuple(levels)


def _update_id(body: dict[str, object]) -> int:
    """Return the book's ``lastUpdateId``: the exchange's sequence number for it."""
    raw = body.get("lastUpdateId")
    if not isinstance(raw, int) or isinstance(raw, bool) or raw < 0:
        raise ExchangeError(f"{_DEPTH_PATH} lastUpdateId is not a non-negative integer: {raw!r}")
    return raw


class BinanceDepthSnapshotAdapter:
    """Current depth of a pair from Binance's public market-data host.

    The adapter owns the injected ``client`` and closes it in :meth:`aclose`.
    """

    def __init__(
        self,
        *,
        config: MarketDataConfig,
        clock: ClockPort,
        client: httpx.AsyncClient,
        limiter: RateLimiter,
        cache_seconds: float = 1.0,
        sleep: Callable[[float], Awaitable[None]] = anyio.sleep,
    ) -> None:
        """Wire the adapter.

        Args:
            config: Host allow-list and timeout.
            clock: Time source that stamps each book.
            client: HTTP client. Must not carry an API key header.
            limiter: Token bucket shared by every depth request.
            cache_seconds: How long a book is reused for the same pair; 0
                disables the cache.
            sleep: Async sleep used while the bucket refills.

        Raises:
            ValueError: ``client`` carries an ``X-MBX-APIKEY`` default header,
                or ``cache_seconds`` is negative.
        """
        if _API_KEY_HEADER in client.headers:
            raise ValueError(
                "BinanceDepthSnapshotAdapter is public-only: client must not carry an API key"
            )
        if cache_seconds < 0:
            raise ValueError(f"cache_seconds must be >= 0, got {cache_seconds}")
        self._url = f"{config.base_url}{_DEPTH_PATH}"
        self._clock = clock
        self._client = client
        self._limiter = limiter
        self._ttl = timedelta(seconds=cache_seconds)
        self._sleep = sleep
        self._cache: dict[Pair, Orderbook] = {}
        self._newest: dict[Pair, tuple[int, datetime]] = {}

    @classmethod
    def create(cls, config: MarketDataConfig, clock: ClockPort) -> BinanceDepthSnapshotAdapter:
        """Build an adapter with its own client and token bucket from ``config``.

        Args:
            config: Market-data configuration.
            clock: Time source.

        Returns:
            An adapter owning its :class:`httpx.AsyncClient`.
        """
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

    async def snapshot(self, pair: Pair) -> Orderbook:
        """Return the current book of ``pair``.

        Args:
            pair: Trading pair.

        Returns:
            The book, stamped with the clock when the response arrived (or the
            cached book, when it is younger than ``cache_seconds``). A book
            whose ``lastUpdateId`` is not newer than one already seen keeps
            the stamp of the response that first carried that id.

        Raises:
            NetworkError: The request did not complete.
            ExchangeError: HTTP error status or a body that is not a valid book.
        """
        cached = self._cache.get(pair)
        if cached is not None and self._clock.now() - cached.snapshot_time < self._ttl:
            return cached
        while not self._limiter.try_acquire(_DEPTH_WEIGHT):
            await self._sleep(_DEPTH_WEIGHT / self._limiter.refill_per_second)
        params = {"symbol": binance_symbol(pair), "limit": str(DEPTH_LEVELS)}
        try:
            response = await self._client.get(self._url, params=params)
        except httpx.HTTPError as exc:
            raise NetworkError(f"transport failure calling {_DEPTH_PATH}: {exc!r}") from exc
        received = self._clock.now()
        if response.status_code >= _HTTP_ERROR_MIN:
            raise ExchangeError(
                f"HTTP {response.status_code} from {_DEPTH_PATH}: "
                f"{response.text[:_ERROR_EXCERPT_CHARS]}"
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise ExchangeError(f"{_DEPTH_PATH} returned a non-JSON body") from exc
        if not isinstance(body, dict):
            raise ExchangeError(f"{_DEPTH_PATH} returned a non-object body")
        try:
            book = Orderbook(
                pair=pair,
                bids=_levels(body.get("bids"), side="bids"),
                asks=_levels(body.get("asks"), side="asks"),
                snapshot_time=self._stamp(pair, _update_id(body), received),
            )
        except ValueError as exc:
            raise ExchangeError(f"{_DEPTH_PATH} returned an invalid book: {exc}") from exc
        self._cache[pair] = book
        return book

    def _stamp(self, pair: Pair, update_id: int, received: datetime) -> datetime:
        """Return when the book carrying ``update_id`` was first seen."""
        newest = self._newest.get(pair)
        if newest is None or update_id > newest[0]:
            self._newest[pair] = (update_id, received)
            return received
        _log.warning(
            "depth.book_not_moving",
            pair=str(pair),
            last_update_id=update_id,
            newest_update_id=newest[0],
            first_seen=newest[1].isoformat(),
        )
        return newest[1]

    async def aclose(self) -> None:
        """Close the owned HTTP client."""
        await self._client.aclose()
