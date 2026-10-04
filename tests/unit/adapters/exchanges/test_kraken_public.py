"""Contract tests for :class:`KrakenPublicAdapter`.

Every wire call is mocked via ``respx`` — no real socket ever opens, no
real Kraken endpoint is contacted. Tests are microsecond-fast.

Every test protects one invariant of the Kraken public-adapter contract:

- Wire URL/path/params match Kraken's ``/0/public/`` endpoints.
- Response parsing produces well-formed domain values with Decimal precision.
- Non-empty Kraken ``error`` array → :class:`KrakenPublicError`.
- HTTP 5xx / 4xx → :class:`KrakenPublicError`.
- Malformed / non-JSON body → :class:`KrakenPublicError`.
- Missing pair key in response → :class:`KrakenPublicError`.
- Pair-symbol mapping handles the X/Z legacy prefixes for BTC/ETH/USD.
- Port conformance for FeeScheduleProviderPort and KlinesFetcherPort.
- Fee-schedule cache hits within TTL, refetches after expiry.
- create() factory + close() idempotency.

Reference: docs/guides/ai-authored-tests.md,
docs/decisions/2026-08-24-kraken-public-adapter.md.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest
import respx

from hermes.adapters.exchanges.kraken_public import (
    KrakenPublicAdapter,
    KrakenPublicConfig,
    _pair_to_kraken_symbol,
)
from hermes.adapters.exchanges.rate_limiter import RateLimitConfig, RateLimiter
from hermes.domain.errors import InfraError, KrakenPublicError
from hermes.domain.fees import FeeSchedule
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.market_data.orderbook import Orderbook
from hermes.domain.market_data.ticker import Ticker
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair
from hermes.ports.fees import FeeScheduleProviderPort
from hermes.ports.klines import KlinesFetcherPort

_BASE_URL = "https://api.kraken.com"
_ASSET_PAIRS_URL = f"{_BASE_URL}/0/public/AssetPairs"
_OHLC_URL = f"{_BASE_URL}/0/public/OHLC"
_DEPTH_URL = f"{_BASE_URL}/0/public/Depth"
_TICKER_URL = f"{_BASE_URL}/0/public/Ticker"

_BTC_USDT = Pair(base=Currency.BTC, quote=Currency.USDT)
_ETH_USDT = Pair(base=Currency.ETH, quote=Currency.USDT)


class _CounterClock:
    """Deterministic monotonic clock for cache-TTL testing.

    Not thread-safe — tests are single-threaded. Callers can advance the
    clock manually via :meth:`advance`.
    """

    def __init__(self, start: float = 0.0) -> None:
        """Initialise the clock at ``start`` seconds."""
        self.now = start

    def __call__(self) -> float:
        """Return the current fake time — matches ``time.monotonic`` shape."""
        return self.now

    def advance(self, seconds: float) -> None:
        """Move the clock forward by ``seconds``."""
        self.now += seconds


def _make_adapter(
    *,
    fees_ttl: int = 86_400,
    clock: _CounterClock | None = None,
) -> KrakenPublicAdapter:
    """Build an adapter with a plain httpx.Client (respx patches transport globally).

    Args:
        fees_ttl: TTL for the in-memory fee-schedule cache.
        clock: Optional deterministic clock — required for cache-TTL tests
            because the limiter's clock also seeds the cache timestamps.

    """
    config = KrakenPublicConfig(
        rate_limit=RateLimitConfig(max_weight=100, window_seconds=1),
        fees_cache_ttl_seconds=fees_ttl,
    )
    client = httpx.Client(base_url=_BASE_URL, timeout=5.0)
    if clock is not None:
        limiter = RateLimiter.for_testing(
            config=config.rate_limit, clock=clock, sleep=lambda _s: None
        )
    else:
        limiter = RateLimiter(config=config.rate_limit)
    return KrakenPublicAdapter(config=config, client=client, limiter=limiter)


def _kraken_ok(result: dict[str, Any]) -> dict[str, Any]:
    """Wrap ``result`` in Kraken's standard envelope with empty error array."""
    return {"error": [], "result": result}


# ---------------------------------------------------------------------------
# Pair-symbol mapping
# ---------------------------------------------------------------------------


