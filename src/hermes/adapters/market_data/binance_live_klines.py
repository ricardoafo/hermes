"""Binance public-klines live closed-bar adapter (REST polling).

Implements :class:`~hermes.ports.bar_feed.ClosedBarFeedPort` (and the
:class:`~hermes.ports.market_data.MarketDataPort` bar channel) by polling
``GET /api/v3/klines`` shortly after each 4h and 1d candle closes.

Design decisions (openspec/changes/307-live-bar-stream/change.md):

- **REST polling, not a WebSocket** (D-1): the strategy evaluates once per 4h
  candle, so sub-second latency buys nothing, and polling needs no connection
  lifecycle and no new dependency. A cursor per stream makes one request also
  return the bars missed during an outage.
- **Market-data host only** (D-2): requests go to the host in
  :class:`~hermes.infrastructure.config.models.MarketDataConfig` (an
  allow-list of one), use GET, and touch only ``/api/v3/klines`` and
  ``/api/v3/time``. There is no key, signature or account code in this module.
- **Closed-bar rule** (D-3): a bar is closed when ``open + interval + settle``
  is at or before the earlier of the local and exchange clocks. The poller
  sleeps through an injected callable, so tests advance a simulated clock.
- **Order** (D-5): :func:`~hermes.domain.market_data.bar_schedule.plan_delivery`
  sorts by close time, longer interval first, then pair.
- **Errors** (D-6): transport errors and 5xx retry three times (100, 200,
  400 ms); 429 and 418 do not retry and the poller waits ``Retry-After``;
  other 4xx and schema errors are permanent (``FAILED``). Exhausted retries
  never raise from the stream: the source goes ``DEGRADED`` and re-polls.
- **Budget** (D-6): klines weigh 2, time weighs 1; a token bucket sized from
  config gates every request without blocking the event loop.

``close_time`` is Binance's inclusive value (``open + interval - 1 ms``), the
one the Vision archives carry, so live and backtest bars compare equal.
"""

from __future__ import annotations

import math
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Final

import anyio
import httpx
import structlog

from hermes.adapters.exchanges.rate_limiter import RateLimitConfig, RateLimiter
from hermes.domain.errors import ExchangeError, InfraError, NetworkError
from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.bar_schedule import (
    Stream,
    close_time_of,
    interval_duration,
    is_closed,
    next_boundary,
    plan_delivery,
    previous_boundary,
)
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.market_data.orderbook import Orderbook
from hermes.domain.market_data.orderbook_diff import OrderbookDiff
from hermes.domain.market_data.trade import Trade
from hermes.domain.value_objects.pair import Pair
from hermes.infrastructure.config.models import MarketDataConfig
from hermes.ports.bar_feed import SourceHealth, SourceState
from hermes.ports.clock import ClockPort

__all__ = ["BinanceLiveKlinesAdapter"]

_KLINES_PATH: Final[str] = "/api/v3/klines"
_TIME_PATH: Final[str] = "/api/v3/time"
_ALLOWED_PATHS: Final[frozenset[str]] = frozenset({_KLINES_PATH, _TIME_PATH})
_API_KEY_HEADER: Final[str] = "x-mbx-apikey"
_USED_WEIGHT_HEADER: Final[str] = "x-mbx-used-weight-1m"
_REQUEST_WEIGHTS: Final[dict[str, int]] = {_KLINES_PATH: 2, _TIME_PATH: 1}
# Binance klines caps a page at 1000 rows; warm-up asks for count + 1 to read
# past the forming candle, so count tops out one below the cap.
_PAGE_LIMIT: Final[int] = 1000
_MAX_WARMUP_BARS: Final[int] = _PAGE_LIMIT - 1
_MAX_PAGES_PER_FETCH: Final[int] = 50
# ADR-003 §04: idempotent reads retry 3 times, waiting 100, 200, 400 ms.
_RETRY_BACKOFF_SECONDS: Final[tuple[float, ...]] = (0.1, 0.2, 0.4)
_RATE_LIMITED_STATUSES: Final[frozenset[int]] = frozenset({429, 418})
_SERVER_ERROR_MIN: Final[int] = 500
_CLIENT_ERROR_MIN: Final[int] = 400
_ERROR_EXCERPT_CHARS: Final[int] = 200
_MIN_KLINE_FIELDS: Final[int] = 7
# A resume marker far behind the exchange's history would otherwise log one
# line per missing bar.
_MAX_GAP_LOGS: Final[int] = 20
_MAX_RETRY_AFTER_SECONDS: Final[float] = 3_600.0
_EPOCH: Final[datetime] = datetime(1970, 1, 1, tzinfo=UTC)
_ONE_MS: Final[timedelta] = timedelta(milliseconds=1)

