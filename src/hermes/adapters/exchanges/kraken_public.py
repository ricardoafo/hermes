"""Kraken read-only adapter — public endpoints only, no auth required.

Implements three narrow slices of Kraken's public REST API against
``https://api.kraken.com/0/public/``:

- :meth:`KrakenPublicAdapter.fetch_klines` — the ``OHLC`` endpoint,
  yielding :class:`~hermes.domain.market_data.kline.Kline` values.
  Satisfies :class:`~hermes.ports.klines.KlinesFetcherPort`.
- :meth:`KrakenPublicAdapter.get_orderbook` — the ``Depth`` endpoint,
  returning an :class:`~hermes.domain.market_data.orderbook.Orderbook`
  snapshot for slippage modeling.
- :meth:`KrakenPublicAdapter.get_ticker` — the ``Ticker`` endpoint,
  returning a :class:`~hermes.domain.market_data.ticker.Ticker` for
  cross-venue price comparison.
- :meth:`KrakenPublicAdapter.get_usd_price` — the same endpoint for a
  stablecoin's USD book, the price the depeg rules RO-05 and RO-06 read
  before real money (pre-registration §5.5, #1158).

Design decisions
----------------

- **Public-only** (per ``docs/decisions/2026-08-24-kraken-public-adapter.md``):
  the endpoints require no API key, no signature, no nonce. MVP1 scope
  is commissions + backtest source + cross-venue price sanity; signed
  trading endpoints are deliberately out of scope until MVP2 Kraken
  trading (adapter shape ready — private methods slot in without
  reshaping this class).
- **HTTP client**: raw :class:`httpx.Client` — same pattern as
  :class:`~hermes.adapters.exchanges.binance_klines.BinanceKlinesAdapter`
  and :class:`~hermes.adapters.fx.frankfurter.FrankfurterAdapter`.
- **Rate limiter**: owned token-bucket sized to Kraken's published
  public-tier cap of 1 call/second per IP. Every HTTP adapter shares the
  limiter code (:mod:`hermes.adapters._shared.rate_limiter`).
- **Kraken response envelope**: every response is
  ``{"error": [...], "result": {...}}``. Non-empty ``error`` MUST be
  raised as :class:`~hermes.domain.errors.KrakenPublicError`; the raw
  Kraken strings are preserved in the exception message for triage.
- **Pair-symbol translation**: Kraken uses legacy X/Z prefixes (BTC →
  XXBT, USD → ZUSD; USDT is untranslated). The mapping lives in
  :func:`_pair_to_kraken_symbol` — extendable per pair, defaults to the
  raw ``BASEQUOTE`` concatenation for pairs not in the override table.
- **Market data: never cached**. Klines, ticker, and orderbook always
  hit the wire. The backtest use case is deterministic replay from
  fetched-once data; the price-validation use case needs freshness.

Reference: docs/decisions/2026-08-24-kraken-public-adapter.md,
https://docs.kraken.com/rest/#tag/Market-Data.
"""

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Final

import httpx
import structlog

from hermes.adapters._shared.rate_limiter import RateLimitConfig, RateLimiter
from hermes.adapters._shared.wire import (
    validate_http_config,
    validate_utc_window,
    wire_decimal,
)
from hermes.domain.errors import KrakenPublicError
from hermes.domain.market_data.bar_schedule import previous_boundary
from hermes.domain.market_data.kline import Kline, KlineInterval
from hermes.domain.market_data.orderbook import Orderbook, OrderbookLevel
from hermes.domain.market_data.ticker import Ticker
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.price import Price
from hermes.domain.value_objects.quantity import Quantity

__all__ = ["KrakenPublicAdapter", "KrakenPublicConfig"]