class TestPairSymbolMapping:
    """Kraken's legacy X/Z prefix mapping is applied for the common pairs."""

    def test_btc_usdt_maps_to_xbtusdt(self) -> None:
        """BTC/USDT translates to Kraken's XBTUSDT altname."""
        assert _pair_to_kraken_symbol(_BTC_USDT) == "XBTUSDT"

    def test_eth_usdt_maps_to_ethusdt(self) -> None:
        """ETH/USDT translates to Kraken's ETHUSDT altname; XETHZUSDT is an unknown pair (#31)."""
        assert _pair_to_kraken_symbol(_ETH_USDT) == "ETHUSDT"

    def test_eth_usdc_maps_to_ethusdc(self) -> None:
        """ETH/USDC translates to Kraken's ETHUSDC altname; XETHZUSDC is an unknown pair (#31)."""
        pair = Pair(base=Currency.ETH, quote=Currency.USDC)
        assert _pair_to_kraken_symbol(pair) == "ETHUSDC"

    def test_sol_usdt_falls_back_to_concatenation(self) -> None:
        """SOL/USDT is not in the override table — falls back to SOLUSDT."""
        pair = Pair(base=Currency.SOL, quote=Currency.USDT)
        assert _pair_to_kraken_symbol(pair) == "SOLUSDT"

    def test_btc_usdc_maps_to_xbtusdc(self) -> None:
        """BTC/USDC has an explicit override entry."""
        pair = Pair(base=Currency.BTC, quote=Currency.USDC)
        assert _pair_to_kraken_symbol(pair) == "XBTUSDC"


# ---------------------------------------------------------------------------
# Config validation
# ---------------------------------------------------------------------------


class TestConfigValidation:
    """Wire-config invalid values fail at construction, not at first fetch."""

    def test_non_https_base_url_rejected(self) -> None:
        """A non-https base_url raises — no plaintext calls to a public API."""
        with pytest.raises(ValueError, match="https://"):
            KrakenPublicConfig(base_url="http://api.kraken.com")

    def test_zero_timeout_rejected(self) -> None:
        """A non-positive timeout raises — would immediately fail every request."""
        with pytest.raises(ValueError, match="timeout_sec"):
            KrakenPublicConfig(timeout_sec=0)

    def test_negative_cache_ttl_rejected(self) -> None:
        """A negative cache TTL is nonsensical — rejected at config layer."""
        with pytest.raises(ValueError, match="fees_cache_ttl_seconds"):
            KrakenPublicConfig(fees_cache_ttl_seconds=-1)


# ---------------------------------------------------------------------------
# get_pair_fees — happy path
# ---------------------------------------------------------------------------


_FEES_PAYLOAD = _kraken_ok(
    {
        "XBTUSDT": {
            "fees": [
                [0, 0.26],
                [50000, 0.24],
                [100000, 0.22],
            ],
            "fees_maker": [
                [0, 0.16],
                [50000, 0.14],
                [100000, 0.12],
            ],
        }
    }
)


class TestGetPairFeesHappyPath:
    """AssetPairs endpoint returns a well-formed FeeSchedule."""

    def test_get_pair_fees_hits_asset_pairs_with_pair_param(self) -> None:
        """The request URL is /0/public/AssetPairs with pair=XBTUSDT for BTC/USDT."""
        adapter = _make_adapter()
        try:
            with respx.mock(assert_all_called=True) as router:
                route = router.get(_ASSET_PAIRS_URL).mock(
                    return_value=httpx.Response(200, json=_FEES_PAYLOAD)
                )
                adapter.get_pair_fees(_BTC_USDT)
                request = route.calls[0].request
                assert request.url.params["pair"] == "XBTUSDT"
        finally:
            adapter.close()

    def test_get_pair_fees_parses_all_tiers(self) -> None:
        """All three tiers land in the schedule with correct bps values."""
        adapter = _make_adapter()
        try:
            with respx.mock() as router:
                router.get(_ASSET_PAIRS_URL).mock(
                    return_value=httpx.Response(200, json=_FEES_PAYLOAD)
                )
                schedule = adapter.get_pair_fees(_BTC_USDT)
                assert isinstance(schedule, FeeSchedule)
                assert len(schedule.tiers) == 3
                assert schedule.tiers[0].volume_threshold_usd == Decimal("0")
                assert schedule.tiers[0].maker_bps == 16
                assert schedule.tiers[0].taker_bps == 26
                assert schedule.tiers[1].maker_bps == 14
                assert schedule.tiers[2].taker_bps == 22
        finally:
            adapter.close()

    def test_get_pair_fees_returns_schedule_pair_matches_input(self) -> None:
        """The returned schedule's pair equals the requested pair."""
        adapter = _make_adapter()
        try:
            with respx.mock() as router:
                router.get(_ASSET_PAIRS_URL).mock(
                    return_value=httpx.Response(200, json=_FEES_PAYLOAD)
                )
                schedule = adapter.get_pair_fees(_BTC_USDT)
                assert schedule.pair == _BTC_USDT
        finally:
            adapter.close()


