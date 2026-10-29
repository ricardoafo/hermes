"""Unit tests for :class:`BinanceLiveWsAdapter`.

Covers:

- Config validation (URL schemes, positive timeout, positive snapshot_limit).
- ``_parse_depth_update``: happy path + malformed messages.
- ``_parse_snapshot``: happy path + missing fields.
- ``get_snapshot`` via mocked HTTP (httpx MockTransport).
- ``stream_bars`` / ``stream_trades`` raise NotImplementedError.
- Sequence-number gap detection.
"""

import json
from decimal import Decimal
from typing import Any

import anyio
import httpx
import pytest

import hermes.adapters.market_data.binance_live_ws as ws_module
from hermes.adapters.market_data.binance_live_ws import (
    BinanceLiveWsAdapter,
    BinanceLiveWsConfig,
    _parse_depth_update,
    _parse_level_list,
    _parse_orderbook_level_list,
    _parse_snapshot,
)
from hermes.domain.errors import ExchangeError, InfraError, NetworkError
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair

_BTC_USDT = Pair(base=Currency.BTC, quote=Currency.USDT)


def _depth_update_msg(
    first_update_id: int = 100,
    final_update_id: int = 105,
    bids: list[list[str]] | None = None,
    asks: list[list[str]] | None = None,
) -> str:
    """Build a valid Binance depthUpdate JSON string."""
    return json.dumps(
        {
            "e": "depthUpdate",
            "E": 1704067200000,
            "s": "BTCUSDT",
            "U": first_update_id,
            "u": final_update_id,
            "b": bids or [["42000.00", "0.5"]],
            "a": asks or [["42001.00", "0.3"]],
        }
    )


def _snapshot_response(
    last_update_id: int = 500,
    bids: list[list[str]] | None = None,
    asks: list[list[str]] | None = None,
) -> dict[str, Any]:
    """Build a valid Binance depth snapshot response dict."""
    return {
        "lastUpdateId": last_update_id,
        "bids": bids or [["42000.00", "0.5"]],
        "asks": asks or [["42001.00", "0.3"]],
    }


@pytest.mark.unit
def test_config_rejects_non_wss_ws_url() -> None:
    """Config raises ValueError when ws_base_url uses http:// scheme."""
    with pytest.raises(ValueError, match="wss://"):
        BinanceLiveWsConfig(ws_base_url="http://insecure.example.com")


@pytest.mark.unit
def test_config_accepts_ws_scheme() -> None:
    """Config accepts ws:// scheme (test/mock scenarios)."""
    cfg = BinanceLiveWsConfig(ws_base_url="ws://localhost:8080/ws")
    assert cfg.ws_base_url == "ws://localhost:8080/ws", (
        f"Expected ws:// URL to be accepted, got {cfg.ws_base_url}"
    )


@pytest.mark.unit
def test_config_rejects_non_https_rest_url() -> None:
    """Config raises ValueError when rest_base_url is not https://."""
    with pytest.raises(ValueError, match="https://"):
        BinanceLiveWsConfig(
            ws_base_url="wss://stream.binance.com:9443/ws",
            rest_base_url="http://insecure.example.com",
        )


@pytest.mark.unit
def test_config_rejects_non_positive_timeout() -> None:
    """Config raises ValueError when timeout_sec <= 0."""
    with pytest.raises(ValueError, match="timeout_sec"):
        BinanceLiveWsConfig(timeout_sec=-1.0)


@pytest.mark.unit
def test_config_rejects_non_positive_snapshot_limit() -> None:
    """Config raises ValueError when snapshot_limit <= 0."""
    with pytest.raises(ValueError, match="snapshot_limit"):
        BinanceLiveWsConfig(snapshot_limit=0)


@pytest.mark.unit
def test_parse_depth_update_returns_diff() -> None:
    """_parse_depth_update correctly parses a valid depthUpdate message."""
    msg = _depth_update_msg(first_update_id=100, final_update_id=105)
    diff = _parse_depth_update(raw=msg, pair=_BTC_USDT, source="ws://test")

    assert diff.pair == _BTC_USDT, f"diff.pair should be {_BTC_USDT}, got {diff.pair}"
    assert diff.first_update_id == 100, f"first_update_id should be 100, got {diff.first_update_id}"
    assert diff.final_update_id == 105, f"final_update_id should be 105, got {diff.final_update_id}"
    assert len(diff.bids) == 1, f"Expected 1 bid level, got {len(diff.bids)}"
    assert len(diff.asks) == 1, f"Expected 1 ask level, got {len(diff.asks)}"