_DEFAULT_BASE_URL: Final[str] = "https://api.kraken.com"
_OHLC_PATH: Final[str] = "/0/public/OHLC"
_DEPTH_PATH: Final[str] = "/0/public/Depth"
_TICKER_PATH: Final[str] = "/0/public/Ticker"
_DEFAULT_TIMEOUT_SEC: Final[float] = 10.0
_HTTP_ERROR_THRESHOLD: Final[int] = 400
# Response-body excerpt cap when building error messages — keeps
# the exception readable even if Kraken dumps HTML on 5xx.
_ERROR_EXCERPT_MAX_CHARS: Final[int] = 200
# Weight per public-endpoint call. Kraken publishes 1 call/second per IP
# for the public tier — every call is one token in our bucket.
_REQUEST_WEIGHT: Final[int] = 1
_DEFAULT_RATE_LIMIT: Final[RateLimitConfig] = RateLimitConfig(max_weight=1, window_seconds=1)
# Seconds-per-minute conversion. Kraken's OHLC interval query parameter
# is expressed in minutes (1, 5, 15, ...).
_SECONDS_PER_MINUTE: Final[int] = 60
# Minimum row lengths — the depth-level rows are [price, quantity, ts?]
# tuples; the OHLC rows are 8-position lists. Constants keep the numeric
# comparisons out of the PLR2004 (magic-value) bucket and self-document
# the wire shape.
_MIN_DEPTH_ROW_FIELDS: Final[int] = 2
_MIN_OHLC_ROW_FIELDS: Final[int] = 7

# Kraken's altname / wsname system uses XBT everywhere; the older asset
# codes prefix X for cryptocurrencies and Z for fiats (BTC → XXBT,
# USD → ZUSD). USDT / USDC (relatively new listings) are NOT prefixed.
# Rather than hard-code every possible currency, we translate the
# well-known cases and fall back to raw concatenation for the rest —
# callers should verify a specific pair's symbol against Kraken's
# AssetPairs response if unsure.
_PAIR_OVERRIDES: Final[dict[str, str]] = {
    # BTC family
    "BTC/USD": "XXBTZUSD",
    "BTC/USDT": "XBTUSDT",
    "BTC/USDC": "XBTUSDC",
    "BTC/EUR": "XXBTZEUR",
    # ETH family
    "ETH/USD": "XETHZUSD",
    # Kraken lists the stablecoin quotes under the plain altname; the
    # prefixed XETHZUSDT / XETHZUSDC forms answer "Unknown asset pair" (#31).
    "ETH/USDT": "ETHUSDT",
    "ETH/USDC": "ETHUSDC",
    "ETH/EUR": "XETHZEUR",
    # SOL family — Kraken lists these without the X/Z prefixes
    "SOL/USD": "SOLUSD",
    "SOL/USDT": "SOLUSDT",
    "SOL/USDC": "SOLUSDC",
    "SOL/EUR": "SOLEUR",
}

# The USD books of the stablecoins, keyed as Kraken answers them: asked for
# the plain ``USDTUSD`` altname, Kraken answers under ``USDTZUSD``. USD is no
# hermes Currency (nothing trades against it), so these sit outside the Pair map.
_USD_SYMBOLS: Final[dict[Currency, str]] = {
    Currency.USDT: "USDTZUSD",
    Currency.USDC: "USDCUSD",
}


_log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class KrakenPublicConfig:
    """Wire-level configuration for :class:`KrakenPublicAdapter`.

    Attributes:
        base_url: HTTPS root of the Kraken API. Defaults to the public
            endpoint. Override for a Kraken sandbox / staging mirror.
        rate_limit: Sizing for the internal token-bucket limiter. Default
            is 1 call/second — Kraken's published public-tier cap.
        timeout_sec: Per-request timeout in seconds.
    """

    base_url: str = _DEFAULT_BASE_URL
    rate_limit: RateLimitConfig = _DEFAULT_RATE_LIMIT
    timeout_sec: float = _DEFAULT_TIMEOUT_SEC

    def __post_init__(self) -> None:
        """Validate URL scheme and timeout — cheap fail-fast at config layer.

        Raises:
            ValueError: On non-https ``base_url`` or non-positive ``timeout_sec``.
        """
        validate_http_config("KrakenPublicConfig", url=self.base_url, timeout_sec=self.timeout_sec)