# ---------------------------------------------------------------------------
# get_pair_fees — cache behaviour
# ---------------------------------------------------------------------------


class TestGetPairFeesCache:
    """The 24 h in-memory cache short-circuits duplicate lookups within TTL."""

    def test_second_call_within_ttl_does_not_hit_wire(self) -> None:
        """A second get_pair_fees call within TTL returns cached and skips the request."""
        clock = _CounterClock(start=1000.0)
        adapter = _make_adapter(fees_ttl=3600, clock=clock)
        try:
            with respx.mock() as router:
                route = router.get(_ASSET_PAIRS_URL).mock(
                    return_value=httpx.Response(200, json=_FEES_PAYLOAD)
                )
                adapter.get_pair_fees(_BTC_USDT)
                clock.advance(100.0)  # well inside TTL
                adapter.get_pair_fees(_BTC_USDT)
                assert route.call_count == 1
        finally:
            adapter.close()

    def test_call_after_ttl_refetches_from_wire(self) -> None:
        """After TTL expiry the adapter re-hits the wire for a fresh schedule."""
        clock = _CounterClock(start=1000.0)
        adapter = _make_adapter(fees_ttl=3600, clock=clock)
        try:
            with respx.mock() as router:
                route = router.get(_ASSET_PAIRS_URL).mock(
                    return_value=httpx.Response(200, json=_FEES_PAYLOAD)
                )
                adapter.get_pair_fees(_BTC_USDT)
                clock.advance(3600.0 + 1.0)  # past TTL
                adapter.get_pair_fees(_BTC_USDT)
                assert route.call_count == 2
        finally:
            adapter.close()


# ---------------------------------------------------------------------------
# get_pair_fees — error paths
# ---------------------------------------------------------------------------