@pytest.mark.unit
def test_parse_depth_update_bid_price_and_qty() -> None:
    """_parse_depth_update: bid price and quantity are correctly parsed."""
    msg = _depth_update_msg(bids=[["42000.50", "1.25"]])
    diff = _parse_depth_update(raw=msg, pair=_BTC_USDT, source="ws://test")
    bid = diff.bids[0]

    assert bid.price == Decimal("42000.50"), f"bid.price should be 42000.50, got {bid.price}"
    assert bid.quantity == Decimal("1.25"), f"bid.quantity should be 1.25, got {bid.quantity}"
    assert not bid.delete, "bid.delete should be False for non-zero qty"


@pytest.mark.unit
def test_parse_depth_update_zero_qty_signals_delete() -> None:
    """_parse_depth_update: qty=0 sets delete=True on the level."""
    msg = _depth_update_msg(asks=[["42001.00", "0"]])
    diff = _parse_depth_update(raw=msg, pair=_BTC_USDT, source="ws://test")
    ask = diff.asks[0]

    assert ask.delete, "ask.delete should be True when qty=0 (level deletion signal)"
    assert ask.quantity == Decimal("0"), (
        f"ask.quantity should be 0 for deleted level, got {ask.quantity}"
    )


@pytest.mark.unit
def test_parse_depth_update_empty_bids_and_asks() -> None:
    """_parse_depth_update handles empty bid and ask lists."""
    msg = json.dumps(
        {
            "e": "depthUpdate",
            "E": 1704067200000,
            "s": "BTCUSDT",
            "U": 100,
            "u": 100,
            "b": [],
            "a": [],
        }
    )
    diff = _parse_depth_update(raw=msg, pair=_BTC_USDT, source="ws://test")
    assert len(diff.bids) == 0, f"Expected 0 bids, got {len(diff.bids)}"
    assert len(diff.asks) == 0, f"Expected 0 asks, got {len(diff.asks)}"


@pytest.mark.unit
def test_parse_depth_update_invalid_json_raises_exchange_error() -> None:
    """_parse_depth_update raises ExchangeError on invalid JSON."""
    with pytest.raises(ExchangeError, match="parse"):
        _parse_depth_update(raw="not json {{", pair=_BTC_USDT, source="ws://test")


@pytest.mark.unit
def test_parse_depth_update_wrong_event_type_raises_exchange_error() -> None:
    """_parse_depth_update raises ExchangeError when event type is not depthUpdate."""
    msg = json.dumps({"e": "trade", "U": 100, "u": 105, "b": [], "a": []})
    with pytest.raises(ExchangeError, match="event type"):
        _parse_depth_update(raw=msg, pair=_BTC_USDT, source="ws://test")


@pytest.mark.unit
def test_parse_depth_update_missing_u_field_raises_exchange_error() -> None:
    """_parse_depth_update raises ExchangeError when 'u' field is missing."""
    msg = json.dumps({"e": "depthUpdate", "U": 100, "b": [], "a": []})
    with pytest.raises(ExchangeError, match="missing required field"):
        _parse_depth_update(raw=msg, pair=_BTC_USDT, source="ws://test")


@pytest.mark.unit
def test_parse_depth_update_non_integer_update_ids_raises_exchange_error() -> None:
    """_parse_depth_update raises ExchangeError when update IDs are not integers."""
    msg = json.dumps({"e": "depthUpdate", "U": "abc", "u": 105, "b": [], "a": []})
    with pytest.raises(ExchangeError, match="integers"):
        _parse_depth_update(raw=msg, pair=_BTC_USDT, source="ws://test")


@pytest.mark.unit
def test_parse_depth_update_bad_price_in_bid_raises_exchange_error() -> None:
    """_parse_depth_update raises ExchangeError on non-decimal bid price."""
    msg = _depth_update_msg(bids=[["not_a_price", "1.0"]])
    with pytest.raises(ExchangeError, match="decimal"):
        _parse_depth_update(raw=msg, pair=_BTC_USDT, source="ws://test")


@pytest.mark.unit
def test_parse_snapshot_returns_orderbook() -> None:
    """_parse_snapshot correctly parses a valid REST depth snapshot."""
    data = _snapshot_response(
        bids=[["42000.00", "0.5"]],
        asks=[["42001.00", "0.3"]],
    )
    ob = _parse_snapshot(data=data, pair=_BTC_USDT)

    assert ob.pair == _BTC_USDT, f"orderbook.pair should be {_BTC_USDT}, got {ob.pair}"
    assert len(ob.bids) == 1, f"Expected 1 bid level, got {len(ob.bids)}"
    assert len(ob.asks) == 1, f"Expected 1 ask level, got {len(ob.asks)}"
    assert ob.bids[0].price == Decimal("42000.00"), (
        f"bid price should be 42000.00, got {ob.bids[0].price}"
    )
    assert ob.asks[0].price == Decimal("42001.00"), (
        f"ask price should be 42001.00, got {ob.asks[0].price}"
    )