@dataclass(slots=True)
class KrakenPublicAdapter:
    """Read-only Kraken adapter — public endpoints (no auth) for MVP1.

    Attributes:
        config: Wire settings.
        client: Injected :class:`httpx.Client`. Tests supply a
            :class:`httpx.MockTransport` (or ``respx`` mock) — production
            wiring uses :meth:`KrakenPublicAdapter.create`.
        limiter: Token-bucket rate limiter. Adapter owns it; callers do
            not touch it.
    """

    config: KrakenPublicConfig
    client: httpx.Client = field()
    limiter: RateLimiter = field()

    @classmethod
    def create(cls, config: KrakenPublicConfig | None = None) -> KrakenPublicAdapter:
        """Build an adapter with a default :class:`httpx.Client` and limiter.

        Args:
            config: Optional wire config. When ``None`` the public
                Kraken defaults are used (safe — no key required).

        Returns:
            A new adapter owning its :class:`httpx.Client` and
            :class:`RateLimiter`.
        """
        cfg = config or KrakenPublicConfig()
        client = httpx.Client(base_url=cfg.base_url, timeout=cfg.timeout_sec)
        limiter = RateLimiter(config=cfg.rate_limit)
        return cls(config=cfg, client=client, limiter=limiter)

    def close(self) -> None:
        """Release the underlying :class:`httpx.Client` resources.

        Safe to call multiple times.
        """
        self.client.close()

    # ------------------------------------------------------------------
    # Public port methods
    # ------------------------------------------------------------------

    def fetch_klines(
        self,
        *,
        pair: Pair,
        interval: KlineInterval,
        since: datetime,
        until: datetime,
    ) -> Iterator[Kline]:
        """Yield candles for ``pair`` at ``interval`` in ``[since, until)``.

        Hits ``GET /0/public/OHLC?pair=<SYMBOL>&interval=<MINUTES>&since=<UNIX>``.
        Kraken returns up to 720 candles per call — the window is walked
        by advancing the ``since`` cursor to the last-seen candle's open
        time each iteration.

        Args:
            pair: Trading pair.
            interval: Candle bucket.
            since: Inclusive lower bound (UTC-aware).
            until: Exclusive upper bound (UTC-aware). Must be > ``since``.

        Yields:
            :class:`Kline` values in ascending ``open_time`` order.

        Raises:
            ValueError: On naive datetimes or ``until <= since``.
            hermes.domain.errors.KrakenPublicError: On any wire, HTTP,
                envelope-error, or schema failure.
        """
        validate_utc_window(since, until, caller="KrakenPublicAdapter.fetch_klines")
        symbol = _pair_to_kraken_symbol(pair)
        interval_minutes = _interval_to_minutes(interval)
        cursor_s = int(since.timestamp())
        end_s = int(until.timestamp())

        while cursor_s < end_s:
            prev_cursor_s = cursor_s
            result = self._get_result(
                path=_OHLC_PATH,
                params={
                    "pair": symbol,
                    "interval": str(interval_minutes),
                    "since": str(cursor_s),
                },
            )
            rows = _extract_ohlc_rows(result=result, symbol=symbol)
            if not rows:
                return
            last_open_s: int | None = None
            for row in rows:
                kline = _row_to_kline(row=row, pair=pair, interval=interval)
                open_s = int(kline.open_time.timestamp())
                if open_s < prev_cursor_s:
                    # Kraken occasionally returns a row at or below the
                    # requested since (dedup/retransmit). Skip it — yielding
                    # would either duplicate or land us at the same cursor.
                    continue
                if kline.open_time >= until:
                    return
                yield kline
                last_open_s = open_s
            if last_open_s is None:
                return
            cursor_s = last_open_s + _SECONDS_PER_MINUTE * interval_minutes
            if cursor_s <= prev_cursor_s:
                # Defensive termination: exchange returned no progress.
                # Break rather than loop forever on a broken pagination.
                return

    def get_orderbook(self, pair: Pair, *, depth: int = 10) -> Orderbook:
        """Return the current depth snapshot for ``pair``.

        Hits ``GET /0/public/Depth?pair=<SYMBOL>&count=<DEPTH>``.

        Args:
            pair: Trading pair.
            depth: Number of levels per side to request. Kraken accepts
                any positive integer up to its own internal cap; the
                default of 10 covers top-of-book slippage models.

        Returns:
            An :class:`Orderbook` snapshot stamped with
            ``datetime.now(UTC)`` as its ``snapshot_time``.

        Raises:
            ValueError: On non-positive ``depth``.
            hermes.domain.errors.KrakenPublicError: On any wire, HTTP,
                envelope-error, or schema failure.
        """
        if depth <= 0:
            raise ValueError(f"KrakenPublicAdapter.get_orderbook: depth must be > 0, got {depth}")
        symbol = _pair_to_kraken_symbol(pair)
        result = self._get_result(
            path=_DEPTH_PATH,
            params={"pair": symbol, "count": str(depth)},
        )
        return _parse_depth_response(result=result, pair=pair, symbol=symbol)

    def get_ticker(self, pair: Pair) -> Ticker:
        """Return the current BBO + last-trade snapshot for ``pair``.

        Hits ``GET /0/public/Ticker?pair=<SYMBOL>``.

        Args:
            pair: Trading pair.

        Returns:
            A :class:`Ticker` stamped with ``datetime.now(UTC)`` as its
            ``snapshot_time``.

        Raises:
            hermes.domain.errors.KrakenPublicError: On any wire, HTTP,
                envelope-error, or schema failure.
        """
        symbol = _pair_to_kraken_symbol(pair)
        result = self._get_result(path=_TICKER_PATH, params={"pair": symbol})
        return _parse_ticker_response(result=result, pair=pair, symbol=symbol)

    def get_usd_price(self, coin: Currency) -> Decimal:
        """Return the mid of ``coin``'s USD book.

        Hits ``GET /0/public/Ticker?pair=<COIN>USD``. The mid of the best bid
        and ask is read, not the last trade: a thin book's last trade can be
        minutes old.

        Args:
            coin: A stablecoin with a Kraken USD book (USDT or USDC).

        Returns:
            ``(best_bid + best_ask) / 2`` in USD.

        Raises:
            ValueError: ``coin`` has no USD book here.
            hermes.domain.errors.KrakenPublicError: On any wire, HTTP,
                envelope-error or schema failure, or a crossed book.
        """
        symbol = _USD_SYMBOLS.get(coin)
        if symbol is None:
            raise ValueError(f"no Kraken USD book for {coin.value}")
        result = self._get_result(path=_TICKER_PATH, params={"pair": symbol})
        payload = _lookup_pair_payload(result=result, symbol=symbol)
        ask = _ticker_first_price(payload=payload, key="a", symbol=symbol)
        bid = _ticker_first_price(payload=payload, key="b", symbol=symbol)
        if bid <= 0 or ask < bid:
            raise KrakenPublicError(
                f"Kraken {symbol} book is crossed or empty: bid={bid} ask={ask}"
            )
        return (bid + ask) / 2

    # ------------------------------------------------------------------
    # HTTP helper
    # ------------------------------------------------------------------

    def _get_result(self, *, path: str, params: dict[str, str]) -> dict[str, Any]:
        """Fetch, unwrap the Kraken envelope, and return the ``result`` mapping.

        Args:
            path: URL path (e.g. ``"/0/public/AssetPairs"``).
            params: Query parameters.

        Returns:
            The ``result`` mapping from the Kraken response envelope.

        Raises:
            hermes.domain.errors.KrakenPublicError: On transport failure,
                HTTP non-2xx, non-JSON body, non-dict root, non-empty
                ``error`` array, or missing / non-dict ``result``.
        """
        self.limiter.acquire(_REQUEST_WEIGHT)
        try:
            response = self.client.get(path, params=params)
        except httpx.HTTPError as exc:
            raise KrakenPublicError(f"transport failure calling Kraken {path!r}: {exc!r}") from exc

        if response.status_code >= _HTTP_ERROR_THRESHOLD:
            excerpt = response.text[:_ERROR_EXCERPT_MAX_CHARS]
            raise KrakenPublicError(f"HTTP {response.status_code} from Kraken {path!r}: {excerpt}")

        try:
            data = response.json()
        except ValueError as exc:
            raise KrakenPublicError(
                f"non-JSON response from Kraken {path!r}: "
                f"{response.text[:_ERROR_EXCERPT_MAX_CHARS]}"
            ) from exc

        if not isinstance(data, dict):
            raise KrakenPublicError(
                f"Kraken {path!r} returned non-object body: {str(data)[:_ERROR_EXCERPT_MAX_CHARS]}"
            )

        errors = data.get("error", [])
        if not isinstance(errors, list):
            raise KrakenPublicError(f"Kraken {path!r} 'error' field must be a list, got {errors!r}")
        if errors:
            raise KrakenPublicError(f"Kraken {path!r} returned errors: {errors!r}")

        result = data.get("result")
        if not isinstance(result, dict):
            raise KrakenPublicError(
                f"Kraken {path!r} missing or non-object 'result': "
                f"{str(data)[:_ERROR_EXCERPT_MAX_CHARS]}"
            )
        return result