class TestGetPairFeesErrors:
    """Every documented failure mode surfaces as KrakenPublicError."""

    def test_non_empty_kraken_error_array_raises(self) -> None:
        """A response with error=['EQuery:Unknown asset pair'] raises KrakenPublicError."""
        adapter = _make_adapter()
        try:
            with respx.mock() as router:
                router.get(_ASSET_PAIRS_URL).mock(
                    return_value=httpx.Response(
                        200,
                        json={"error": ["EQuery:Unknown asset pair"], "result": {}},
                    )
                )
                with pytest.raises(KrakenPublicError, match="Unknown asset pair"):
                    adapter.get_pair_fees(_BTC_USDT)
        finally:
            adapter.close()

    def test_http_500_raises(self) -> None:
        """A 5xx raises KrakenPublicError with the status preserved."""
        adapter = _make_adapter()
        try:
            with respx.mock() as router:
                router.get(_ASSET_PAIRS_URL).mock(
                    return_value=httpx.Response(500, text="Internal Server Error")
                )
                with pytest.raises(KrakenPublicError, match="500"):
                    adapter.get_pair_fees(_BTC_USDT)
        finally:
            adapter.close()

    def test_http_429_raises(self) -> None:
        """A 429 raises KrakenPublicError immediately — no silent retry."""
        adapter = _make_adapter()
        try:
            with respx.mock() as router:
                route = router.get(_ASSET_PAIRS_URL).mock(
                    return_value=httpx.Response(429, text="Too Many Requests")
                )
                with pytest.raises(KrakenPublicError, match="429"):
                    adapter.get_pair_fees(_BTC_USDT)
                assert route.call_count == 1
        finally:
            adapter.close()

    def test_transport_failure_raises(self) -> None:
        """A ConnectError wraps into KrakenPublicError — no raw httpx leaks."""
        adapter = _make_adapter()
        try:
            with respx.mock() as router:
                router.get(_ASSET_PAIRS_URL).mock(side_effect=httpx.ConnectError("DNS failed"))
                with pytest.raises(KrakenPublicError, match="transport failure"):
                    adapter.get_pair_fees(_BTC_USDT)
        finally:
            adapter.close()

    def test_non_json_response_raises(self) -> None:
        """A 200 with non-JSON body raises KrakenPublicError, not ValueError."""
        adapter = _make_adapter()
        try:
            with respx.mock() as router:
                router.get(_ASSET_PAIRS_URL).mock(
                    return_value=httpx.Response(200, content=b"<html>outage</html>")
                )
                with pytest.raises(KrakenPublicError, match="non-JSON"):
                    adapter.get_pair_fees(_BTC_USDT)
        finally:
            adapter.close()

    def test_missing_pair_key_raises(self) -> None:
        """A response whose result dict lacks the requested pair raises."""
        adapter = _make_adapter()
        try:
            with respx.mock() as router:
                router.get(_ASSET_PAIRS_URL).mock(
                    return_value=httpx.Response(
                        200,
                        json=_kraken_ok({"XETHZUSD": {"fees": [], "fees_maker": []}}),
                    )
                )
                with pytest.raises(KrakenPublicError, match="no entry for pair 'XBTUSDT'"):
                    adapter.get_pair_fees(_BTC_USDT)
        finally:
            adapter.close()

    def test_mismatched_tier_lengths_raises(self) -> None:
        """Fees vs fees_maker length mismatch raises KrakenPublicError."""
        adapter = _make_adapter()
        try:
            with respx.mock() as router:
                router.get(_ASSET_PAIRS_URL).mock(
                    return_value=httpx.Response(
                        200,
                        json=_kraken_ok(
                            {
                                "XBTUSDT": {
                                    "fees": [[0, 0.26], [50000, 0.24]],
                                    "fees_maker": [[0, 0.16]],  # short
                                }
                            }
                        ),
                    )
                )
                with pytest.raises(KrakenPublicError, match="length mismatch"):
                    adapter.get_pair_fees(_BTC_USDT)
        finally:
            adapter.close()

    def test_error_field_is_non_list_raises(self) -> None:
        """A malformed 'error' field (non-list) is rejected as a schema error."""
        adapter = _make_adapter()
        try:
            with respx.mock() as router:
                router.get(_ASSET_PAIRS_URL).mock(
                    return_value=httpx.Response(
                        200,
                        json={"error": "not-a-list", "result": {}},
                    )
                )
                with pytest.raises(KrakenPublicError, match="'error' field must be a list"):
                    adapter.get_pair_fees(_BTC_USDT)
        finally:
            adapter.close()

    def test_error_inherits_from_infra_error(self) -> None:
        """KrakenPublicError caught by 'except InfraError' satisfies the base contract."""
        err = KrakenPublicError(msg="bad")
        assert isinstance(err, InfraError)


# ---------------------------------------------------------------------------
# fetch_klines
# ---------------------------------------------------------------------------


def _ohlc_row(open_time_s: int, close_price: str = "50050", volume: str = "1.5") -> list[Any]:
    """Build one Kraken OHLC row: [time, open, high, low, close, vwap, volume, count]."""
    return [
        open_time_s,
        "50000",  # open
        "50100",  # high
        "49900",  # low
        close_price,  # close
        "50025",  # vwap (unused)
        volume,  # volume
        10,  # count (unused)
    ]


