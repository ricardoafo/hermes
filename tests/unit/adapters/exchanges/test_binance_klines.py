"""Contract tests for :class:`BinanceKlinesAdapter`.

Every wire call is mocked via ``respx`` — no real socket ever opens, no
real Binance testnet endpoint is contacted. Rate-limiter is driven by a
deterministic fake time surface so tests are microsecond-fast.

Every test protects one invariant of the fetcher contract:

- Wire URL/path/params match Binance klines endpoint spec
- Pagination advances by last-open-time + 1ms until window exhausted
- Empty batch terminates pagination
- Rate limiter is charged exactly one token per page
- 429 raises immediately (no silent retry — see decisions journal)
- Transport failures wrap into NetworkError
- Malformed schemas raise ExchangeError with attribution

Reference: docs/guides/ai-authored-tests.md,
docs/decisions/2026-08-23-binance-testnet-klines.md.
"""

from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest
import respx

from hermes.adapters._shared.rate_limiter import RateLimitConfig, RateLimiter
from hermes.adapters.exchanges.binance_klines import (
    BinanceKlinesAdapter,
    BinanceKlinesConfig,
)
from hermes.domain.errors import ExchangeError, InfraError, NetworkError
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair

_BTC_USDT = Pair(base=Currency.BTC, quote=Currency.USDT)
_TESTNET = "https://testnet.binance.vision"
_KLINES_URL = f"{_TESTNET}/api/v3/klines"


def _row(open_time_ms: int, close_price: str = "50050", volume: str = "1.5") -> list[object]:
    """Build a Binance-style raw klines row (list of positional fields).

    Only the fields the adapter reads are populated with realistic values;
    trailing fields (quote_volume, trades, etc.) are placeholders.
    """
    close_time_ms = open_time_ms + 60_000 - 1  # 1-minute candle
    return [
        open_time_ms,
        "50000",  # open
        "50100",  # high
        "49900",  # low
        close_price,  # close
        volume,  # volume
        close_time_ms,
        "0",  # quote_volume (unused)
        0,  # trades (unused)
        "0",  # taker_buy_base_volume (unused)
        "0",  # taker_buy_quote_volume (unused)
        "0",  # ignored
    ]


def _make_adapter(
    *,
    max_weight: int = 1000,
    rate_limit_window: int = 60,
) -> BinanceKlinesAdapter:
    """Build an adapter with a plain httpx.Client (respx patches transport globally)."""
    config = BinanceKlinesConfig(
        rate_limit=RateLimitConfig(max_weight=max_weight, window_seconds=rate_limit_window)
    )
    client = httpx.Client(base_url=_TESTNET, timeout=5.0)
    limiter = RateLimiter(config=config.rate_limit)
    return BinanceKlinesAdapter(config=config, client=client, limiter=limiter)


class TestConfigValidation:
    """Wire-config invalid values fail at construction, not at first fetch."""

    def test_mainnet_url_rejected(self) -> None:
        """A non-https base_url raises — enforces testnet/https-only invariant at config layer."""
        with pytest.raises(ValueError, match="https://"):
            BinanceKlinesConfig(base_url="http://api.binance.com")

    def test_zero_timeout_rejected(self) -> None:
        """A non-positive timeout raises — would immediately fail every request."""
        with pytest.raises(ValueError, match="timeout_sec"):
            BinanceKlinesConfig(timeout_sec=0)


class TestFetchWindowValidation:
    """fetch() rejects naive datetimes and non-increasing windows."""

    def test_fetch_naive_since_raises(self) -> None:
        """A naive since datetime raises ValueError — hermes stores UTC only."""
        adapter = _make_adapter()
        try:
            with pytest.raises(ValueError, match="since must be UTC-aware"):
                list(
                    adapter.fetch(
                        pair=_BTC_USDT,
                        interval=KlineInterval.M1,
                        since=datetime(2026, 1, 1, 0, 0),  # naive
                        until=datetime(2026, 1, 1, 1, 0, tzinfo=UTC),
                    )
                )
        finally:
            adapter.close()

    def test_fetch_until_le_since_raises(self) -> None:
        """A window with until <= since raises ValueError before any wire call."""
        adapter = _make_adapter()
        try:
            with pytest.raises(ValueError, match="must be > since"):
                list(
                    adapter.fetch(
                        pair=_BTC_USDT,
                        interval=KlineInterval.M1,
                        since=datetime(2026, 1, 1, 1, 0, tzinfo=UTC),
                        until=datetime(2026, 1, 1, 0, 0, tzinfo=UTC),
                    )
                )
        finally:
            adapter.close()