# ---------------------------------------------------------------------------
# Pair-symbol translation
# ---------------------------------------------------------------------------


def _pair_to_kraken_symbol(pair: Pair) -> str:
    """Translate a hermes :class:`Pair` to Kraken's altname string.

    Kraken's legacy asset codes prefix cryptocurrencies with ``X`` and
    fiats with ``Z`` — ``BTC/USD`` is ``XXBTZUSD``, ``ETH/USD`` is
    ``XETHZUSD``. Newer listings (SOL, USDT, USDC) are not prefixed.

    The mapping is a lookup on the canonical ``BASE/QUOTE`` form; pairs
    not in :data:`_PAIR_OVERRIDES` fall back to raw ``BASEQUOTE``
    concatenation, which works for many modern listings but not the
    prefixed legacy ones.

    Args:
        pair: Trading pair.

    Returns:
        Kraken's altname / concatenated-symbol form.
    """
    canonical = f"{pair.base.value}/{pair.quote.value}"
    override = _PAIR_OVERRIDES.get(canonical)
    if override is not None:
        return override
    return f"{pair.base.value}{pair.quote.value}"


# ---------------------------------------------------------------------------
# Response parsers
# ---------------------------------------------------------------------------


def _lookup_pair_payload(*, result: dict[str, Any], symbol: str) -> dict[str, Any]:
    """Return ``result[symbol]`` or attempt a case-insensitive fallback.

    Kraken sometimes echoes back the pair under a NORMALISED altname that
    differs from what we sent (e.g. asked for ``XBTUSDT``, got back
    ``XBTUSDT`` or the internal wsname ``XBT/USDT``). If the direct
    lookup misses, we search the dict for any single entry that matches
    case-insensitively — Kraken's response never returns more than one
    pair when we asked for one.

    Args:
        result: The Kraken ``result`` mapping.
        symbol: The Kraken symbol we asked for.

    Returns:
        The pair-scoped payload dict.

    Raises:
        hermes.domain.errors.KrakenPublicError: On missing pair, ambiguous
            match, or non-dict payload.
    """
    payload = result.get(symbol)
    if payload is None:
        # Case-insensitive fallback: some Kraken endpoints return the
        # wsname or a differently-cased altname.
        matches = [v for k, v in result.items() if k.upper() == symbol.upper()]
        if len(matches) == 1:
            payload = matches[0]
        else:
            raise KrakenPublicError(
                f"Kraken response has no entry for pair {symbol!r}; "
                f"available: {sorted(result.keys())}"
            )
    if not isinstance(payload, dict):
        raise KrakenPublicError(
            f"Kraken response for pair {symbol!r} is not an object: {payload!r}"
        )
    return payload