_log = structlog.get_logger(__name__)


class _Transient(Exception):  # noqa: N818 — internal control flow, never leaves this module
    """Retries ran out on a transport error or a 5xx response."""

    def __init__(self, cause: InfraError) -> None:
        super().__init__(str(cause))
        self.cause = cause


class _RateLimited(Exception):  # noqa: N818 — internal control flow, never leaves this module
    """The exchange answered 429 or 418; the caller waits ``retry_after``."""

    def __init__(self, status: int, retry_after: float | None) -> None:
        super().__init__(f"HTTP {status}")
        self.status = status
        self.retry_after = retry_after


def _to_ms(moment: datetime) -> int:
    """Return milliseconds since the UTC epoch, exactly (no float rounding)."""
    return (moment - _EPOCH) // _ONE_MS


def _from_ms(value: int) -> datetime:
    """Return the UTC datetime for ``value`` milliseconds since the epoch.

    Raises:
        ExchangeError: ``value`` is outside the calendar ``datetime`` supports.
    """
    try:
        return _EPOCH + timedelta(milliseconds=value)
    except OverflowError as exc:
        raise ExchangeError(f"klines timestamp out of range: {value}") from exc


def _symbol(pair: Pair) -> str:
    """Return the Binance concatenated symbol (``BTCUSDT``) for ``pair``."""
    return f"{pair.base.value}{pair.quote.value}"


def _retry_after_seconds(response: httpx.Response) -> float | None:
    """Parse a ``Retry-After`` header given in seconds; ``None`` when unusable."""
    raw = response.headers.get("retry-after")
    if raw is None:
        return None
    try:
        seconds = float(raw)
    except ValueError:
        return None
    if not math.isfinite(seconds) or seconds < 0:
        return None
    return min(seconds, _MAX_RETRY_AFTER_SECONDS)


def _decimal(raw: object, *, field: str) -> Decimal:
    """Coerce one wire field to :class:`Decimal` or raise :class:`ExchangeError`."""
    if not isinstance(raw, (str, int, float)) or isinstance(raw, bool):
        raise ExchangeError(f"klines row field {field!r} is not string or numeric: {raw!r}")
    try:
        value = Decimal(str(raw))
    except InvalidOperation as exc:
        raise ExchangeError(f"klines row field {field!r} is not a decimal: {raw!r}") from exc
    if not value.is_finite():
        raise ExchangeError(f"klines row field {field!r} is not a finite number: {raw!r}")
    return value


def _row_times(row: object, interval: KlineInterval) -> tuple[list[object], datetime, datetime]:
    """Return a raw klines row's fields, ``open_time`` and ``close_time``, without reading prices.

    Raises:
        ExchangeError: The row is not a list of enough fields, its timestamps
            are not ints, ``open_time`` is unaligned, or ``close_time`` is not
            ``open + interval - 1 ms``.
    """
    if not isinstance(row, list) or len(row) < _MIN_KLINE_FIELDS:
        raise ExchangeError(f"klines row must be a list of >= {_MIN_KLINE_FIELDS} fields: {row!r}")
    open_ms, close_ms = row[0], row[6]
    if (
        not isinstance(open_ms, int)
        or isinstance(open_ms, bool)
        or not isinstance(close_ms, int)
        or isinstance(close_ms, bool)
    ):
        raise ExchangeError(f"klines row timestamps must be ints, got {open_ms!r}, {close_ms!r}")
    open_time = _from_ms(open_ms)
    close_time = _from_ms(close_ms)
    if previous_boundary(open_time, interval) != open_time:
        raise ExchangeError(f"klines open_time {open_time.isoformat()} is not {interval} aligned")
    if close_time != close_time_of(open_time, interval):
        raise ExchangeError(
            f"klines close_time {close_time.isoformat()} is not open + {interval} - 1 ms"
        )
    return row, open_time, close_time