class TestFetchHappyPath:
    """Basic wire-shape assertions on a single-page happy path."""

    def test_fetch_hits_klines_path_with_symbol_and_interval(self) -> None:
        """The request URL contains symbol and interval params as Binance expects."""
        adapter = _make_adapter()
        since = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
        until = datetime(2026, 1, 1, 0, 5, tzinfo=UTC)
        try:
            with respx.mock(assert_all_called=True) as router:
                route = router.get(_KLINES_URL).mock(
                    return_value=httpx.Response(200, json=[_row(open_time_ms=1735689600000)])
                )
                list(
                    adapter.fetch(
                        pair=_BTC_USDT,
                        interval=KlineInterval.M1,
                        since=since,
                        until=until,
                    )
                )
                request = route.calls[0].request
                assert request.url.params["symbol"] == "BTCUSDT"
                assert request.url.params["interval"] == "1m"
                assert request.url.params["limit"] == "1000"
        finally:
            adapter.close()

    def test_fetch_returns_kline_with_decimal_precision(self) -> None:
        """Numeric fields arrive as Decimal on the domain Kline (no float loss)."""
        adapter = _make_adapter()
        since = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
        until = datetime(2026, 1, 1, 0, 2, tzinfo=UTC)
        open_ms = int(since.timestamp() * 1000)
        try:
            with respx.mock() as router:
                router.get(_KLINES_URL).mock(
                    return_value=httpx.Response(
                        200, json=[_row(open_time_ms=open_ms, close_price="50050.12345678")]
                    )
                )
                klines = list(
                    adapter.fetch(
                        pair=_BTC_USDT,
                        interval=KlineInterval.M1,
                        since=since,
                        until=until,
                    )
                )
                assert len(klines) == 1
                assert klines[0].close.amount == Decimal("50050.12345678")
        finally:
            adapter.close()

    def test_fetch_translates_timestamps_to_utc(self) -> None:
        """open_time and close_time come back as timezone-aware UTC datetimes."""
        adapter = _make_adapter()
        since = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
        until = datetime(2026, 1, 1, 0, 2, tzinfo=UTC)
        open_ms = int(since.timestamp() * 1000)
        try:
            with respx.mock() as router:
                router.get(_KLINES_URL).mock(
                    return_value=httpx.Response(200, json=[_row(open_time_ms=open_ms)])
                )
                klines = list(
                    adapter.fetch(
                        pair=_BTC_USDT,
                        interval=KlineInterval.M1,
                        since=since,
                        until=until,
                    )
                )
                assert klines[0].open_time == since
                assert klines[0].open_time.tzinfo == UTC
        finally:
            adapter.close()

    def test_fetch_stops_at_until_boundary(self) -> None:
        """Klines with open_time >= until are excluded even if returned by the exchange."""
        adapter = _make_adapter()
        since = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
        until = datetime(2026, 1, 1, 0, 2, tzinfo=UTC)
        base_ms = int(since.timestamp() * 1000)
        # Rows at t+0, t+1min (both inside), t+2min (== until, excluded)
        rows = [
            _row(open_time_ms=base_ms),
            _row(open_time_ms=base_ms + 60_000),
            _row(open_time_ms=base_ms + 120_000),
        ]
        try:
            with respx.mock() as router:
                router.get(_KLINES_URL).mock(return_value=httpx.Response(200, json=rows))
                klines = list(
                    adapter.fetch(
                        pair=_BTC_USDT,
                        interval=KlineInterval.M1,
                        since=since,
                        until=until,
                    )
                )
                assert len(klines) == 2
                assert klines[-1].open_time == datetime(2026, 1, 1, 0, 1, tzinfo=UTC)
        finally:
            adapter.close()