def _parse_depth_response(
    *,
    result: dict[str, Any],
    pair: Pair,
    symbol: str,
) -> Orderbook:
    """Parse the ``Depth`` response into an :class:`Orderbook` snapshot.

    Kraken's payload::

        {"XXBTZUSD": {
            "asks": [["50100.0", "1.5", 1698765432], ...],
            "bids": [["50000.0", "2.0", 1698765431], ...],
        }}

    Each level is ``[price, quantity, timestamp]`` — we only read price
    and quantity, discard the per-level timestamp (Orderbook has a single
    snapshot_time set at parse time).

    Args:
        result: Kraken's ``result`` mapping.
        pair: Domain :class:`Pair`.
        symbol: Kraken symbol requested (used to attribute errors).

    Returns:
        An :class:`Orderbook` stamped with ``datetime.now(UTC)``.

    Raises:
        hermes.domain.errors.KrakenPublicError: On missing pair, missing
            ``asks``/``bids`` arrays, or unparseable levels.
    """
    pair_payload = _lookup_pair_payload(result=result, symbol=symbol)
    bids = _parse_depth_side(payload=pair_payload, key="bids", symbol=symbol)
    asks = _parse_depth_side(payload=pair_payload, key="asks", symbol=symbol)
    try:
        return Orderbook(
            pair=pair,
            bids=bids,
            asks=asks,
            snapshot_time=datetime.now(UTC),
        )
    except ValueError as exc:
        raise KrakenPublicError(
            f"Kraken depth for {symbol!r} fails Orderbook invariants: {exc}"
        ) from exc


