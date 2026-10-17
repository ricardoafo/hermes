"""Binance testnet historical-klines adapter.

Implements :class:`hermes.ports.klines.KlinesFetcherPort` against the
public klines endpoint at ``https://testnet.binance.vision/api/v3/klines``.

Design decisions
----------------

- **Testnet only** (per ``docs/decisions/2026-08-23-binance-testnet-klines.md``):
  the endpoint is public, requires no API key, and AGENTS.md prohibition
  #7 blocks any mainnet code path in dev.
- **HTTP client**: raw :class:`httpx.Client` — same pattern as the
  :class:`~hermes.adapters.notifier.TelegramAdapter` (see
  ``src/hermes/adapters/notifier/telegram_adapter.py``). Sync, matches
  the port shape.
- **Rate limiter**: token-bucket private to the exchanges adapter package
  (:class:`hermes.adapters.exchanges.rate_limiter.RateLimiter`), pre-sized
  to 5000 weight/min — well below Binance's published 6000 cap (ADR-003
  §04, "buffer below Binance's 6000/min"). Klines is weight 1 per
  request; the buffer covers accidental co-tenancy on the request pool.
- **Pagination**: Binance klines caps at 1000 candles per request. The
  adapter walks the window by advancing ``startTime`` after each batch;
  the loop terminates when the exchange returns an empty batch OR when
  the last candle's ``open_time`` is >= ``until``.
- **429 semantics**: raise
  :class:`~hermes.domain.errors.NetworkError` immediately. We do NOT
  retry on 429 — the rate limiter's whole job is to prevent this from
  happening, so a 429 is a signal the limiter is misconfigured or a
  co-tenant is eating our budget. Retrying would compound the pressure.
  The caller sees the error and can back off deliberately.
- **Errors**: transport failures wrap into :class:`NetworkError`; HTTP
  non-2xx (including 429) and malformed schemas raise
  :class:`~hermes.domain.errors.ExchangeError`. Both are
  :class:`~hermes.domain.errors.InfraError` subclasses so callers can
  use one ``except InfraError`` if they want to.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Final

import httpx
import structlog

from hermes.adapters.exchanges.rate_limiter import RateLimitConfig, RateLimiter
from hermes.domain.errors import ExchangeError, NetworkError
from hermes.domain.market_data.bar_schedule import close_time_of, previous_boundary
from hermes.domain.market_data.kline import Kline, KlineInterval
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.price import Price
from hermes.domain.value_objects.quantity import Quantity

__all__ = ["BinanceKlinesAdapter", "BinanceKlinesConfig"]


_TESTNET_BASE_URL: Final[str] = "https://testnet.binance.vision"
_KLINES_PATH: Final[str] = "/api/v3/klines"
# Binance klines max page size (spot API v3). Values above this are silently
# clamped by the exchange; we send exactly 1000 to minimise round trips.
_MAX_PAGE_SIZE: Final[int] = 1000
_DEFAULT_TIMEOUT_SEC: Final[float] = 10.0
_HTTP_ERROR_THRESHOLD: Final[int] = 400
# Response-body excerpt cap when constructing error messages — keeps
# the exception readable even when the exchange dumps HTML on 5xx.
_ERROR_EXCERPT_MAX_CHARS: Final[int] = 200
# Weight-per-request for the public klines endpoint (Binance docs).
# https://binance-docs.github.io/apidocs/spot/en/#kline-candlestick-data
_KLINES_REQUEST_WEIGHT: Final[int] = 1
# Default bucket: 5000 weight / 60s — leaves 1000 weight/min headroom
# under Binance's public cap for accidental co-tenants.
_DEFAULT_RATE_LIMIT: Final[RateLimitConfig] = RateLimitConfig(max_weight=5000, window_seconds=60)
# Milliseconds per second — Binance timestamps are ms-since-epoch UTC.
_MS_PER_SECOND: Final[int] = 1000
# Number of positional fields the exchange returns per candle. Anything
# else is a schema break we refuse to silently paper over.
_MIN_KLINE_FIELDS: Final[int] = 7
_EPOCH: Final[datetime] = datetime(1970, 1, 1, tzinfo=UTC)

_log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class BinanceKlinesConfig:
    """Wire-level configuration for :class:`BinanceKlinesAdapter`.

    Attributes:
        base_url: HTTPS root. Defaults to Binance testnet; overriding to
            mainnet is a deliberate act blocked in dev by AGENTS.md #7.
        rate_limit: Sizing for the internal token-bucket limiter.
        timeout_sec: Per-request timeout in seconds.
    """

    base_url: str = _TESTNET_BASE_URL
    rate_limit: RateLimitConfig = _DEFAULT_RATE_LIMIT
    timeout_sec: float = _DEFAULT_TIMEOUT_SEC

    def __post_init__(self) -> None:
        """Validate the URL scheme and timeout — cheap fail-fast in the config layer."""
        if not self.base_url.startswith("https://"):
            raise ValueError(
                f"BinanceKlinesConfig.base_url must start with https://, got {self.base_url!r}"
            )
        if self.timeout_sec <= 0:
            raise ValueError(f"BinanceKlinesConfig.timeout_sec must be > 0, got {self.timeout_sec}")


@dataclass(slots=True)
class BinanceKlinesAdapter:
    """Concrete :class:`~hermes.ports.klines.KlinesFetcherPort` for Binance testnet.

    Attributes:
        config: Wire settings.
        client: Injected :class:`httpx.Client`. Tests supply a
            :class:`httpx.MockTransport` (or ``respx`` mock) — production
            wiring uses :meth:`BinanceKlinesAdapter.create`.
        limiter: Token-bucket rate limiter. Adapter owns it; callers do
            not touch it.
    """

    config: BinanceKlinesConfig
    client: httpx.Client = field()
    limiter: RateLimiter = field()

    @classmethod
    def create(
        cls,
        config: BinanceKlinesConfig | None = None,
    ) -> BinanceKlinesAdapter:
        """Build an adapter with a default :class:`httpx.Client` and limiter.

        Args:
            config: Optional wire config. When ``None`` the testnet
                defaults are used (safe for dev — no mainnet reads).

        Returns:
            A new adapter owning its :class:`httpx.Client` and
            :class:`RateLimiter`.
        """
        cfg = config or BinanceKlinesConfig()
        client = httpx.Client(base_url=cfg.base_url, timeout=cfg.timeout_sec)
        limiter = RateLimiter(config=cfg.rate_limit)
        return cls(config=cfg, client=client, limiter=limiter)

    def close(self) -> None:
        """Release the underlying :class:`httpx.Client` resources.

        Safe to call multiple times.
        """
        self.client.close()

    def fetch(
        self,
        *,
        pair: Pair,
        interval: KlineInterval,
        since: datetime,
        until: datetime,
    ) -> Iterator[Kline]:
        """Yield candles for ``pair`` at ``interval`` in the ``[since, until)`` window.

        Handles pagination transparently: each iteration consumes one
        rate-limit token, hits the klines endpoint, and yields the
        candles in the batch until the window is exhausted.

        Args:
            pair: Trading pair.
            interval: Candle bucket.
            since: Inclusive lower bound (UTC-aware).
            until: Exclusive upper bound (UTC-aware). Must be > ``since``.

        Yields:
            :class:`Kline` values in ascending ``open_time`` order.

        Raises:
            ValueError: On naive datetimes or ``until <= since``.
            hermes.domain.errors.NetworkError: On transport-level failure.
            hermes.domain.errors.ExchangeError: On HTTP non-2xx (including
                429), malformed schema, or unparseable numeric fields.
        """
        _validate_window(since, until)
        symbol = _pair_to_binance_symbol(pair)
        cursor_ms = _to_epoch_ms(since)
        end_ms = _to_epoch_ms(until)

        while cursor_ms < end_ms:
            prev_cursor_ms = cursor_ms
            self.limiter.acquire(_KLINES_REQUEST_WEIGHT)
            batch = self._get_page(
                symbol=symbol,
                interval=interval,
                start_time_ms=cursor_ms,
                end_time_ms=end_ms,
            )
            if not batch:
                return
            last_open_ms: int | None = None
            for row in batch:
                kline = _row_to_kline(row=row, pair=pair, interval=interval)
                kline_open_ms = _to_epoch_ms(kline.open_time)
                # Skip stale rows: the exchange occasionally returns a
                # row at or below our requested startTime (dedup/retransmit).
                # Yielding it would either duplicate a prior yield or land
                # us right back at the same cursor.
                if kline_open_ms < prev_cursor_ms:
                    continue
                if kline.open_time >= until:
                    return
                yield kline
                last_open_ms = kline_open_ms
            if last_open_ms is None:
                # Batch existed but every row was past the window — done.
                return
            # Advance cursor past the last-seen open_time. Binance uses
            # inclusive start_time semantics so +1ms is the safe next
            # boundary regardless of interval.
            cursor_ms = last_open_ms + 1
            # Defensive termination: if the exchange returned stale data
            # (same last_open_ms as our previous cursor), advancing by 1ms
            # still lands us at the same cursor. Break instead of looping
            # forever. In production this catches exchange dedup/retransmit
            # bugs; in tests it catches single-mock responses that would
            # otherwise hang the fetch generator.
            if cursor_ms <= prev_cursor_ms:
                return

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_page(
        self,
        *,
        symbol: str,
        interval: KlineInterval,
        start_time_ms: int,
        end_time_ms: int,
    ) -> list[list[object]]:
        """Fetch one page of raw klines from the exchange.

        Args:
            symbol: Binance-style concatenated pair (e.g. ``"BTCUSDT"``).
            interval: Candle bucket.
            start_time_ms: Inclusive start in ms-since-epoch.
            end_time_ms: Exclusive end in ms-since-epoch.

        Returns:
            The parsed JSON body — a list of list-of-fields rows.

        Raises:
            hermes.domain.errors.NetworkError: On transport-level failure.
            hermes.domain.errors.ExchangeError: On HTTP non-2xx or a
                non-list response body.
        """
        params = {
            "symbol": symbol,
            "interval": interval.value,
            "startTime": str(start_time_ms),
            "endTime": str(end_time_ms),
            "limit": str(_MAX_PAGE_SIZE),
        }
        try:
            response = self.client.get(_KLINES_PATH, params=params)
        except httpx.HTTPError as exc:
            raise NetworkError(f"transport failure calling klines: {exc!r}") from exc

        if response.status_code >= _HTTP_ERROR_THRESHOLD:
            excerpt = response.text[:_ERROR_EXCERPT_MAX_CHARS]
            raise ExchangeError(f"HTTP {response.status_code} from klines endpoint: {excerpt}")

        try:
            data = response.json()
        except ValueError as exc:
            raise ExchangeError(
                f"non-JSON response from klines endpoint: "
                f"{response.text[:_ERROR_EXCERPT_MAX_CHARS]}"
            ) from exc

        if not isinstance(data, list):
            raise ExchangeError(
                f"klines endpoint returned non-list body: {str(data)[:_ERROR_EXCERPT_MAX_CHARS]}"
            )

        # Cast rows to the expected shape — every element is expected to
        # itself be a list, but we defer per-row validation to
        # :func:`_row_to_kline` so we can attribute the offending row.
        return data


def _validate_window(since: datetime, until: datetime) -> None:
    """Reject naive datetimes and non-strictly-increasing windows.

    Args:
        since: Inclusive lower bound.
        until: Exclusive upper bound.

    Raises:
        ValueError: On naive datetime or ``until <= since``.
    """
    for label, value in (("since", since), ("until", until)):
        if value.tzinfo is None or value.tzinfo.utcoffset(None) != UTC.utcoffset(None):
            raise ValueError(f"BinanceKlinesAdapter.fetch: {label} must be UTC-aware")
    if until <= since:
        raise ValueError(
            f"BinanceKlinesAdapter.fetch: until ({until!r}) must be > since ({since!r})"
        )


def _pair_to_binance_symbol(pair: Pair) -> str:
    """Translate hermes :class:`Pair` to Binance concatenated-symbol form.

    Args:
        pair: Trading pair.

    Returns:
        Uppercase concatenated symbol (e.g. ``"BTCUSDT"``).
    """
    return f"{pair.base.value}{pair.quote.value}"


def _to_epoch_ms(dt: datetime) -> int:
    """Return the ms-since-epoch UTC timestamp for a tz-aware datetime.

    Args:
        dt: UTC-aware datetime (precondition — callers validate first).

    Returns:
        Integer ms since 1970-01-01T00:00:00Z.
    """
    return int(dt.timestamp() * _MS_PER_SECOND)


def _from_epoch_ms(ms: int) -> datetime:
    """Return the tz-aware UTC datetime for a ms-since-epoch value.

    Args:
        ms: Milliseconds since 1970-01-01T00:00:00Z.

    Returns:
        UTC-aware datetime.

    Raises:
        hermes.domain.errors.ExchangeError: ``ms`` is outside the calendar
            ``datetime`` supports.
    """
    try:
        return _EPOCH + timedelta(milliseconds=ms)
    except OverflowError as exc:
        raise ExchangeError(f"klines timestamp out of range: {ms}") from exc


def _decimal_or_raise(raw: object, *, field_name: str) -> Decimal:
    """Coerce a raw JSON field into a :class:`Decimal` or raise :class:`ExchangeError`.

    Args:
        raw: Field value from the exchange response.
        field_name: Descriptive name used in the error message.

    Returns:
        The value as :class:`Decimal`.

    Raises:
        hermes.domain.errors.ExchangeError: On non-string/numeric input
            or a value that :class:`Decimal` refuses.
    """
    if not isinstance(raw, (str, int, float)):
        raise ExchangeError(f"klines row field {field_name!r} not string/numeric: {raw!r}")
    try:
        value = Decimal(str(raw))
    except InvalidOperation as exc:
        raise ExchangeError(f"klines row field {field_name!r} is not a decimal: {raw!r}") from exc
    if not value.is_finite():
        raise ExchangeError(f"klines row field {field_name!r} is not a finite number: {raw!r}")
    return value


def _row_to_kline(
    *,
    row: object,
    pair: Pair,
    interval: KlineInterval,
) -> Kline:
    """Convert one raw Binance klines row into a domain :class:`Kline`.

    Binance returns each candle as a positional list. Indices we use:
    0=open_time_ms, 1=open, 2=high, 3=low, 4=close, 5=volume,
    6=close_time_ms. Higher indices (quote_volume, trades, etc.) are
    dropped — they are not part of the domain Kline.

    Args:
        row: One element of the response array. Must itself be a list
            with at least :data:`_MIN_KLINE_FIELDS` elements.
        pair: Pair the candle belongs to — used for the embedded
            :class:`Price` and volume :class:`Quantity` fields.
        interval: Candle bucket.

    Returns:
        A validated :class:`Kline`.

    Raises:
        hermes.domain.errors.ExchangeError: On a non-list row, too few
            fields, unparseable numeric fields, an ``open_time`` off the
            interval grid, a ``close_time`` that is not ``open + interval -
            1 ms``, or a price that is not positive.
    """
    if not isinstance(row, list) or len(row) < _MIN_KLINE_FIELDS:
        raise ExchangeError(f"klines row must be list of ≥{_MIN_KLINE_FIELDS} fields, got {row!r}")

    open_time_ms = row[0]
    close_time_ms = row[6]
    if (
        not isinstance(open_time_ms, int)
        or isinstance(open_time_ms, bool)
        or not isinstance(close_time_ms, int)
        or isinstance(close_time_ms, bool)
    ):
        raise ExchangeError(
            f"klines row timestamps must be ints, got open={open_time_ms!r}, "
            f"close={close_time_ms!r}"
        )
    open_time = _from_epoch_ms(open_time_ms)
    close_time = _from_epoch_ms(close_time_ms)
    if previous_boundary(open_time, interval) != open_time:
        raise ExchangeError(f"klines open_time {open_time.isoformat()} is not {interval} aligned")
    if close_time != close_time_of(open_time, interval):
        raise ExchangeError(
            f"klines close_time {close_time.isoformat()} is not open + {interval} - 1 ms"
        )

    open_dec = _decimal_or_raise(row[1], field_name="open")
    high_dec = _decimal_or_raise(row[2], field_name="high")
    low_dec = _decimal_or_raise(row[3], field_name="low")
    close_dec = _decimal_or_raise(row[4], field_name="close")
    volume_dec = _decimal_or_raise(row[5], field_name="volume")
    if low_dec <= 0:
        raise ExchangeError(f"klines row low price is not positive: {row[3]!r}")

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
        # Domain validation failed — the exchange returned self-inconsistent
        # OHLC data. Surface as an ExchangeError so callers can attribute it
        # to the wire, not to a bug in our domain model.
        raise ExchangeError(f"klines row fails domain invariants: {exc}") from exc