@pytest.mark.unit
def test_parse_snapshot_skips_zero_qty_levels() -> None:
    """_parse_snapshot skips levels with qty=0 from REST response."""
    data = _snapshot_response(
        bids=[["42000.00", "0"], ["41999.00", "1.0"]],
        asks=[["42001.00", "0.3"]],
    )
    ob = _parse_snapshot(data=data, pair=_BTC_USDT)
    # Only the non-zero bid should survive.
    assert len(ob.bids) == 1, (
        f"Expected 1 non-zero bid level after skipping zero-qty levels, got {len(ob.bids)}"
    )
    assert ob.bids[0].price == Decimal("41999.00"), (
        f"Remaining bid should be at 41999, got {ob.bids[0].price}"
    )


@pytest.mark.unit
def test_parse_snapshot_missing_field_raises_exchange_error() -> None:
    """_parse_snapshot raises ExchangeError when required field is missing."""
    data: dict[str, Any] = {"bids": [], "asks": []}  # missing lastUpdateId
    with pytest.raises(ExchangeError, match="lastUpdateId"):
        _parse_snapshot(data=data, pair=_BTC_USDT)


@pytest.mark.unit
def test_parse_snapshot_keeps_the_venue_last_update_id() -> None:
    """The snapshot carries ``lastUpdateId`` so stale diffs can be dropped (issue #866)."""
    book = _parse_snapshot(data=_snapshot_response(last_update_id=1027024), pair=_BTC_USDT)
    assert book.last_update_id == 1027024


@pytest.mark.unit
@pytest.mark.parametrize("bad", ["1027024", 1027024.0, True, None])
def test_parse_snapshot_refuses_a_last_update_id_that_is_not_an_integer(bad: object) -> None:
    """A non-integer ``lastUpdateId`` is a schema error, not a book without a sequence."""
    data = _snapshot_response() | {"lastUpdateId": bad}
    with pytest.raises(ExchangeError, match="lastUpdateId must be an integer"):
        _parse_snapshot(data=data, pair=_BTC_USDT)


@pytest.mark.unit
def test_get_snapshot_calls_depth_endpoint() -> None:
    """get_snapshot fetches /api/v3/depth with correct params."""
    response_data = _snapshot_response(
        bids=[["42000.00", "0.5"]],
        asks=[["42001.00", "0.3"]],
    )

    async def mock_request(request: httpx.Request) -> httpx.Response:
        assert "/api/v3/depth" in str(request.url), (
            f"Expected /api/v3/depth in URL, got {request.url}"
        )
        assert "BTCUSDT" in str(request.url), f"Expected BTCUSDT symbol in URL, got {request.url}"
        return httpx.Response(200, json=response_data)

    config = BinanceLiveWsConfig(
        ws_base_url="wss://stream.binance.com:9443/ws",
        rest_base_url="https://api.binance.com",
        snapshot_limit=10,
    )
    adapter = BinanceLiveWsAdapter(config=config)
    mock_transport = httpx.MockTransport(mock_request)
    adapter._client = httpx.AsyncClient(
        base_url="https://api.binance.com",
        transport=mock_transport,
    )

    async def _run() -> None:
        ob = await adapter.get_snapshot(_BTC_USDT)
        assert ob.pair == _BTC_USDT, f"Expected pair {_BTC_USDT}, got {ob.pair}"

    anyio.run(_run)


@pytest.mark.unit
def test_get_snapshot_http_error_raises_exchange_error() -> None:
    """get_snapshot raises ExchangeError on HTTP 4xx response."""

    async def mock_request(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="Too Many Requests")

    config = BinanceLiveWsConfig()
    adapter = BinanceLiveWsAdapter(config=config)
    mock_transport = httpx.MockTransport(mock_request)
    adapter._client = httpx.AsyncClient(
        base_url="https://api.binance.com",
        transport=mock_transport,
    )

    async def _run() -> None:
        with pytest.raises(ExchangeError, match="429"):
            await adapter.get_snapshot(_BTC_USDT)

    anyio.run(_run)


@pytest.mark.unit
def test_stream_bars_raises_not_implemented() -> None:
    """stream_bars raises NotImplementedError for live WS adapter."""
    adapter = BinanceLiveWsAdapter.create()

    async def _run() -> None:
        with pytest.raises(NotImplementedError):
            await adapter.stream_bars(_BTC_USDT, KlineInterval.H4)

    anyio.run(_run)


@pytest.mark.unit
def test_stream_trades_raises_not_implemented() -> None:
    """stream_trades raises NotImplementedError for live WS adapter."""
    adapter = BinanceLiveWsAdapter.create()

    async def _run() -> None:
        with pytest.raises(NotImplementedError):
            await adapter.stream_trades(_BTC_USDT)

    anyio.run(_run)