def _parse_depth_side(
    *,
    payload: dict[str, Any],
    key: str,
    symbol: str,
) -> tuple[OrderbookLevel, ...]:
    """Parse ``payload[key]`` (a list of price/qty/ts triples) into level tuples."""
    raw = payload.get(key)
    if not isinstance(raw, list):
        raise KrakenPublicError(
            f"Kraken depth for {symbol!r} missing or non-list {key!r}: {payload!r}"
        )
    levels: list[OrderbookLevel] = []
    for entry in raw:
        if not isinstance(entry, list) or len(entry) < _MIN_DEPTH_ROW_FIELDS:
            raise KrakenPublicError(f"Kraken depth {key!r} row malformed for {symbol!r}: {entry!r}")
        price = _decimal_or_raise(entry[0], field_name=f"{symbol} depth {key} price")
        qty = _decimal_or_raise(entry[1], field_name=f"{symbol} depth {key} quantity")
        try:
            levels.append(OrderbookLevel(price=price, quantity=qty))
        except ValueError as exc:
            raise KrakenPublicError(
                f"Kraken depth {key!r} level fails invariants for {symbol!r}: {exc}"
            ) from exc
    # Kraken's depth response ships bids DESCENDING and asks ASCENDING —
    # exactly what Orderbook requires. Preserve wire order verbatim.
    return tuple(levels)