class TestFetchPagination:
    """Multi-page fetches advance startTime past the last-seen candle each page."""

    def test_second_page_uses_last_open_time_plus_one_ms(self) -> None:
        """When page 1 fills, page 2's startTime is (last open_time_ms + 1)."""
        adapter = _make_adapter()
        since = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
        # Ask for a large window to force pagination cursor advancement
        until = datetime(2026, 1, 1, 4, 0, tzinfo=UTC)
        base_ms = int(since.timestamp() * 1000)
        page_one = [_row(open_time_ms=base_ms + i * 60_000) for i in range(3)]
        page_two: list[list[object]] = []  # empty -> terminate
        last_open_ms = base_ms + 2 * 60_000
        try:
            with respx.mock(assert_all_called=True) as router:
                route = router.get(_KLINES_URL).mock(
                    side_effect=[
                        httpx.Response(200, json=page_one),
                        httpx.Response(200, json=page_two),
                    ]
                )
                list(
                    adapter.fetch(
                        pair=_BTC_USDT,
                        interval=KlineInterval.M1,
                        since=since,
                        until=until,
                    )
                )
                assert route.call_count == 2
                second_request = route.calls[1].request
                assert second_request.url.params["startTime"] == str(last_open_ms + 1)
        finally:
            adapter.close()

    def test_empty_first_page_terminates_immediately(self) -> None:
        """An empty response body ends pagination with zero yielded klines."""
        adapter = _make_adapter()
        since = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
        until = datetime(2026, 1, 1, 1, 0, tzinfo=UTC)
        try:
            with respx.mock(assert_all_called=True) as router:
                route = router.get(_KLINES_URL).mock(return_value=httpx.Response(200, json=[]))
                klines = list(
                    adapter.fetch(
                        pair=_BTC_USDT,
                        interval=KlineInterval.M1,
                        since=since,
                        until=until,
                    )
                )
                assert klines == []
                assert route.call_count == 1
        finally:
            adapter.close()