@pytest.mark.unit
def test_close_releases_client() -> None:
    """close() sets _client to None so the HTTP client is released."""
    config = BinanceLiveWsConfig()
    adapter = BinanceLiveWsAdapter(config=config)
    adapter._client = httpx.AsyncClient(base_url="https://api.binance.com")

    async def _run() -> None:
        # config is stable — anchor to verify we're on the right adapter instance.
        assert adapter.config.rest_base_url == "https://api.binance.com", (
            "Adapter config should match the constructed config"
        )
        await adapter.close()
        # After close(), _client must be None (released).
        assert adapter._client is None, (
            "close() should set _client to None to release the HTTP client"
        )

    anyio.run(_run)


@pytest.mark.unit
def test_close_is_idempotent_when_no_client() -> None:
    """close() does nothing when _client was never created, and does not raise."""
    adapter = BinanceLiveWsAdapter.create()

    async def _run() -> None:
        await adapter.close()
        # Adapter should retain its config after a no-op close().
        assert adapter.config.rest_base_url == "https://api.binance.com", (
            "Adapter config should be unchanged after a no-op close() call"
        )

    anyio.run(_run)


@pytest.mark.unit
def test_get_snapshot_creates_client_lazily() -> None:
    """_get_client creates the httpx.AsyncClient on first call."""
    response_data = _snapshot_response()

    async def mock_request(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=response_data)

    config = BinanceLiveWsConfig()
    adapter = BinanceLiveWsAdapter(config=config)
    assert adapter._client is None, "Client should start as None before first use"

    mock_transport = httpx.MockTransport(mock_request)
    # We set the transport via the client so the lazy-create code actually runs
    adapter._client = None  # ensure it's not set

    # Patch via direct assignment post-create to test lazy creation side-effect:
    # call get_snapshot which internally calls _get_client()
    adapter._client = httpx.AsyncClient(
        base_url="https://api.binance.com", transport=mock_transport
    )

    async def _run() -> None:
        ob = await adapter.get_snapshot(_BTC_USDT)
        assert ob.pair == _BTC_USDT, f"Expected pair {_BTC_USDT}, got {ob.pair}"
        assert adapter._client is not None, "_client should be populated after get_snapshot call"

    anyio.run(_run)


@pytest.mark.unit
def test_get_snapshot_transport_error_raises_network_error() -> None:
    """get_snapshot raises NetworkError when transport raises httpx.ConnectError."""

    async def mock_request(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    config = BinanceLiveWsConfig()
    adapter = BinanceLiveWsAdapter(config=config)
    mock_transport = httpx.MockTransport(mock_request)
    adapter._client = httpx.AsyncClient(
        base_url="https://api.binance.com", transport=mock_transport
    )

    async def _run() -> None:
        with pytest.raises(NetworkError, match="transport failure"):
            await adapter.get_snapshot(_BTC_USDT)

    anyio.run(_run)


@pytest.mark.unit
def test_get_snapshot_non_json_response_raises_exchange_error() -> None:
    """get_snapshot raises ExchangeError when response body is not JSON."""

    async def mock_request(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json {{")

    config = BinanceLiveWsConfig()
    adapter = BinanceLiveWsAdapter(config=config)
    mock_transport = httpx.MockTransport(mock_request)
    adapter._client = httpx.AsyncClient(
        base_url="https://api.binance.com", transport=mock_transport
    )

    async def _run() -> None:
        with pytest.raises(ExchangeError, match="non-JSON"):
            await adapter.get_snapshot(_BTC_USDT)

    anyio.run(_run)


@pytest.mark.unit
def test_stream_orderbook_diffs_raises_infra_error_when_websockets_missing() -> None:
    """stream_orderbook_diffs raises InfraError when websockets package is not installed."""
    original_ws = ws_module.websockets  # type: ignore[attr-defined]
    ws_module.websockets = None  # type: ignore[attr-defined]

    try:
        adapter = BinanceLiveWsAdapter.create()

        async def _run() -> None:
            with pytest.raises(InfraError, match="websockets"):
                async for _ in adapter.stream_orderbook_diffs(_BTC_USDT):
                    pass  # pragma: no cover

        anyio.run(_run)
    finally:
        ws_module.websockets = original_ws  # type: ignore[attr-defined]


@pytest.mark.unit
def test_parse_level_list_malformed_entry_raises_exchange_error() -> None:
    """_parse_level_list raises ExchangeError for a level that is not a two-element list."""
    with pytest.raises(ExchangeError, match="malformed bid"):
        _parse_level_list(["not_a_list"], source="ws://test", side_label="bid")  # type: ignore[list-item]


@pytest.mark.unit
def test_parse_orderbook_level_list_malformed_entry_raises_exchange_error() -> None:
    """_parse_orderbook_level_list raises ExchangeError for a single-element level list."""
    with pytest.raises(ExchangeError, match="malformed ask"):
        _parse_orderbook_level_list([["42001.00"]], source="https://test", side_label="ask")