def _row_to_bar(row: object, pair: Pair, interval: KlineInterval) -> Bar:
    """Convert one raw klines row into a :class:`Bar`.

    Raises:
        ExchangeError: Malformed row, unaligned timestamps, a ``close_time``
            that is not ``open + interval - 1 ms``, or a domain-invariant
            violation.
    """
    fields, open_time, close_time = _row_times(row, interval)
    try:
        return Bar(
            pair=pair,
            interval=interval,
            open_time=open_time,
            open=_decimal(fields[1], field="open"),
            high=_decimal(fields[2], field="high"),
            low=_decimal(fields[3], field="low"),
            close=_decimal(fields[4], field="close"),
            volume=_decimal(fields[5], field="volume"),
            close_time=close_time,
        )
    except ValueError as exc:
        raise ExchangeError(f"klines row fails domain invariants: {exc}") from exc


def _parse_rows(
    rows: Sequence[object],
    pair: Pair,
    interval: KlineInterval,
    *,
    keep: Callable[[datetime], bool],
) -> list[Bar]:
    """Parse the rows whose ``open_time`` passes ``keep``, ascending with duplicates dropped.

    Rows are filtered on their timestamps before their prices are read, so a
    malformed value in a dropped row (the still-forming candle) cannot fail
    the bars that closed before it (issue #850).
    """
    kept = [row for row in rows if keep(_row_times(row, interval)[1])]
    by_open = {bar.open_time: bar for bar in (_row_to_bar(r, pair, interval) for r in kept)}
    return [by_open[key] for key in sorted(by_open)]


def _json(response: httpx.Response, path: str) -> object:
    """Return the decoded JSON body or raise :class:`ExchangeError`."""
    try:
        decoded: object = response.json()
    except ValueError as exc:
        raise ExchangeError(
            f"non-JSON response from {path}: {response.text[:_ERROR_EXCERPT_CHARS]}"
        ) from exc
    return decoded