def _parse_ticker_response(
    *,
    result: dict[str, Any],
    pair: Pair,
    symbol: str,
) -> Ticker:
    """Parse the ``Ticker`` response into a :class:`Ticker` snapshot.

    Kraken's payload::

        {"XXBTZUSD": {
            "a": ["50100.0", "1", "1.000"],   # ask array: price, whole-lot, lot
            "b": ["50000.0", "1", "1.000"],   # bid array
            "c": ["50050.0", "0.5"],          # last-trade close: price, volume
            ...
        }}

    We only read ``a[0]`` (best ask), ``b[0]`` (best bid), and ``c[0]``
    (last-trade price).

    Args:
        result: Kraken's ``result`` mapping.
        pair: Domain :class:`Pair`.
        symbol: Kraken symbol requested (used to attribute errors).

    Returns:
        A :class:`Ticker` stamped with ``datetime.now(UTC)``.

    Raises:
        hermes.domain.errors.KrakenPublicError: On missing pair, missing
            fields, or unparseable numbers.
    """
    pair_payload = _lookup_pair_payload(result=result, symbol=symbol)
    ask = _ticker_first_price(payload=pair_payload, key="a", symbol=symbol)
    bid = _ticker_first_price(payload=pair_payload, key="b", symbol=symbol)
    last = _ticker_first_price(payload=pair_payload, key="c", symbol=symbol)
    try:
        return Ticker(
            pair=pair,
            best_bid=bid,
            best_ask=ask,
            last_price=last,
            snapshot_time=datetime.now(UTC),
        )
    except ValueError as exc:
        raise KrakenPublicError(
            f"Kraken ticker for {symbol!r} fails Ticker invariants: {exc}"
        ) from exc


def _ticker_first_price(
    *,
    payload: dict[str, Any],
    key: str,
    symbol: str,
) -> Decimal:
    """Extract ``payload[key][0]`` as a :class:`Decimal` price field."""
    raw = payload.get(key)
    if not isinstance(raw, list) or not raw:
        raise KrakenPublicError(
            f"Kraken ticker for {symbol!r} missing or empty {key!r}: {payload!r}"
        )
    return _decimal_or_raise(raw[0], field_name=f"{symbol} ticker {key}")


# ---------------------------------------------------------------------------
# OHLC row → Kline
# ---------------------------------------------------------------------------


def _extract_ohlc_rows(*, result: dict[str, Any], symbol: str) -> list[Any]:
    """Return the raw OHLC rows for ``symbol`` from the OHLC ``result`` mapping.

    Kraken's OHLC response wraps the array under the pair key plus a
    sibling ``"last"`` field (pagination cursor we intentionally ignore
    — we drive pagination from open-time rather than trusting an
    exchange-supplied cursor).

    Args:
        result: The Kraken ``result`` mapping.
        symbol: Kraken symbol requested.

    Returns:
        The raw rows list (each row itself a positional list).

    Raises:
        hermes.domain.errors.KrakenPublicError: On missing pair key or
            non-list rows payload.
    """
    # Filter out the "last" sibling before looking up the pair. Copying
    # into a fresh dict keeps _lookup_pair_payload's error message clean.
    pair_scoped = {k: v for k, v in result.items() if k != "last"}
    payload = pair_scoped.get(symbol)
    if payload is None:
        matches = [v for k, v in pair_scoped.items() if k.upper() == symbol.upper()]
        if len(matches) == 1:
            payload = matches[0]
        else:
            raise KrakenPublicError(
                f"Kraken OHLC response has no entry for {symbol!r}; "
                f"available: {sorted(pair_scoped.keys())}"
            )
    if not isinstance(payload, list):
        raise KrakenPublicError(
            f"Kraken OHLC rows for {symbol!r} must be a list, got {type(payload).__name__}"
        )
    return payload


