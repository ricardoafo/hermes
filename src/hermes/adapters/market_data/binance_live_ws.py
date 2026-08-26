"""Binance WebSocket L2 depth-stream adapter — shadow-phase only.

Implements the ``stream_orderbook_diffs`` and ``get_snapshot`` methods of
:class:`hermes.ports.market_data.MarketDataPort` using the Binance partial
book depth WebSocket and REST depth endpoints.

The adapter is **shadow-phase only** — MVP1 backtest uses historical bars,
not live L2 (OD-1 Option C). This module provides the reusable streaming
logic; the VPS-side 24/7 systemd collector unit is out of scope here.

L2 bootstrap procedure (per SA-3):

1. Caller calls ``get_snapshot`` to obtain a REST depth snapshot with a
   ``lastUpdateId``.
2. Caller calls ``stream_orderbook_diffs`` to open the WebSocket stream.
3. Caller discards diff events where ``final_update_id <= lastUpdateId``
   (stale events received before the snapshot).
4. The first retained event must satisfy
   ``first_update_id <= lastUpdateId + 1 <= final_update_id``.
5. Subsequent events are applied in ``first_update_id`` order; a gap
   (``first_update_id != prev_final_update_id + 1``) triggers a re-snapshot.

This adapter raises :class:`~hermes.domain.errors.InfraError` on:

- WebSocket connection failures or unexpected close.
- JSON parse errors on depth-update messages.
- Schema violations (missing required fields).
- Detected sequence-number gaps (caller should re-snapshot).

``stream_bars`` and ``stream_trades`` raise ``NotImplementedError`` —
live WebSocket depth data does not carry bars or trade ticks; use
:class:`~hermes.adapters.market_data.binance_historical.BinanceHistoricalAdapter`
for bar/trade streams.

Design decisions:

- D-2: ``anyio`` is used for async I/O (project policy — anyio-on-asyncio).
  WebSocket is provided by the ``websockets`` library (async-native).
  The adapter wraps the WS library under anyio task scope.
- Gap detection is implemented inside ``stream_orderbook_diffs``: when a
  gap is detected the generator raises ``InfraError`` so the caller
  (application layer) can decide whether to re-snapshot or halt.
- ``get_snapshot`` uses ``httpx.AsyncClient`` for the REST call
  (consistent with project HTTP patterns).

Reference: spec-4c-market-data-adapters change.md D-2.
SA-3 §Binance L2 snapshot procedure.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Final

import httpx
import structlog

# Optional dependency — installed by the VPS shadow collector. Imported at
# module level so ruff's PLC0415 is satisfied; guarded by ImportError inside
# stream_orderbook_diffs to give a helpful error when not present.
try:
    import websockets  # type: ignore[import-not-found]
except ImportError:
    websockets = None

from hermes.domain.errors import ExchangeError, InfraError, NetworkError
from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.market_data.orderbook import Orderbook, OrderbookLevel
from hermes.domain.market_data.orderbook_diff import OrderbookDiff, OrderbookDiffLevel
from hermes.domain.market_data.trade import Trade
from hermes.domain.value_objects.pair import Pair

__all__ = ["BinanceLiveWsAdapter", "BinanceLiveWsConfig"]

_BINANCE_WS_BASE: Final[str] = "wss://stream.binance.com:9443/ws"
_BINANCE_REST_BASE: Final[str] = "https://api.binance.com"
_DEPTH_SNAPSHOT_PATH: Final[str] = "/api/v3/depth"
_DEFAULT_SNAPSHOT_LIMIT: Final[int] = 100
_DEFAULT_TIMEOUT_SEC: Final[float] = 10.0
_MS_PER_SECOND: Final[int] = 1_000
# Maximum number of levels in a REST depth snapshot. Binance allows up to 5000.
_HTTP_ERROR_THRESHOLD: Final[int] = 400
# Minimum number of elements in a depth level (price + qty).
_LEVEL_MIN_ELEMENTS: Final[int] = 2

_log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class BinanceLiveWsConfig:
    """Wire configuration for :class:`BinanceLiveWsAdapter`.

    Attributes:
        ws_base_url: WebSocket base URL. Overridable for testing.
        rest_base_url: REST API base URL.
        snapshot_limit: Number of orderbook levels to request via REST.
        timeout_sec: Per-request REST timeout in seconds.
    """

    ws_base_url: str = _BINANCE_WS_BASE
    rest_base_url: str = _BINANCE_REST_BASE
    snapshot_limit: int = _DEFAULT_SNAPSHOT_LIMIT
    timeout_sec: float = _DEFAULT_TIMEOUT_SEC

    def __post_init__(self) -> None:
        """Validate URL schemes and positive timeout."""
        if not self.ws_base_url.startswith(("wss://", "ws://")):
            raise ValueError(
                f"BinanceLiveWsConfig.ws_base_url must start with wss:// or ws://, "
                f"got {self.ws_base_url!r}"
            )
        if not self.rest_base_url.startswith("https://"):
            raise ValueError(
                f"BinanceLiveWsConfig.rest_base_url must start with https://, "
                f"got {self.rest_base_url!r}"
            )
        if self.timeout_sec <= 0:
            raise ValueError(f"BinanceLiveWsConfig.timeout_sec must be > 0, got {self.timeout_sec}")
        if self.snapshot_limit <= 0:
            raise ValueError(
                f"BinanceLiveWsConfig.snapshot_limit must be > 0, got {self.snapshot_limit}"
            )


@dataclass(slots=True)
class BinanceLiveWsAdapter:
    """WebSocket L2 depth adapter for Binance — shadow-phase only.

    Implements ``stream_orderbook_diffs`` and ``get_snapshot``. The bar
    and trade streaming methods raise ``NotImplementedError``.

    Attributes:
        config: Wire configuration.
        _client: Lazily-created :class:`httpx.AsyncClient` for REST calls.
    """

    config: BinanceLiveWsConfig
    _client: httpx.AsyncClient | None = field(default=None, init=False, repr=False)

    @classmethod
    def create(cls, config: BinanceLiveWsConfig | None = None) -> BinanceLiveWsAdapter:
        """Build an adapter with default config.

        Args:
            config: Optional wire config. When ``None`` defaults to the
                Binance mainnet WebSocket + REST endpoints.

        Returns:
            A new :class:`BinanceLiveWsAdapter`.
        """
        return cls(config=config or BinanceLiveWsConfig())

    async def _get_client(self) -> httpx.AsyncClient:
        """Return a lazily-created async HTTP client."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.config.rest_base_url,
                timeout=self.config.timeout_sec,
            )
        return self._client

    async def close(self) -> None:
        """Release the underlying HTTP client resources."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------
    # MarketDataPort — L2 snapshot
    # ------------------------------------------------------------------

    async def get_snapshot(self, pair: Pair) -> Orderbook:
        """Return a REST depth snapshot for ``pair`` to bootstrap L2 state.

        Calls ``GET /api/v3/depth`` with ``limit=config.snapshot_limit``.

        Args:
            pair: Trading pair.

        Returns:
            An :class:`~hermes.domain.market_data.orderbook.Orderbook`
            snapshot at ``last_update_id``.

        Raises:
            hermes.domain.errors.NetworkError: On transport failure.
            hermes.domain.errors.ExchangeError: On HTTP non-2xx or schema error.
        """
        symbol = _pair_to_binance_symbol(pair)
        client = await self._get_client()

        _log.debug("get_snapshot: fetching", symbol=symbol, limit=self.config.snapshot_limit)

        try:
            response = await client.get(
                _DEPTH_SNAPSHOT_PATH,
                params={
                    "symbol": symbol,
                    "limit": str(self.config.snapshot_limit),
                },
            )
        except httpx.HTTPError as exc:
            raise NetworkError(
                f"BinanceLiveWsAdapter: transport failure fetching snapshot for {pair}: {exc!r}"
            ) from exc

        if response.status_code >= _HTTP_ERROR_THRESHOLD:
            raise ExchangeError(
                f"BinanceLiveWsAdapter: HTTP {response.status_code} from depth endpoint "
                f"for {pair}: {response.text[:200]}"
            )

        try:
            data: dict[str, Any] = response.json()
        except ValueError as exc:
            raise ExchangeError(
                f"BinanceLiveWsAdapter: non-JSON response from depth endpoint: "
                f"{response.text[:200]}"
            ) from exc

        return _parse_snapshot(data=data, pair=pair)

    # ------------------------------------------------------------------
    # MarketDataPort — L2 diff stream
    # ------------------------------------------------------------------

    async def stream_orderbook_diffs(self, pair: Pair) -> AsyncIterator[OrderbookDiff]:
        """Yield L2 incremental depth-update events for ``pair``.

        Opens a Binance ``<symbol>@depth`` WebSocket stream and yields
        :class:`~hermes.domain.market_data.orderbook_diff.OrderbookDiff`
        events in order. Raises :class:`~hermes.domain.errors.InfraError`
        on sequence-number gaps so the caller can re-snapshot.

        Args:
            pair: Trading pair.

        Yields:
            :class:`~hermes.domain.market_data.orderbook_diff.OrderbookDiff`
            events in ascending ``final_update_id`` order.

        Raises:
            hermes.domain.errors.InfraError: On WebSocket failure, parse
                error, schema violation, or detected sequence-number gap.
        """
        symbol = _pair_to_binance_symbol(pair).lower()
        ws_url = f"{self.config.ws_base_url}/{symbol}@depth"

        _log.debug("stream_orderbook_diffs: connecting", url=ws_url)

        if websockets is None:
            raise InfraError(
                "BinanceLiveWsAdapter: 'websockets' package is required for WebSocket "
                "streaming — install it with: uv add websockets"
            )

        prev_final_update_id: int | None = None

        try:
            async with websockets.connect(ws_url) as ws:
                async for raw_message in ws:
                    diff = _parse_depth_update(
                        raw=raw_message,
                        pair=pair,
                        source=ws_url,
                    )

                    # Gap detection (SA-3 §Binance L2 snapshot procedure step 5).
                    if prev_final_update_id is not None:
                        expected_first = prev_final_update_id + 1
                        if diff.first_update_id != expected_first:
                            raise InfraError(
                                f"BinanceLiveWsAdapter: sequence-number gap for {pair}. "
                                f"Expected first_update_id={expected_first}, "
                                f"got {diff.first_update_id}. Re-snapshot required."
                            )

                    prev_final_update_id = diff.final_update_id
                    yield diff

        except InfraError:
            raise
        except Exception as exc:
            raise InfraError(f"BinanceLiveWsAdapter: WebSocket error for {pair}: {exc!r}") from exc

    # ------------------------------------------------------------------
    # MarketDataPort — bar / trade (not available for live WS)
    # ------------------------------------------------------------------

    async def stream_bars(self, pair: Pair, interval: KlineInterval) -> AsyncIterator[Bar]:
        """Not supported for live WebSocket adapter.

        Raises:
            NotImplementedError: Always.
        """
        raise NotImplementedError(
            "BinanceLiveWsAdapter does not support stream_bars — "
            "use BinanceHistoricalAdapter for bar streams."
        )

    async def stream_trades(self, pair: Pair) -> AsyncIterator[Trade]:
        """Not supported for live WebSocket adapter.

        Raises:
            NotImplementedError: Always.
        """
        raise NotImplementedError(
            "BinanceLiveWsAdapter does not support stream_trades — "
            "use BinanceHistoricalAdapter for trade streams."
        )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _pair_to_binance_symbol(pair: Pair) -> str:
    """Convert :class:`Pair` to Binance uppercase symbol.

    Args:
        pair: Trading pair.

    Returns:
        Uppercase concatenated string, e.g. ``"BTCUSDT"``.
    """
    return f"{pair.base.value}{pair.quote.value}"


def _decimal_or_raise(raw: str, *, field_name: str, source: str) -> Decimal:
    """Coerce a raw string field to :class:`~decimal.Decimal`.

    Args:
        raw: String from the exchange payload.
        field_name: Descriptive name for error messages.
        source: WS URL or endpoint name for traceability.

    Returns:
        The value as :class:`~decimal.Decimal`.

    Raises:
        hermes.domain.errors.ExchangeError: On unparseable input.
    """
    try:
        return Decimal(raw)
    except InvalidOperation as exc:
        raise ExchangeError(
            f"BinanceLiveWsAdapter: {field_name} is not a decimal ({raw!r}) from {source}"
        ) from exc


def _parse_level_list(
    raw_levels: list[list[str]],
    *,
    source: str,
    side_label: str,
) -> tuple[OrderbookDiffLevel, ...]:
    """Parse ``[price, qty]`` pairs from a WS depth message into diff level tuples.

    Each tuple is an :class:`~hermes.domain.market_data.orderbook_diff.OrderbookDiffLevel`.
    A ``qty`` of ``"0"`` signals a level deletion (``delete=True``).

    Args:
        raw_levels: List of ``[price_str, qty_str]`` from the WS message.
        source: WS URL for error attribution.
        side_label: ``"bid"`` or ``"ask"`` for error messages.

    Returns:
        Tuple of :class:`~hermes.domain.market_data.orderbook_diff.OrderbookDiffLevel`.

    Raises:
        hermes.domain.errors.ExchangeError: On malformed level data.
    """
    levels: list[OrderbookDiffLevel] = []
    for idx, level in enumerate(raw_levels):
        if not isinstance(level, list) or len(level) < _LEVEL_MIN_ELEMENTS:
            raise ExchangeError(
                f"BinanceLiveWsAdapter: malformed {side_label} level at index {idx} "
                f"from {source}: {level!r}"
            )
        price = _decimal_or_raise(level[0], field_name=f"{side_label}[{idx}].price", source=source)
        qty = _decimal_or_raise(level[1], field_name=f"{side_label}[{idx}].qty", source=source)
        delete = qty == Decimal("0")
        try:
            levels.append(OrderbookDiffLevel(price=price, quantity=qty, delete=delete))
        except ValueError as exc:
            raise ExchangeError(
                f"BinanceLiveWsAdapter: {side_label}[{idx}] fails domain invariants "
                f"from {source}: {exc}"
            ) from exc
    return tuple(levels)


def _parse_depth_update(*, raw: str | bytes, pair: Pair, source: str) -> OrderbookDiff:
    """Parse a raw Binance ``depthUpdate`` WebSocket message.

    Expected shape::

        {
            "e": "depthUpdate",
            "E": 123456789,   // event time (ms)
            "s": "BTCUSDT",   // symbol
            "U": 157,          // first update id
            "u": 160,          // final update id
            "b": [["50000.00", "0.5"]],  // bids
            "a": [["50001.00", "0.3"]]   // asks
        }

    Args:
        raw: Raw WebSocket message (str or bytes).
        pair: Expected trading pair (used for domain object construction).
        source: WS URL for error attribution.

    Returns:
        A validated :class:`~hermes.domain.market_data.orderbook_diff.OrderbookDiff`.

    Raises:
        hermes.domain.errors.ExchangeError: On parse error or schema violation.
    """
    try:
        data: dict[str, Any] = json.loads(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ExchangeError(
            f"BinanceLiveWsAdapter: failed to parse depth update JSON from {source}: {exc!r}"
        ) from exc

    event_type = data.get("e")
    if event_type != "depthUpdate":
        raise ExchangeError(
            f"BinanceLiveWsAdapter: unexpected event type {event_type!r} from {source} "
            f"(expected 'depthUpdate')"
        )

    for required in ("U", "u", "b", "a"):
        if required not in data:
            raise ExchangeError(
                f"BinanceLiveWsAdapter: depth update missing required field {required!r} "
                f"from {source}"
            )

    first_update_id = data["U"]
    final_update_id = data["u"]

    if not isinstance(first_update_id, int) or not isinstance(final_update_id, int):
        raise ExchangeError(
            f"BinanceLiveWsAdapter: update IDs must be integers from {source}, "
            f"got U={first_update_id!r}, u={final_update_id!r}"
        )

    raw_bids: list[list[str]] = data["b"]
    raw_asks: list[list[str]] = data["a"]

    bids = _parse_level_list(raw_bids, source=source, side_label="bid")
    asks = _parse_level_list(raw_asks, source=source, side_label="ask")

    try:
        return OrderbookDiff(
            pair=pair,
            bids=bids,
            asks=asks,
            first_update_id=first_update_id,
            final_update_id=final_update_id,
        )
    except ValueError as exc:
        raise ExchangeError(
            f"BinanceLiveWsAdapter: depth update fails domain invariants from {source}: {exc}"
        ) from exc


def _parse_orderbook_level_list(
    raw_levels: list[list[str]],
    *,
    source: str,
    side_label: str,
) -> list[OrderbookLevel]:
    """Parse ``[price, qty]`` pairs from a REST depth response into a level list.

    Each item is an :class:`~hermes.domain.market_data.orderbook.OrderbookLevel`.

    Levels with ``qty == "0"`` are skipped (empty levels in REST snapshots
    are artefacts, not valid resting orders).

    Args:
        raw_levels: List of ``[price_str, qty_str]`` from the REST response.
        source: Endpoint URL for traceability.
        side_label: ``"bid"`` or ``"ask"`` for error messages.

    Returns:
        List of :class:`~hermes.domain.market_data.orderbook.OrderbookLevel`.

    Raises:
        hermes.domain.errors.ExchangeError: On malformed level data.
    """
    levels: list[OrderbookLevel] = []
    for idx, level in enumerate(raw_levels):
        if not isinstance(level, list) or len(level) < _LEVEL_MIN_ELEMENTS:
            raise ExchangeError(
                f"BinanceLiveWsAdapter: malformed {side_label} level at index {idx} "
                f"from {source}: {level!r}"
            )
        price = _decimal_or_raise(level[0], field_name=f"{side_label}[{idx}].price", source=source)
        qty = _decimal_or_raise(level[1], field_name=f"{side_label}[{idx}].qty", source=source)
        # Skip zero-quantity levels — they appear in REST responses when a
        # level was deleted between the exchange computing the snapshot and
        # the HTTP response arriving.
        if qty == Decimal("0"):
            continue
        try:
            levels.append(OrderbookLevel(price=price, quantity=qty))
        except ValueError as exc:
            raise ExchangeError(
                f"BinanceLiveWsAdapter: {side_label}[{idx}] fails domain invariants "
                f"from {source}: {exc}"
            ) from exc
    return levels


def _parse_snapshot(*, data: dict[str, Any], pair: Pair) -> Orderbook:
    """Convert a Binance REST depth snapshot response dict to an :class:`Orderbook`.

    Expected shape::

        {
            "lastUpdateId": 1027024,
            "bids": [["4.00000000", "431.00000000"]],
            "asks": [["4.00000200", "12.00000000"]]
        }

    Args:
        data: Parsed JSON response dict.
        pair: Trading pair.

    Returns:
        An :class:`~hermes.domain.market_data.orderbook.Orderbook` snapshot.

    Raises:
        hermes.domain.errors.ExchangeError: On missing fields or schema errors.
    """
    source = _DEPTH_SNAPSHOT_PATH

    for required in ("lastUpdateId", "bids", "asks"):
        if required not in data:
            raise ExchangeError(
                f"BinanceLiveWsAdapter: depth snapshot missing field {required!r} from {source}"
            )

    raw_bids: list[list[str]] = data["bids"]
    raw_asks: list[list[str]] = data["asks"]

    bid_levels = _parse_orderbook_level_list(raw_bids, source=source, side_label="bid")
    ask_levels = _parse_orderbook_level_list(raw_asks, source=source, side_label="ask")

    # Binance REST depth: bids descending (best bid first), asks ascending.
    # Domain Orderbook enforces these invariants in __post_init__.

    snapshot_time = datetime.now(tz=UTC)

    try:
        return Orderbook(
            pair=pair,
            bids=tuple(bid_levels),
            asks=tuple(ask_levels),
            snapshot_time=snapshot_time,
        )
    except ValueError as exc:
        raise ExchangeError(
            f"BinanceLiveWsAdapter: depth snapshot fails domain invariants: {exc}"
        ) from exc