class BinanceLiveKlinesAdapter:
    """Closed-bar source over Binance public klines.

    Satisfies :class:`~hermes.ports.bar_feed.ClosedBarFeedPort` and the bar
    channel of :class:`~hermes.ports.market_data.MarketDataPort`. Trades and
    order books are not available here (order book diffs stay on
    :class:`~hermes.adapters.market_data.binance_live_ws.BinanceLiveWsAdapter`).

    The adapter owns the injected ``client`` and closes it in :meth:`aclose`.
    """

    def __init__(
        self,
        *,
        config: MarketDataConfig,
        clock: ClockPort,
        client: httpx.AsyncClient,
        limiter: RateLimiter,
        sleep: Callable[[float], Awaitable[None]] = anyio.sleep,
    ) -> None:
        """Wire the adapter.

        Args:
            config: Host allow-list, pairs, timing and budget.
            clock: Local time source. Never the wall clock directly.
            client: HTTP client. Must not carry an API key header.
            limiter: Token bucket sized from ``config``.
            sleep: Async sleep. Tests inject one that advances a simulated
                clock instead of waiting.

        Raises:
            ValueError: ``client`` carries an ``X-MBX-APIKEY`` default header.
        """
        if _API_KEY_HEADER in client.headers:
            raise ValueError(
                "BinanceLiveKlinesAdapter is public-only: client must not carry an API key"
            )
        self._config = config
        self._clock = clock
        self._client = client
        self._limiter = limiter
        self._sleep = sleep
        self._settle = timedelta(seconds=config.settle_delay_seconds)
        self._state = SourceState.STARTING
        self._overdue_seconds = 0.0
        self._consecutive_failures = 0
        self._last_closed: datetime | None = None
        self._clock_skew_ms: int | None = None
        self._used_weight_1m: int | None = None
        self._primed: dict[Stream, datetime] = {}
        self._reported_gaps: set[tuple[Stream, datetime]] = set()

    @classmethod
    def create(cls, config: MarketDataConfig, clock: ClockPort) -> BinanceLiveKlinesAdapter:
        """Build an adapter with a default client and limiter from ``config``.

        Args:
            config: Market-data configuration.
            clock: Local time source.

        Returns:
            An adapter owning its :class:`httpx.AsyncClient` and limiter.
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

    # ------------------------------------------------------------------
    # ClosedBarFeedPort
    # ------------------------------------------------------------------

    async def warm_up(self, pair: Pair, interval: KlineInterval, count: int) -> list[Bar]:
        """Return up to ``count`` of the latest closed bars, oldest first.

        The open candle is dropped by the closed-bar rule. The bars returned
        become the stream's starting point, so :meth:`stream_closed_bars`
        continues from the next bar with no gap.

        Args:
            pair: Trading pair.
            interval: Candle-duration bucket.
            count: Closed bars wanted, 1 to 999.

        Returns:
            Closed bars in ascending ``open_time`` order.

        Raises:
            ValueError: ``count`` is outside 1 to 999.
            hermes.domain.errors.InfraError: The read failed. A transient
                failure leaves the source ``DEGRADED``; a permanent one
                (for example HTTP 451) leaves it ``FAILED``.
        """
        if not 1 <= count <= _MAX_WARMUP_BARS:
            raise ValueError(f"count must be between 1 and {_MAX_WARMUP_BARS}, got {count}")
        try:
            local_now = self._clock.now()
            exchange_now = await self._exchange_now()
            rows = await self._fetch_rows(pair, interval, start_ms=None, limit=count + 1)
            closed = _parse_rows(
                rows,
                pair,
                interval,
                keep=lambda open_time: is_closed(
                    open_time,
                    interval,
                    local_now=local_now,
                    exchange_now=exchange_now,
                    settle=self._settle,
                ),
            )[-count:]
        except _Transient as exc:
            self._mark_not_started()
            raise exc.cause from exc
        except _RateLimited as exc:
            self._mark_not_started()
            raise ExchangeError(
                f"HTTP {exc.status} from klines during warm-up (retry after {exc.retry_after} s)"
            ) from exc
        except InfraError:
            self._fail()
            raise
        if closed:
            stream = (pair, interval)
            self._primed[stream] = max(closed[-1].open_time, self._primed.get(stream, _EPOCH))
        if self._state is SourceState.STARTING:
            self._state = SourceState.HEALTHY
        return closed

    async def stream_closed_bars(
        self,
        pairs: Sequence[Pair],
        intervals: Sequence[KlineInterval],
        *,
        resume_after: Mapping[tuple[Pair, KlineInterval], datetime] | None = None,
    ) -> AsyncIterator[Bar]:
        """Yield each closed bar once, in delivery order, until :meth:`aclose`.

        A stream starts after ``resume_after[(pair, interval)]`` when given,
        else after the bars :meth:`warm_up` returned for it, else after the
        latest bar already closed when streaming starts.

        Args:
            pairs: Trading pairs to follow.
            intervals: Candle-duration buckets to follow.
            resume_after: Optional per-stream marker; bars with a later
                ``open_time`` are delivered.

        Yields:
            Closed bars sorted by ``(close_time, larger interval, pair)``.

        Raises:
            ValueError: No stream was requested, or a marker is naive.
            hermes.domain.errors.InfraError: A permanent failure; the source
                is then ``FAILED``.
        """
        streams = list(dict.fromkeys((pair, interval) for pair in pairs for interval in intervals))
        if not streams:
            raise ValueError("stream_closed_bars needs at least one pair and one interval")
        cursors = self._initial_cursors(streams, resume_after or {})
        while not self._stopped():
            await self._sleep_until_due(streams, cursors)
            if self._stopped():
                return
            try:
                batch, owed = await self._poll_cycle(streams, cursors)
            except _RateLimited as exc:
                self._record_failure(streams, cursors)
                await self._sleep(
                    exc.retry_after
                    if exc.retry_after is not None
                    else self._config.retry_interval_seconds
                )
                continue
            except _Transient:
                self._record_failure(streams, cursors)
                await self._sleep(self._retry_delay())
                continue
            except InfraError:
                self._fail()
                raise
            for bar in batch:
                yield bar
            if owed:
                await self._sleep(self._retry_delay())

    async def stream_bars(self, pair: Pair, interval: KlineInterval) -> AsyncIterator[Bar]:
        """Yield closed bars for one stream (a filter over the shared poller).

        Args:
            pair: Trading pair.
            interval: Candle-duration bucket.

        Yields:
            Closed bars in ascending ``open_time`` order.
        """
        async for bar in self.stream_closed_bars([pair], [interval]):
            yield bar

    def health(self) -> SourceHealth:
        """Return the current health snapshot."""
        return SourceHealth(
            state=self._state,
            overdue_seconds=self._overdue_seconds,
            consecutive_failures=self._consecutive_failures,
            last_closed=self._last_closed,
            clock_skew_ms=self._clock_skew_ms,
            used_weight_1m=self._used_weight_1m,
        )

    async def aclose(self) -> None:
        """Stop streaming and close the HTTP client. Safe to call twice."""
        self._state = SourceState.STOPPED
        await self._client.aclose()

    # ------------------------------------------------------------------
    # MarketDataPort channels this source does not serve
    # ------------------------------------------------------------------

    async def stream_trades(self, pair: Pair) -> AsyncIterator[Trade]:
        """Not supported: this source carries candles only.

        Raises:
            NotImplementedError: Always.
        """
        raise NotImplementedError("BinanceLiveKlinesAdapter serves closed bars only")

    async def stream_orderbook_diffs(self, pair: Pair) -> AsyncIterator[OrderbookDiff]:
        """Not supported: order book diffs stay on ``BinanceLiveWsAdapter``.

        Raises:
            NotImplementedError: Always.
        """
        raise NotImplementedError(
            "BinanceLiveKlinesAdapter serves closed bars only; "
            "use BinanceLiveWsAdapter for order book diffs"
        )

    async def get_snapshot(self, pair: Pair) -> Orderbook:
        """Not supported: order book snapshots stay on ``BinanceLiveWsAdapter``.

        Raises:
            NotImplementedError: Always.
        """
        raise NotImplementedError(
            "BinanceLiveKlinesAdapter serves closed bars only; "
            "use BinanceLiveWsAdapter for order book snapshots"
        )

    # ------------------------------------------------------------------
    # Poll cycle
    # ------------------------------------------------------------------

    def _stopped(self) -> bool:
        """Return whether :meth:`aclose` has run."""
        return self._state is SourceState.STOPPED

    def _initial_cursors(
        self,
        streams: Sequence[Stream],
        resume_after: Mapping[Stream, datetime],
    ) -> dict[Stream, datetime]:
        """Pick each stream's starting cursor: marker, warm-up, or latest closed bar."""
        now = self._clock.now()
        cursors: dict[Stream, datetime] = {}
        for stream in streams:
            interval = stream[1]
            if stream in resume_after:
                marker = resume_after[stream]
                if marker.tzinfo is None:
                    raise ValueError(f"resume_after[{stream}] must be UTC-aware, got {marker!r}")
                cursors[stream] = marker
            elif stream in self._primed:
                cursors[stream] = self._primed[stream]
            else:
                latest_close = previous_boundary(now - self._settle, interval)
                cursors[stream] = latest_close - interval_duration(interval)
        return cursors

    def _next_due_at(self, stream: Stream, cursor: datetime) -> datetime:
        """Return when the bar after ``cursor`` counts as closed on the local clock."""
        interval = stream[1]
        return next_boundary(cursor, interval) + interval_duration(interval) + self._settle

    async def _sleep_until_due(
        self, streams: Sequence[Stream], cursors: Mapping[Stream, datetime]
    ) -> None:
        """Sleep until the earliest stream has a bar that should be closed."""
        now = self._clock.now()
        due_at = min(self._next_due_at(stream, cursors[stream]) for stream in streams)
        if due_at > now:
            await self._sleep((due_at - now).total_seconds())

    def _owed_overdue_seconds(
        self,
        streams: Sequence[Stream],
        cursors: Mapping[Stream, datetime],
        local_now: datetime,
    ) -> float | None:
        """Return the largest overdue time among streams owed a bar, else ``None``."""
        overdue: list[float] = []
        for stream in streams:
            interval = stream[1]
            expected_close = next_boundary(cursors[stream], interval) + interval_duration(interval)
            if expected_close + self._settle <= local_now:
                overdue.append((local_now - expected_close).total_seconds())
        return max(overdue) if overdue else None

    async def _poll_cycle(
        self,
        streams: Sequence[Stream],
        cursors: dict[Stream, datetime],
    ) -> tuple[list[Bar], bool]:
        """Run one poll cycle and return ``(bars to deliver, still owed a bar)``.

        Raises:
            _Transient: Retries ran out on a transient failure.
            _RateLimited: The exchange answered 429 or 418.
            hermes.domain.errors.InfraError: A permanent failure.
        """
        local_now = self._clock.now()
        exchange_now = await self._exchange_now()
        # Skew is read at the midpoint of the round trip so a slow response is
        # not mistaken for a wrong clock. Bar closure keeps the earlier local
        # reading, which can only delay a bar.
        round_trip = self._clock.now() - local_now
        skew = local_now + round_trip / 2 - exchange_now
        self._clock_skew_ms = skew // _ONE_MS
        fetched: dict[Stream, list[Bar]] = {}
        for stream in streams:
            interval = stream[1]
            next_open = next_boundary(cursors[stream], interval)
            if is_closed(
                next_open,
                interval,
                local_now=local_now,
                exchange_now=exchange_now,
                settle=self._settle,
            ):
                fetched[stream] = await self._fetch_closed_since(
                    stream, cursors[stream], local_now, exchange_now
                )
        plan = plan_delivery(fetched, cursors)
        cursors.update(plan.cursors)
        self._report_gaps(plan.gaps)
        if plan.bars:
            self._last_closed = max(bar.close_time for bar in plan.bars)
        overdue = self._owed_overdue_seconds(streams, cursors, local_now)
        clean = overdue is None and not plan.gaps and abs(skew) <= self._settle
        self._record_cycle(clean=clean, overdue=overdue or 0.0)
        return list(plan.bars), overdue is not None

    def _report_gaps(self, gaps: Sequence[tuple[Stream, datetime]]) -> None:
        """Log each skipped bar once, at most ``_MAX_GAP_LOGS`` per call."""
        fresh = [gap for gap in gaps if gap not in self._reported_gaps]
        if len(fresh) > _MAX_GAP_LOGS:
            _log.warning("market_data.gaps_detected", count=len(fresh))
            self._reported_gaps.update(fresh)
            return
        for stream, open_time in fresh:
            self._reported_gaps.add((stream, open_time))
            _log.warning(
                "market_data.gap_detected",
                pair=str(stream[0]),
                interval=stream[1].value,
                missing_open_time=open_time.isoformat(),
            )

    def _record_cycle(self, *, clean: bool, overdue: float) -> None:
        """Update health after a cycle that reached the exchange."""
        if self._state in {SourceState.FAILED, SourceState.STOPPED}:
            return
        if clean:
            self._state = SourceState.HEALTHY
            self._overdue_seconds = 0.0
            self._consecutive_failures = 0
            return
        self._consecutive_failures += 1
        self._overdue_seconds = overdue
        self._state = (
            SourceState.STALE
            if overdue >= self._config.stale_after_seconds
            else SourceState.DEGRADED
        )

    def _record_failure(
        self, streams: Sequence[Stream], cursors: Mapping[Stream, datetime]
    ) -> None:
        """Update health after a cycle that could not reach the exchange."""
        overdue = self._owed_overdue_seconds(streams, cursors, self._clock.now())
        self._record_cycle(clean=False, overdue=overdue or 0.0)

    def _fail(self) -> None:
        """Mark the source ``FAILED`` unless it was already stopped."""
        if not self._stopped():
            self._state = SourceState.FAILED

    def _mark_not_started(self) -> None:
        """Mark the source ``DEGRADED`` after a failed warm-up."""
        if self._state is SourceState.STARTING:
            self._state = SourceState.DEGRADED
        self._consecutive_failures += 1

    def _retry_delay(self) -> float:
        """Return the wait before the next re-poll: slow once the source is stale."""
        if self._state is SourceState.STALE:
            return self._config.slow_retry_interval_seconds
        return self._config.retry_interval_seconds

    # ------------------------------------------------------------------
    # Wire
    # ------------------------------------------------------------------

    async def _exchange_now(self) -> datetime:
        """Return the exchange clock from ``GET /api/v3/time``."""
        payload = _json(await self._get(_TIME_PATH, None), _TIME_PATH)
        server_time = payload.get("serverTime") if isinstance(payload, dict) else None
        if not isinstance(server_time, int) or isinstance(server_time, bool):
            raise ExchangeError(f"time endpoint returned no integer serverTime: {payload!r}")
        return _from_ms(server_time)

    async def _fetch_rows(
        self,
        pair: Pair,
        interval: KlineInterval,
        *,
        start_ms: int | None,
        limit: int,
    ) -> list[object]:
        """Return one raw page from ``GET /api/v3/klines``."""
        params: dict[str, str] = {
            "symbol": _symbol(pair),
            "interval": interval.value,
            "limit": str(limit),
        }
        if start_ms is not None:
            params["startTime"] = str(start_ms)
        payload = _json(await self._get(_KLINES_PATH, params), _KLINES_PATH)
        if not isinstance(payload, list):
            raise ExchangeError(f"klines endpoint returned a non-list body: {str(payload)[:200]}")
        return payload

    async def _fetch_closed_since(
        self,
        stream: Stream,
        cursor: datetime,
        local_now: datetime,
        exchange_now: datetime,
    ) -> list[Bar]:
        """Return every closed bar with ``open_time`` after ``cursor``, paging as needed."""
        pair, interval = stream
        start_ms = _to_ms(cursor) + 1
        closed: list[Bar] = []
        for _ in range(_MAX_PAGES_PER_FETCH):
            rows = await self._fetch_rows(pair, interval, start_ms=start_ms, limit=_PAGE_LIMIT)
            bars = _parse_rows(
                rows,
                pair,
                interval,
                keep=lambda open_time: is_closed(
                    open_time,
                    interval,
                    local_now=local_now,
                    exchange_now=exchange_now,
                    settle=self._settle,
                ),
            )
            closed.extend(bar for bar in bars if bar.open_time > cursor)
            if len(rows) < _PAGE_LIMIT or not rows:
                break
            next_start_ms = max(_to_ms(_row_times(row, interval)[1]) for row in rows) + 1
            if next_start_ms <= start_ms:
                break
            start_ms = next_start_ms
        return closed

    async def _acquire(self, weight: int) -> None:
        """Wait, without blocking the loop, until the bucket grants ``weight``."""
        while not self._limiter.try_acquire(weight):
            await self._sleep(weight / self._limiter.refill_per_second)

    async def _get(self, path: str, params: dict[str, str] | None) -> httpx.Response:
        """Send one GET to an allow-listed path, retrying transient failures.

        Raises:
            _Transient: Retries ran out on a transport error or a 5xx.
            _RateLimited: HTTP 429 or 418 (never retried here).
            hermes.domain.errors.ExchangeError: Any other HTTP error (permanent).
        """
        if path not in _ALLOWED_PATHS:
            raise ExchangeError(f"refusing request to {path!r}: only {sorted(_ALLOWED_PATHS)}")
        url = f"{self._config.base_url}{path}"
        weight = _REQUEST_WEIGHTS[path]
        last_error: InfraError | None = None
        for attempt in range(len(_RETRY_BACKOFF_SECONDS) + 1):
            await self._acquire(weight)
            try:
                response = await self._client.get(url, params=params)
            except httpx.HTTPError as exc:
                last_error = NetworkError(f"transport failure calling {path}: {exc!r}")
            else:
                self._note_used_weight(response)
                status = response.status_code
                if status in _RATE_LIMITED_STATUSES:
                    raise _RateLimited(status, _retry_after_seconds(response))
                if status >= _SERVER_ERROR_MIN:
                    last_error = ExchangeError(
                        f"HTTP {status} from {path}: {response.text[:_ERROR_EXCERPT_CHARS]}"
                    )
                elif status >= _CLIENT_ERROR_MIN:
                    raise ExchangeError(
                        f"HTTP {status} from {path}: {response.text[:_ERROR_EXCERPT_CHARS]}"
                    )
                else:
                    return response
            if attempt < len(_RETRY_BACKOFF_SECONDS):
                await self._sleep(_RETRY_BACKOFF_SECONDS[attempt])
        if last_error is None:  # pragma: no cover — the loop always records an error first
            raise ExchangeError(f"request to {path} failed without an error")
        raise _Transient(last_error)

    def _note_used_weight(self, response: httpx.Response) -> None:
        """Remember the exchange's ``x-mbx-used-weight-1m`` header, when valid."""
        raw = response.headers.get(_USED_WEIGHT_HEADER)
        if raw is None:
            return
        try:
            self._used_weight_1m = int(raw)
        except ValueError:
            return