def _row_to_kline(*, row: Any, pair: Pair, interval: KlineInterval) -> Kline:
    """Convert one raw Kraken OHLC row into a domain :class:`Kline`.

    Kraken row layout (positional list)::

        [time, open, high, low, close, vwap, volume, count]

    Indices 0-4 give open_time_s, OHLC prices; index 6 is base-asset
    volume; the vwap (5) and trade count (7) are discarded (not part of
    the domain Kline).

    Args:
        row: One Kraken OHLC row.
        pair: Pair the candle belongs to.
        interval: Candle bucket.

    Returns:
        A validated :class:`Kline`.

    Raises:
        hermes.domain.errors.KrakenPublicError: On a non-list row, too
            few fields, unparseable numerics, an ``open_time`` off the
            interval grid, or a price that is not positive.
    """
    if not isinstance(row, list) or len(row) < _MIN_OHLC_ROW_FIELDS:
        raise KrakenPublicError(
            f"Kraken OHLC row must be a list of >={_MIN_OHLC_ROW_FIELDS} fields, got {row!r}"
        )
    open_time_s = row[0]
    if not isinstance(open_time_s, (int, float)) or isinstance(open_time_s, bool):
        raise KrakenPublicError(f"Kraken OHLC open_time must be numeric, got {open_time_s!r}")
    open_dec = _decimal_or_raise(row[1], field_name="Kraken OHLC open")
    high_dec = _decimal_or_raise(row[2], field_name="Kraken OHLC high")
    low_dec = _decimal_or_raise(row[3], field_name="Kraken OHLC low")
    close_dec = _decimal_or_raise(row[4], field_name="Kraken OHLC close")
    volume_dec = _decimal_or_raise(row[6], field_name="Kraken OHLC volume")
    if low_dec <= 0:
        raise KrakenPublicError(f"Kraken OHLC low price is not positive: {row[3]!r}")

    try:
        open_time = datetime.fromtimestamp(int(open_time_s), tz=UTC)
        close_time = datetime.fromtimestamp(
            int(open_time_s) + _SECONDS_PER_MINUTE * _interval_to_minutes(interval), tz=UTC
        )
    except (OverflowError, OSError, ValueError) as exc:
        raise KrakenPublicError(f"Kraken OHLC open_time out of range: {open_time_s!r}") from exc
    if open_time_s != int(open_time_s) or previous_boundary(open_time, interval) != open_time:
        raise KrakenPublicError(
            f"Kraken OHLC open_time {open_time_s!r} is not on the {interval} grid"
        )
    base_asset: Currency = pair.base
    try:
        return Kline(
            pair=pair,
            interval=interval,
            open_time=open_time,
            open=Price(amount=open_dec, pair=pair),
            high=Price(amount=high_dec, pair=pair),
            low=Price(amount=low_dec, pair=pair),
            close=Price(amount=close_dec, pair=pair),
            volume=Quantity(amount=volume_dec, asset=base_asset),
            close_time=close_time,
        )
    except ValueError as exc:
        raise KrakenPublicError(f"Kraken OHLC row fails Kline invariants: {exc}") from exc


def _interval_to_minutes(interval: KlineInterval) -> int:
    """Translate a :class:`KlineInterval` into Kraken's minute-based interval integer.

    Kraken supports 1, 5, 15, 30, 60, 240, 1440, 10080, 21600 minutes.
    Our domain enum currently covers 1m / 5m / 1h / 4h / 1d — a strict
    subset. Extending the enum requires updating this table in the same
    PR.

    Args:
        interval: Domain interval enum.

    Returns:
        Minutes-count int accepted by Kraken's ``interval`` query param.

    Raises:
        hermes.domain.errors.KrakenPublicError: On an interval we have
            not mapped (i.e. a future enum value the adapter has not
            been updated for).
    """
    mapping: dict[KlineInterval, int] = {
        KlineInterval.M1: 1,
        KlineInterval.M5: 5,
        KlineInterval.H1: 60,
        KlineInterval.H4: 240,
        KlineInterval.D1: 1440,
    }
    minutes = mapping.get(interval)
    if minutes is None:
        raise KrakenPublicError(
            f"KrakenPublicAdapter does not support KlineInterval={interval!r} — "
            "extend _interval_to_minutes()."
        )
    return minutes


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _decimal_or_raise(raw: object, *, field_name: str) -> Decimal:
    return wire_decimal(raw, field=f"Kraken field {field_name!r}", error=KrakenPublicError)