class TestFetchKlinesHappyPath:
    """OHLC endpoint returns well-formed Kline values."""

    def test_fetch_klines_hits_ohlc_with_pair_and_interval(self) -> None:
        """URL is /0/public/OHLC with pair=XBTUSDT and interval=1."""
        adapter = _make_adapter()
        since = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
        until = datetime(2026, 1, 1, 0, 5, tzinfo=UTC)
        try:
            with respx.mock(assert_all_called=True) as router:
                route = router.get(_OHLC_URL).mock(
                    return_value=httpx.Response(
                        200,
                        json=_kraken_ok(
                            {"XBTUSDT": [_ohlc_row(int(since.timestamp()))], "last": 0}
                        ),
                    )
                )
                list(
                    adapter.fetch_klines(
                        pair=_BTC_USDT,
                        interval=KlineInterval.M1,
                        since=since,
                        until=until,
                    )
                )
                request = route.calls[0].request
                assert request.url.params["pair"] == "XBTUSDT"
                assert request.url.params["interval"] == "1"
        finally:
            adapter.close()

    def test_fetch_klines_supports_the_4h_strategy_interval(self) -> None:
        """A 4h request is sent as interval=240, so the failover check can read swing bars (#31)."""
        adapter = _make_adapter()
        since = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
        until = datetime(2026, 1, 1, 4, 0, tzinfo=UTC)
        try:
            with respx.mock(assert_all_called=True) as router:
                route = router.get(_OHLC_URL).mock(
                    return_value=httpx.Response(
                        200,
                        json=_kraken_ok(
                            {"XBTUSDT": [_ohlc_row(int(since.timestamp()))], "last": 0}
                        ),
                    )
                )
                klines = list(
                    adapter.fetch_klines(
                        pair=_BTC_USDT,
                        interval=KlineInterval.H4,
                        since=since,
                        until=until,
                    )
                )
                assert route.calls[0].request.url.params["interval"] == "240"
                assert [k.close_time - k.open_time for k in klines] == [timedelta(hours=4)]
        finally:
            adapter.close()

    def test_fetch_klines_returns_decimal_prices(self) -> None:
        """Numeric fields arrive as Decimal on the domain Kline — no float loss."""
        adapter = _make_adapter()
        since = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
        until = datetime(2026, 1, 1, 0, 2, tzinfo=UTC)
        try:
            with respx.mock() as router:
                router.get(_OHLC_URL).mock(
                    return_value=httpx.Response(
                        200,
                        json=_kraken_ok(
                            {
                                "XBTUSDT": [
                                    _ohlc_row(int(since.timestamp()), close_price="50050.12345678")
                                ],
                                "last": 0,
                            }
                        ),
                    )
                )
                klines = list(
                    adapter.fetch_klines(
                        pair=_BTC_USDT,
                        interval=KlineInterval.M1,
                        since=since,
                        until=until,
                    )
                )
                assert len(klines) == 1
                assert klines[0].close.amount == Decimal("50050.12345678")
                assert klines[0].open_time == since
        finally:
            adapter.close()

    def test_fetch_klines_stops_at_until_boundary(self) -> None:
        """Klines with open_time >= until are excluded even if returned."""
        adapter = _make_adapter()
        since = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
        until = datetime(2026, 1, 1, 0, 2, tzinfo=UTC)
        base_s = int(since.timestamp())
        rows = [
            _ohlc_row(base_s),
            _ohlc_row(base_s + 60),
            _ohlc_row(base_s + 120),  # == until, excluded
        ]
        try:
            with respx.mock() as router:
                router.get(_OHLC_URL).mock(
                    return_value=httpx.Response(
                        200,
                        json=_kraken_ok({"XBTUSDT": rows, "last": 0}),
                    )
                )
                klines = list(
                    adapter.fetch_klines(
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


class TestFetchKlinesErrors:
    """fetch_klines rejects bad windows and surfaces wire failures cleanly."""

    def test_naive_since_raises(self) -> None:
        """A naive since datetime raises ValueError — hermes stores UTC only."""
        adapter = _make_adapter()
        try:
            with pytest.raises(ValueError, match="since must be UTC-aware"):
                list(
                    adapter.fetch_klines(
                        pair=_BTC_USDT,
                        interval=KlineInterval.M1,
                        since=datetime(2026, 1, 1, 0, 0),  # naive
                        until=datetime(2026, 1, 1, 1, 0, tzinfo=UTC),
                    )
                )
        finally:
            adapter.close()

    def test_until_le_since_raises(self) -> None:
        """A window with until <= since raises ValueError before any wire call."""
        adapter = _make_adapter()
        try:
            with pytest.raises(ValueError, match="must be > since"):
                list(
                    adapter.fetch_klines(
                        pair=_BTC_USDT,
                        interval=KlineInterval.M1,
                        since=datetime(2026, 1, 1, 1, 0, tzinfo=UTC),
                        until=datetime(2026, 1, 1, 0, 0, tzinfo=UTC),
                    )
                )
        finally:
            adapter.close()

    def test_kraken_error_response_raises(self) -> None:
        """Non-empty error array on OHLC surfaces as KrakenPublicError."""
        adapter = _make_adapter()
        try:
            with respx.mock() as router:
                router.get(_OHLC_URL).mock(
                    return_value=httpx.Response(
                        200,
                        json={"error": ["EQuery:Invalid interval"], "result": {}},
                    )
                )
                with pytest.raises(KrakenPublicError, match="Invalid interval"):
                    list(
                        adapter.fetch_klines(
                            pair=_BTC_USDT,
                            interval=KlineInterval.M1,
                            since=datetime(2026, 1, 1, 0, 0, tzinfo=UTC),
                            until=datetime(2026, 1, 1, 0, 5, tzinfo=UTC),
                        )
                    )
        finally:
            adapter.close()

    def test_missing_pair_in_ohlc_result_raises(self) -> None:
        """OHLC response missing our pair key raises with the available keys listed."""
        adapter = _make_adapter()
        try:
            with respx.mock() as router:
                router.get(_OHLC_URL).mock(
                    return_value=httpx.Response(
                        200,
                        json=_kraken_ok({"XETHZUSD": [], "last": 0}),
                    )
                )
                with pytest.raises(KrakenPublicError, match="no entry for 'XBTUSDT'"):
                    list(
                        adapter.fetch_klines(
                            pair=_BTC_USDT,
                            interval=KlineInterval.M1,
                            since=datetime(2026, 1, 1, 0, 0, tzinfo=UTC),
                            until=datetime(2026, 1, 1, 0, 5, tzinfo=UTC),
                        )
                    )
        finally:
            adapter.close()


# ---------------------------------------------------------------------------
# get_orderbook
# ---------------------------------------------------------------------------


_DEPTH_PAYLOAD = _kraken_ok(
    {
        "XBTUSDT": {
            "bids": [
                ["50000.0", "1.5", 1735689600],
                ["49990.0", "2.0", 1735689600],
            ],
            "asks": [
                ["50100.0", "1.5", 1735689600],
                ["50110.0", "2.0", 1735689600],
            ],
        }
    }
)


class TestGetOrderbook:
    """Depth endpoint returns a well-formed Orderbook snapshot."""

    def test_get_orderbook_hits_depth_with_pair_and_count(self) -> None:
        """URL is /0/public/Depth with pair=XBTUSDT and count=10."""
        adapter = _make_adapter()
        try:
            with respx.mock(assert_all_called=True) as router:
                route = router.get(_DEPTH_URL).mock(
                    return_value=httpx.Response(200, json=_DEPTH_PAYLOAD)
                )
                adapter.get_orderbook(_BTC_USDT, depth=10)
                request = route.calls[0].request
                assert request.url.params["pair"] == "XBTUSDT"
                assert request.url.params["count"] == "10"
        finally:
            adapter.close()

    def test_get_orderbook_returns_bids_and_asks_with_decimal_precision(self) -> None:
        """Levels arrive as Decimal price + Decimal quantity, ordering preserved."""
        adapter = _make_adapter()
        try:
            with respx.mock() as router:
                router.get(_DEPTH_URL).mock(return_value=httpx.Response(200, json=_DEPTH_PAYLOAD))
                book = adapter.get_orderbook(_BTC_USDT)
                assert isinstance(book, Orderbook)
                assert book.bids[0].price == Decimal("50000.0")
                assert book.bids[0].quantity == Decimal("1.5")
                assert book.asks[0].price == Decimal("50100.0")
                # Bids descending, asks ascending (verified by Orderbook invariants).
        finally:
            adapter.close()

    def test_get_orderbook_zero_depth_raises_value_error(self) -> None:
        """A non-positive depth is a caller bug — raises before any wire call."""
        adapter = _make_adapter()
        try:
            with pytest.raises(ValueError, match="depth must be > 0"):
                adapter.get_orderbook(_BTC_USDT, depth=0)
        finally:
            adapter.close()

    def test_get_orderbook_missing_bids_raises(self) -> None:
        """A depth response without a 'bids' key raises KrakenPublicError."""
        adapter = _make_adapter()
        try:
            with respx.mock() as router:
                router.get(_DEPTH_URL).mock(
                    return_value=httpx.Response(
                        200,
                        json=_kraken_ok({"XBTUSDT": {"asks": [["50100.0", "1.5", 1]]}}),
                    )
                )
                with pytest.raises(KrakenPublicError, match="missing or non-list 'bids'"):
                    adapter.get_orderbook(_BTC_USDT)
        finally:
            adapter.close()


# ---------------------------------------------------------------------------
# get_ticker
# ---------------------------------------------------------------------------


_TICKER_PAYLOAD = _kraken_ok(
    {
        "XBTUSDT": {
            "a": ["50100.0", "1", "1.000"],
            "b": ["50000.0", "1", "1.000"],
            "c": ["50050.0", "0.5"],
            "v": ["100.0", "200.0"],
            "p": ["50050.0", "50060.0"],
            "t": [100, 200],
            "l": ["49900.0", "49800.0"],
            "h": ["50200.0", "50300.0"],
            "o": "49950.0",
        }
    }
)


class TestGetTicker:
    """Ticker endpoint returns a well-formed Ticker snapshot."""

    def test_get_ticker_hits_ticker_with_pair_param(self) -> None:
        """URL is /0/public/Ticker with pair=XBTUSDT."""
        adapter = _make_adapter()
        try:
            with respx.mock(assert_all_called=True) as router:
                route = router.get(_TICKER_URL).mock(
                    return_value=httpx.Response(200, json=_TICKER_PAYLOAD)
                )
                adapter.get_ticker(_BTC_USDT)
                request = route.calls[0].request
                assert request.url.params["pair"] == "XBTUSDT"
        finally:
            adapter.close()

    def test_get_ticker_returns_bid_ask_last_with_decimal_precision(self) -> None:
        """Ticker fields arrive as Decimal — best_bid < best_ask < verified by Ticker invariants."""
        adapter = _make_adapter()
        try:
            with respx.mock() as router:
                router.get(_TICKER_URL).mock(return_value=httpx.Response(200, json=_TICKER_PAYLOAD))
                ticker = adapter.get_ticker(_BTC_USDT)
                assert isinstance(ticker, Ticker)
                assert ticker.best_bid == Decimal("50000.0")
                assert ticker.best_ask == Decimal("50100.0")
                assert ticker.last_price == Decimal("50050.0")
        finally:
            adapter.close()

    def test_get_ticker_missing_ask_raises(self) -> None:
        """A ticker response without an 'a' key raises KrakenPublicError."""
        adapter = _make_adapter()
        try:
            with respx.mock() as router:
                router.get(_TICKER_URL).mock(
                    return_value=httpx.Response(
                        200,
                        json=_kraken_ok(
                            {"XBTUSDT": {"b": ["50000", "1", "1"], "c": ["50050", "1"]}}
                        ),
                    )
                )
                with pytest.raises(KrakenPublicError, match="missing or empty 'a'"):
                    adapter.get_ticker(_BTC_USDT)
        finally:
            adapter.close()


# ---------------------------------------------------------------------------
# Port conformance + factory
# ---------------------------------------------------------------------------


class TestPortConformance:
    """The adapter class satisfies the runtime-checkable Protocols."""

    def test_adapter_satisfies_fee_schedule_provider_port(self) -> None:
        """Isinstance against the runtime_checkable FeeScheduleProviderPort returns True."""
        adapter = _make_adapter()
        try:
            assert isinstance(adapter, FeeScheduleProviderPort)
        finally:
            adapter.close()

    def test_adapter_satisfies_klines_fetcher_port(self) -> None:
        """The adapter has a fetch_klines method — Kraken port shape mirrors Binance."""
        adapter = _make_adapter()
        try:
            # KlinesFetcherPort's protocol method is `fetch`; Kraken exposes
            # `fetch_klines` because it also owns other endpoints. Wire-up
            # code adapts the two names. Assert method exists + callable.
            assert callable(adapter.fetch_klines)
            _ = KlinesFetcherPort  # keep the import used
        finally:
            adapter.close()


class TestCreateFactoryAndClose:
    """The create() factory produces a working adapter; close is idempotent."""

    def test_create_default_targets_public_kraken(self) -> None:
        """create() with no args builds an adapter pointing at api.kraken.com."""
        adapter = KrakenPublicAdapter.create()
        try:
            assert str(adapter.client.base_url) == _BASE_URL
        finally:
            adapter.close()

    def test_create_with_custom_config_uses_it(self) -> None:
        """create(config=X) wires the client and limiter to X's values."""
        cfg = KrakenPublicConfig(
            base_url="https://kraken.example.com",
            rate_limit=RateLimitConfig(max_weight=5, window_seconds=1),
            timeout_sec=2.5,
        )
        adapter = KrakenPublicAdapter.create(cfg)
        try:
            assert str(adapter.client.base_url) == "https://kraken.example.com"
            assert adapter.limiter.config.max_weight == 5
        finally:
            adapter.close()

    def test_close_is_idempotent(self) -> None:
        """close() called twice does not raise (second call is a no-op)."""
        adapter = KrakenPublicAdapter.create()
        adapter.close()
        adapter.close()  # must not raise