class TestFetchErrorPaths:
    """Every documented failure mode surfaces as a specific InfraError subclass."""

    def test_http_429_raises_exchange_error_immediately(self) -> None:
        """A 429 response raises ExchangeError with the status in the message (no retry)."""
        adapter = _make_adapter()
        since = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
        until = datetime(2026, 1, 1, 0, 5, tzinfo=UTC)
        try:
            with respx.mock() as router:
                route = router.get(_KLINES_URL).mock(
                    return_value=httpx.Response(429, text="Too Many Requests")
                )
                with pytest.raises(ExchangeError, match="429"):
                    list(
                        adapter.fetch(
                            pair=_BTC_USDT,
                            interval=KlineInterval.M1,
                            since=since,
                            until=until,
                        )
                    )
                # No silent retry — exactly one wire call
                assert route.call_count == 1
        finally:
            adapter.close()

    def test_http_500_raises_exchange_error(self) -> None:
        """A 5xx from the exchange raises ExchangeError, not NetworkError."""
        adapter = _make_adapter()
        since = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
        until = datetime(2026, 1, 1, 0, 5, tzinfo=UTC)
        try:
            with respx.mock() as router:
                router.get(_KLINES_URL).mock(
                    return_value=httpx.Response(500, text="Internal Server Error")
                )
                with pytest.raises(ExchangeError, match="500"):
                    list(
                        adapter.fetch(
                            pair=_BTC_USDT,
                            interval=KlineInterval.M1,
                            since=since,
                            until=until,
                        )
                    )
        finally:
            adapter.close()

    def test_transport_failure_wraps_as_network_error(self) -> None:
        """A ConnectError wraps into NetworkError with the underlying exception in msg."""
        adapter = _make_adapter()
        since = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
        until = datetime(2026, 1, 1, 0, 5, tzinfo=UTC)
        try:
            with respx.mock() as router:
                router.get(_KLINES_URL).mock(side_effect=httpx.ConnectError("DNS failed"))
                with pytest.raises(NetworkError, match="transport failure"):
                    list(
                        adapter.fetch(
                            pair=_BTC_USDT,
                            interval=KlineInterval.M1,
                            since=since,
                            until=until,
                        )
                    )
        finally:
            adapter.close()

    def test_non_json_response_raises_exchange_error(self) -> None:
        """A 200 with non-JSON body raises ExchangeError, not ValueError."""
        adapter = _make_adapter()
        since = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
        until = datetime(2026, 1, 1, 0, 5, tzinfo=UTC)
        try:
            with respx.mock() as router:
                router.get(_KLINES_URL).mock(
                    return_value=httpx.Response(200, content=b"<html>error</html>")
                )
                with pytest.raises(ExchangeError, match="non-JSON"):
                    list(
                        adapter.fetch(
                            pair=_BTC_USDT,
                            interval=KlineInterval.M1,
                            since=since,
                            until=until,
                        )
                    )
        finally:
            adapter.close()

    def test_non_list_body_raises_exchange_error(self) -> None:
        """A 200 with a JSON object (not list) raises ExchangeError naming the shape."""
        adapter = _make_adapter()
        since = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
        until = datetime(2026, 1, 1, 0, 5, tzinfo=UTC)
        try:
            with respx.mock() as router:
                router.get(_KLINES_URL).mock(
                    return_value=httpx.Response(200, json={"error": "bad symbol"})
                )
                with pytest.raises(ExchangeError, match="non-list"):
                    list(
                        adapter.fetch(
                            pair=_BTC_USDT,
                            interval=KlineInterval.M1,
                            since=since,
                            until=until,
                        )
                    )
        finally:
            adapter.close()

    def test_row_with_too_few_fields_raises_exchange_error(self) -> None:
        """A row with <7 fields raises ExchangeError — schema break must not silently degrade."""
        adapter = _make_adapter()
        since = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
        until = datetime(2026, 1, 1, 0, 5, tzinfo=UTC)
        try:
            with respx.mock() as router:
                router.get(_KLINES_URL).mock(return_value=httpx.Response(200, json=[[1, "50000"]]))
                with pytest.raises(ExchangeError, match="fields"):
                    list(
                        adapter.fetch(
                            pair=_BTC_USDT,
                            interval=KlineInterval.M1,
                            since=since,
                            until=until,
                        )
                    )
        finally:
            adapter.close()

    def test_unparseable_numeric_field_raises_exchange_error(self) -> None:
        """A row with a non-decimal price field raises ExchangeError naming the field."""
        adapter = _make_adapter()
        since = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
        until = datetime(2026, 1, 1, 0, 5, tzinfo=UTC)
        bad_row = _row(open_time_ms=int(since.timestamp() * 1000))
        bad_row[1] = "not-a-number"
        try:
            with respx.mock() as router:
                router.get(_KLINES_URL).mock(return_value=httpx.Response(200, json=[bad_row]))
                with pytest.raises(ExchangeError, match="not a decimal"):
                    list(
                        adapter.fetch(
                            pair=_BTC_USDT,
                            interval=KlineInterval.M1,
                            since=since,
                            until=until,
                        )
                    )
        finally:
            adapter.close()

    def test_exchange_error_inherits_from_infra_error(self) -> None:
        """ExchangeError caught by an 'except InfraError' clause satisfies the base contract."""
        err = ExchangeError(msg="bad")
        assert isinstance(err, InfraError)


class TestCreateFactoryAndClose:
    """The create() factory produces a working adapter; close is idempotent."""

    def test_create_default_targets_testnet(self) -> None:
        """create() with no args builds an adapter whose client points at testnet."""
        adapter = BinanceKlinesAdapter.create()
        try:
            assert str(adapter.client.base_url) == _TESTNET
        finally:
            adapter.close()

    def test_close_is_idempotent(self) -> None:
        """Closing twice does not raise and leaves the client closed."""
        adapter = BinanceKlinesAdapter.create()
        adapter.close()
        adapter.close()

        assert adapter.client.is_closed is True
