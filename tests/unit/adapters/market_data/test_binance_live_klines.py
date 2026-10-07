"""Tests for :class:`BinanceLiveKlinesAdapter` (live closed-bar source, REST polling).

The exchange is :class:`FakeBinancePublic` driven by a simulated clock, so a
two-day stream runs in milliseconds and every fault (outage, skipped candle,
skewed or lagging exchange clock, 429, 5xx) is injected exactly.

Invariants (live-bar-stream change D-1 to D-7):

- the feed never emits an open candle and emits each bar once;
- delivery is monotonic in close time, 1d before 4h at the same instant;
- an outage leaves no gap: the post-outage sequence equals the no-outage one;
- the exchange clock can only delay a bar, never release one early;
- transport errors and 5xx retry 3 times (100, 200, 400 ms), 429 and 418 do not
  retry and wait ``Retry-After``, other 4xx and schema errors are permanent;
- only GET requests to two market-data paths, with no credentials.

Seams: the integration test at the HTTP boundary lives in
``tests/integration/adapters/market_data/test_binance_live_klines_replay.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import anyio
import httpx
import pytest
import respx
import structlog
from hypothesis import given, settings
from hypothesis import strategies as st

import hermes.adapters.market_data.binance_live_klines as klines_module
from hermes.adapters.clock import SimulatedClock
from hermes.adapters.exchanges.rate_limiter import RateLimitConfig, RateLimiter
from hermes.adapters.market_data.binance_live_klines import BinanceLiveKlinesAdapter
from hermes.domain.errors import ExchangeError, NetworkError
from hermes.domain.market_data.bar_schedule import interval_duration
from hermes.domain.market_data.kline import KlineInterval
from hermes.infrastructure.config.models import MarketDataConfig
from hermes.ports.bar_feed import ClosedBarFeedPort, SourceState
from hermes.ports.market_data import MarketDataPort
from tests.unit.adapters.market_data.fake_binance_public import (
    BTC_USDT,
    ETH_USDT,
    START,
    FakeBinancePublic,
    SimulatedSleep,
    build_adapter,
    keys,
    row_for,
    run_stream,
)

_H4 = KlineInterval.H4
_D1 = KlineInterval.D1
_SETTLE = timedelta(seconds=5)
_TWO_DAYS = timedelta(days=2)
_MIDNIGHT = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)


def _fake(start: datetime = START) -> FakeBinancePublic:
    """Return a fake exchange on a fresh simulated clock."""
    return FakeBinancePublic(SimulatedClock(start=start))


def _reference_keys(until: timedelta = _TWO_DAYS) -> list[tuple[str, str, datetime]]:
    """Return the delivery sequence of a fault-free run, for comparisons."""
    return keys(run_stream(_fake(), until=START + until).bars)


class TestDelivery:
    """Closed bars are delivered once, in order, never before they are closed."""

    def test_feed_never_emits_the_open_candle(self) -> None:
        """Every bar reaches the consumer only after open + interval + settle (D-3)."""
        run = run_stream(_fake(), until=START + _TWO_DAYS)
        premature = [
            (bar.pair, bar.open_time)
            for bar, received in zip(run.bars, run.received_at, strict=True)
            if received < bar.open_time + interval_duration(bar.interval) + _SETTLE
        ]
        assert premature == []

    def test_each_bar_is_emitted_once_across_overlapping_polls(self) -> None:
        """An exchange that repeats old candles on every poll still yields each bar once."""
        overlapping = _fake()
        overlapping.ignore_start_time = True
        run = run_stream(overlapping, until=START + _TWO_DAYS)
        assert keys(run.bars) == _reference_keys()

    @pytest.mark.timeout(120)  # each example simulates two days of polling
    @settings(max_examples=12, deadline=None)
    @given(start_offset_minutes=st.integers(min_value=0, max_value=24 * 60))
    def test_emitted_close_times_are_monotonic(self, start_offset_minutes: int) -> None:
        """Close times never go backwards, wherever in the day the stream starts."""
        start = START + timedelta(minutes=start_offset_minutes)
        run = run_stream(_fake(start), until=start + _TWO_DAYS)
        close_times = [bar.close_time for bar in run.bars]
        assert close_times == sorted(close_times)

    def test_midnight_delivers_1d_before_4h_for_every_pair(self) -> None:
        """At 00:00 UTC each pair's 1d bar precedes the 4h bar closing at the same instant."""
        run = run_stream(_fake(), until=_MIDNIGHT + timedelta(minutes=1))
        assert keys(run.bars) == [
            ("BTC/USDT", "1d", datetime(2026, 9, 30, 0, 0, tzinfo=UTC)),
            ("ETH/USDT", "1d", datetime(2026, 9, 30, 0, 0, tzinfo=UTC)),
            ("SOL/USDT", "1d", datetime(2026, 9, 30, 0, 0, tzinfo=UTC)),
            ("BTC/USDT", "4h", datetime(2026, 9, 30, 20, 0, tzinfo=UTC)),
            ("ETH/USDT", "4h", datetime(2026, 9, 30, 20, 0, tzinfo=UTC)),
            ("SOL/USDT", "4h", datetime(2026, 9, 30, 20, 0, tzinfo=UTC)),
        ]

    def test_bar_fields_come_straight_from_the_wire_row(self) -> None:
        """OHLCV and the inclusive close_time of the first bar match the klines row."""
        run = run_stream(_fake(), until=_MIDNIGHT + timedelta(minutes=1), pairs=[BTC_USDT])
        first = run.bars[0]
        row = row_for("BTCUSDT", _D1, datetime(2026, 9, 30, 0, 0, tzinfo=UTC))
        assert (first.open, first.high, first.low, first.close, first.volume) == tuple(
            Decimal(str(row[index])) for index in (1, 2, 3, 4, 5)
        )
        assert first.close_time == datetime(2026, 9, 30, 23, 59, 59, 999000, tzinfo=UTC)

    def test_stream_bars_yields_only_the_requested_stream(self) -> None:
        """The MarketDataPort bar channel filters the shared poller to one stream."""
        run = run_stream(_fake(), until=START + _TWO_DAYS, single=(BTC_USDT, _H4))
        assert {(k[0], k[1]) for k in keys(run.bars)} == {("BTC/USDT", "4h")}
        assert len(run.bars) == 12


class TestOutageBackfill:
    """A source outage is healed by the cursor: no gap, no duplicate, same order."""

    @pytest.mark.timeout(180)  # each example polls through an outage on a 5 s cadence
    @settings(max_examples=10, deadline=None)
    @given(
        outage_start_minutes=st.integers(min_value=0, max_value=30 * 60),
        outage_minutes=st.integers(min_value=1, max_value=5 * 60),
    )
    def test_backfill_after_outage_leaves_no_gap(
        self, outage_start_minutes: int, outage_minutes: int
    ) -> None:
        """Wherever an outage falls, the feed ends with exactly the fault-free sequence."""
        faulty = _fake()
        outage_start = START + timedelta(minutes=outage_start_minutes)
        faulty.outages = [(outage_start, outage_start + timedelta(minutes=outage_minutes))]
        three_days = timedelta(days=3)
        run = run_stream(faulty, until=START + three_days)
        assert keys(run.bars) == _reference_keys(three_days)

    def test_source_is_healthy_again_after_the_outage(self) -> None:
        """Once backfill has delivered the missed bars the source reports HEALTHY."""
        faulty = _fake()
        faulty.outages = [(_MIDNIGHT + timedelta(hours=3), _MIDNIGHT + timedelta(hours=6))]
        run = run_stream(faulty, until=START + _TWO_DAYS)
        health = run.adapter.health()
        assert (health.state, health.consecutive_failures) == (SourceState.HEALTHY, 0)

    def test_outage_degrades_then_marks_stale_after_fifteen_minutes(self) -> None:
        """During an outage the state goes DEGRADED, then STALE once 900 s overdue."""
        faulty = _fake()
        faulty.outages = [(_MIDNIGHT, _MIDNIGHT + timedelta(hours=1))]
        states: list[str] = []
        run_stream(faulty, until=START + _TWO_DAYS, states=states)
        assert {"degraded", "stale"} <= set(states)


class TestExchangeClock:
    """The exchange clock can delay a bar but never release one early (D-3)."""

    def test_exchange_clock_ahead_never_advances_closure(self) -> None:
        """An exchange 2 minutes ahead of the local clock still delivers on the local schedule."""
        skewed = _fake()
        skewed.server_offset = timedelta(minutes=2)
        run = run_stream(skewed, until=START + _TWO_DAYS)
        early = [
            bar.open_time
            for bar, received in zip(run.bars, run.received_at, strict=True)
            if received < bar.open_time + interval_duration(bar.interval) + _SETTLE
        ]
        assert early == []

    def test_exchange_clock_behind_delays_delivery_until_both_clocks_agree(self) -> None:
        """An exchange 2 minutes behind delivers the midnight bars 2 minutes late, at 00:02:05."""
        skewed = _fake()
        skewed.server_offset = -timedelta(minutes=2)
        run = run_stream(skewed, until=_MIDNIGHT + timedelta(minutes=5))
        assert run.received_at[0] == _MIDNIGHT + timedelta(minutes=2, seconds=5)

    def test_clock_skew_is_measured_at_the_midpoint_of_the_time_request(self) -> None:
        """A slow 20 s /time round trip is not clock skew: the local reading is its midpoint."""
        slow = _fake()
        slow.time_latency = timedelta(seconds=20)
        run = run_stream(slow, until=START + _TWO_DAYS)
        health = run.adapter.health()
        assert (health.clock_skew_ms, health.state) == (0, SourceState.HEALTHY)

    def test_skew_beyond_settle_degrades_the_source_and_reports_the_skew(self) -> None:
        """A 2 minute skew exceeds the 5 s settle: DEGRADED, skew reported in milliseconds."""
        skewed = _fake()
        skewed.server_offset = timedelta(minutes=2)
        run = run_stream(skewed, until=START + _TWO_DAYS)
        health = run.adapter.health()
        assert (health.state, health.clock_skew_ms) == (SourceState.DEGRADED, -120_000)


class TestOverdueBars:
    """A bar the exchange has not published yet is retried, then flagged stale (D-6, D-7)."""

    def test_bar_published_20_minutes_late_is_delivered_when_it_appears(self) -> None:
        """Polling continues through the lag and delivers at boundary + 20 minutes."""
        lagging = _fake()
        lagging.lag = timedelta(minutes=20)
        run = run_stream(lagging, until=_MIDNIGHT + timedelta(minutes=30))
        assert run.received_at[0] == _MIDNIGHT + timedelta(minutes=20)

    def test_retry_cadence_slows_from_5_to_30_seconds_once_stale(self) -> None:
        """Re-polls run every 5 s until 900 s overdue, then every 30 s."""
        lagging = _fake()
        lagging.lag = timedelta(minutes=20)
        run = run_stream(lagging, until=_MIDNIGHT + timedelta(minutes=30))
        assert {5.0, 30.0} <= set(run.sleeper.calls)

    def test_source_goes_stale_while_a_bar_is_overdue_and_recovers_after(self) -> None:
        """The overdue window shows STALE; the delivery of the bar returns the source to HEALTHY."""
        lagging = _fake()
        lagging.lag = timedelta(minutes=20)
        states: list[str] = []
        run = run_stream(lagging, until=START + _TWO_DAYS, states=states)
        assert ("stale" in states, run.adapter.health().state) == (True, SourceState.HEALTHY)


class TestSkippedCandles:
    """A candle the exchange never publishes is a gap: logged, source DEGRADED, rest delivered."""

    def test_skipped_4h_candle_is_logged_once_per_pair_and_the_rest_delivered(self) -> None:
        """R-5: the missing 04:00 bar logs market_data.gap_detected and later bars still flow."""
        gappy = _fake()
        gappy.missing = {("4h", _MIDNIGHT + timedelta(hours=4))}
        with structlog.testing.capture_logs() as logs:
            run = run_stream(gappy, until=START + _TWO_DAYS)
        gap_events = [e for e in logs if e["event"] == "market_data.gap_detected"]
        assert [e["pair"] for e in gap_events] == ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
        expected = [k for k in _reference_keys() if k[1:] != ("4h", _MIDNIGHT + timedelta(hours=4))]
        assert keys(run.bars) == expected

    def test_gap_marks_the_source_degraded_until_the_next_clean_cycle(self) -> None:
        """The cycle that finds a gap is not clean, so the source is DEGRADED at that moment."""
        gappy = _fake()
        gappy.missing = {("4h", _MIDNIGHT + timedelta(hours=4))}
        states: list[str] = []
        run_stream(gappy, until=START + _TWO_DAYS, states=states)
        assert "degraded" in states


class TestErrorPolicy:
    """D-6: retry transient errors, wait out 429/418, fail loud on permanent ones."""

    def test_transport_error_is_retried_three_times_with_100_200_400_ms_backoff(self) -> None:
        """Three failed attempts then success: waits are 0.1, 0.2, 0.4 s and the bar arrives."""
        flaky = _fake()
        flaky.klines_script = [httpx.ConnectError("boom")] * 3
        run = run_stream(flaky, until=_MIDNIGHT + timedelta(minutes=10))
        backoffs = [s for s in run.sleeper.calls if s in {0.1, 0.2, 0.4}]
        assert (backoffs, run.received_at[0] - _MIDNIGHT) == (
            [0.1, 0.2, 0.4],
            timedelta(seconds=5.7),
        )

    def test_retries_exhausted_degrades_and_repolls_in_5_seconds_without_raising(self) -> None:
        """Four 503s in a row do not raise: the cycle is abandoned and retried 5 s later."""
        down = _fake()
        down.klines_script = [httpx.Response(503, text="busy")] * 4
        run = run_stream(down, until=_MIDNIGHT + timedelta(minutes=10))
        assert (run.error, run.received_at[0] - _MIDNIGHT) == (None, timedelta(seconds=10.7))

    @pytest.mark.parametrize("status", [429, 418])
    def test_rate_limit_responses_are_not_retried_and_wait_retry_after(self, status: int) -> None:
        """429 and 418 trigger exactly one request, then a wait of the Retry-After seconds."""
        limited = _fake()
        limited.klines_script = [httpx.Response(status, headers={"Retry-After": "7"})]
        run = run_stream(limited, until=_MIDNIGHT + timedelta(minutes=10))
        assert (7.0 in run.sleeper.calls, run.received_at[0] - _MIDNIGHT) == (
            True,
            timedelta(seconds=12),
        )

    def test_rate_limit_response_without_retry_after_waits_the_retry_interval(self) -> None:
        """A 429 with no Retry-After header falls back to the 5 s retry interval."""
        limited = _fake()
        limited.klines_script = [httpx.Response(429)]
        run = run_stream(limited, until=_MIDNIGHT + timedelta(minutes=10))
        assert run.received_at[0] - _MIDNIGHT == timedelta(seconds=10)

    @pytest.mark.parametrize("status", [400, 403, 404, 451])
    def test_other_client_errors_are_permanent_and_fail_the_source(self, status: int) -> None:
        """A 4xx other than 429/418 (451 included) raises ExchangeError and marks FAILED."""
        blocked = _fake()
        blocked.klines_script = [httpx.Response(status, text="blocked")]
        run = run_stream(blocked, until=_MIDNIGHT + timedelta(minutes=10))
        assert (isinstance(run.error, ExchangeError), run.adapter.health().state) == (
            True,
            SourceState.FAILED,
        )

    @pytest.mark.parametrize(
        "bad_reply",
        [
            httpx.Response(200, text="<html>not json</html>"),
            httpx.Response(200, json={"code": -1121}),
            httpx.Response(200, json=[[1, "1", "1", "1", "1", "1"]]),
            httpx.Response(200, json=[["x", "1", "1", "1", "1", "1", 2]]),
        ],
        ids=["non-json", "non-list", "short-row", "non-int-time"],
    )
    def test_schema_errors_are_permanent_and_fail_the_source(
        self, bad_reply: httpx.Response
    ) -> None:
        """A malformed klines body is a wrong URL or a changed API: FAILED, loud."""
        broken = _fake()
        broken.klines_script = [bad_reply]
        run = run_stream(broken, until=_MIDNIGHT + timedelta(minutes=10))
        assert (isinstance(run.error, ExchangeError), run.adapter.health().state) == (
            True,
            SourceState.FAILED,
        )

    def test_close_time_that_is_not_open_plus_interval_minus_1ms_is_permanent(self) -> None:
        """A row whose close_time breaks Binance's inclusive convention is rejected."""
        wrong = _fake()
        wrong.klines_script = [
            httpx.Response(
                200, json=[[1_790_899_200_000, "1", "1", "1", "1", "1", 1_790_899_200_001]]
            )
        ]
        run = run_stream(wrong, until=_MIDNIGHT + timedelta(minutes=10))
        assert "close_time" in str(run.error)

    def test_time_endpoint_without_server_time_is_permanent(self) -> None:
        """A /time body without an integer serverTime fails the source."""
        broken = _fake()
        broken.time_script = [httpx.Response(200, json={"serverTime": "soon"})]
        run = run_stream(broken, until=_MIDNIGHT + timedelta(minutes=10))
        assert (isinstance(run.error, ExchangeError), run.adapter.health().state) == (
            True,
            SourceState.FAILED,
        )

    def test_503_on_the_time_endpoint_is_transient(self) -> None:
        """The time call retries like any other read: four 503s degrade, they do not fail."""
        down = _fake()
        down.time_script = [httpx.Response(503)] * 4
        run = run_stream(down, until=_MIDNIGHT + timedelta(minutes=10))
        assert (run.error, run.received_at[0] - _MIDNIGHT) == (None, timedelta(seconds=10.7))


class TestRequestSafety:
    """D-2: only GET to two market-data paths, no credentials, no trading host."""

    def test_every_request_is_a_get_to_one_of_two_paths_with_no_credentials(self) -> None:
        """No non-GET verb, no other path, no X-MBX-APIKEY header, no signature parameter."""
        fake = _fake()
        run_stream(fake, until=START + _TWO_DAYS)
        observed = {
            (
                r.method,
                r.url.host,
                r.url.path,
                "x-mbx-apikey" in r.headers,
                "signature" in r.url.params,
            )
            for r in fake.requests
        }
        assert observed == {
            ("GET", "data-api.binance.vision", "/api/v3/time", False, False),
            ("GET", "data-api.binance.vision", "/api/v3/klines", False, False),
        }

    def test_client_carrying_an_api_key_header_is_rejected(self) -> None:
        """The adapter is public-only: a client with X-MBX-APIKEY cannot be injected."""
        client = httpx.AsyncClient(headers={"X-MBX-APIKEY": "not-a-real-key"})  # gitleaks:allow
        with pytest.raises(ValueError, match="public-only"):
            BinanceLiveKlinesAdapter(
                config=MarketDataConfig(),
                clock=SimulatedClock(start=START),
                client=client,
                limiter=RateLimiter(RateLimitConfig(max_weight=600, window_seconds=60)),
            )

    def test_path_outside_the_allow_list_is_refused_before_any_request(self) -> None:
        """A trading path such as /api/v3/order never leaves the process."""
        clock = SimulatedClock(start=START)

        async def _drive() -> None:
            with respx.mock(assert_all_called=False) as router:
                adapter = build_adapter(clock, SimulatedSleep(clock))
                with pytest.raises(ExchangeError, match="refusing request"):
                    await adapter._get("/api/v3/order", None)
                assert router.calls.call_count == 0

        anyio.run(_drive)

    def test_source_names_no_trading_or_stream_host_and_reads_no_wall_clock(self) -> None:
        """The module text has no api.binance.com, stream.binance.com, datetime.now or time.time."""
        text = Path(klines_module.__file__).read_text(encoding="utf-8")
        found = [
            needle
            for needle in ("api.binance.com", "stream.binance.com", "datetime.now", "time.time(")
            if needle in text
        ]
        assert found == []

    def test_client_base_url_is_never_consulted(self) -> None:
        """Requests go to the config's allow-listed host even if the client has another base_url."""
        fake = _fake()
        clock = fake.clock

        async def _drive() -> None:
            with respx.mock(assert_all_called=False) as router:
                fake.install(router)
                client = httpx.AsyncClient(base_url="https://elsewhere.example")
                adapter = BinanceLiveKlinesAdapter(
                    config=MarketDataConfig(),
                    clock=clock,
                    client=client,
                    limiter=RateLimiter(RateLimitConfig(max_weight=600, window_seconds=60)),
                    sleep=SimulatedSleep(clock),
                )
                await adapter.warm_up(BTC_USDT, _H4, 5)

        anyio.run(_drive)
        assert {r.url.host for r in fake.requests} == {"data-api.binance.vision"}


class TestWarmUp:
    """Warm-up returns closed history apart from the live stream (D-4, risk R-1)."""

    @staticmethod
    def _warm(
        fake: FakeBinancePublic, interval: KlineInterval, count: int
    ) -> tuple[list[datetime], BinanceLiveKlinesAdapter]:
        async def _drive() -> tuple[list[datetime], BinanceLiveKlinesAdapter]:
            with respx.mock(assert_all_called=False) as router:
                fake.install(router)
                adapter = build_adapter(fake.clock, SimulatedSleep(fake.clock))
                bars = await adapter.warm_up(BTC_USDT, interval, count)
                return [b.open_time for b in bars], adapter

        return anyio.run(_drive)

    def test_warm_up_returns_300_closed_4h_bars_and_drops_the_open_candle(self) -> None:
        """Asked for 300 at 23:00, the 20:00 candle is still forming, so the newest is 16:00."""
        fake = _fake()
        opens, _ = self._warm(fake, _H4, 300)
        assert (len(opens), opens[-1], opens == sorted(opens)) == (
            300,
            datetime(2026, 9, 30, 16, 0, tzinfo=UTC),
            True,
        )

    def test_warm_up_requests_one_extra_row_to_read_past_the_open_candle(self) -> None:
        """Warm-up of 300 sends limit=301 (D diagram: 301 rows, the last is the open candle)."""
        fake = _fake()
        self._warm(fake, _H4, 300)
        assert fake.klines_requests()[0].url.params["limit"] == "301"

    def test_warm_up_returns_300_closed_daily_bars_ending_the_previous_day(self) -> None:
        """At 23:00 on Sep 30 the newest closed daily bar is Sep 29."""
        opens, _ = self._warm(_fake(), _D1, 300)
        assert (len(opens), opens[-1]) == (300, datetime(2026, 9, 29, 0, 0, tzinfo=UTC))

    @pytest.mark.parametrize(
        ("seconds_after_midnight", "newest_open"),
        [
            (4, datetime(2026, 9, 30, 16, 0, tzinfo=UTC)),
            (5, datetime(2026, 9, 30, 20, 0, tzinfo=UTC)),
        ],
    )
    def test_warm_up_waits_for_the_settle_delay_after_a_boundary(
        self, seconds_after_midnight: int, newest_open: datetime
    ) -> None:
        """The 20:00 bar closes at 00:00 but counts as closed only from 00:00:05."""
        fake = _fake(_MIDNIGHT + timedelta(seconds=seconds_after_midnight))
        opens, _ = self._warm(fake, _H4, 10)
        assert opens[-1] == newest_open

    def test_warm_up_with_an_exchange_clock_behind_excludes_the_unsettled_bar(self) -> None:
        """At 00:05 local, an exchange 10 minutes behind has not closed the 20:00 bar yet."""
        fake = _fake(_MIDNIGHT + timedelta(minutes=5))
        fake.server_offset = -timedelta(minutes=10)
        opens, _ = self._warm(fake, _H4, 10)
        assert opens[-1] == datetime(2026, 9, 30, 16, 0, tzinfo=UTC)

    @pytest.mark.parametrize("count", [0, -1, 1000])
    def test_warm_up_rejects_a_count_outside_1_to_999(self, count: int) -> None:
        """The klines page cap is 1000 rows and the open candle needs one: count is 1 to 999."""
        with pytest.raises(ValueError, match="count must be between 1 and 999"):
            self._warm(_fake(), _H4, count)

    def test_warm_up_moves_the_source_from_starting_to_healthy(self) -> None:
        """Health reads STARTING before any call and HEALTHY after a successful warm-up."""
        fake = _fake()
        _, adapter = self._warm(fake, _H4, 5)
        fresh = build_adapter(fake.clock, SimulatedSleep(fake.clock))
        assert (fresh.health().state, adapter.health().state) == (
            SourceState.STARTING,
            SourceState.HEALTHY,
        )

    def test_streaming_after_warm_up_continues_with_the_next_bar_and_no_repeat(self) -> None:
        """No gap, no repeat: the first 4h bar streamed is the one after the last warmed up."""
        run = run_stream(
            _fake(),
            until=_MIDNIGHT + timedelta(minutes=1),
            pairs=[BTC_USDT],
            intervals=[_H4],
            warm_up={(BTC_USDT, _H4): 50},
        )
        assert (run.warm_bars[(BTC_USDT, _H4)][-1].open_time, run.bars[0].open_time) == (
            datetime(2026, 9, 30, 16, 0, tzinfo=UTC),
            datetime(2026, 9, 30, 20, 0, tzinfo=UTC),
        )

    def test_transient_failure_raises_and_leaves_the_source_degraded(self) -> None:
        """Four 503s exhaust the retries: the warm-up raises ExchangeError, state DEGRADED."""
        fake = _fake()
        fake.klines_script = [httpx.Response(503, text="busy")] * 4

        async def _drive() -> BinanceLiveKlinesAdapter:
            with respx.mock(assert_all_called=False) as router:
                fake.install(router)
                adapter = build_adapter(fake.clock, SimulatedSleep(fake.clock))
                with pytest.raises(ExchangeError, match="HTTP 503"):
                    await adapter.warm_up(BTC_USDT, _H4, 5)
                return adapter

        assert anyio.run(_drive).health().state == SourceState.DEGRADED

    def test_transport_failure_raises_network_error(self) -> None:
        """Four connection errors exhaust the retries and surface as NetworkError."""
        fake = _fake()
        fake.klines_script = [httpx.ConnectError("down")] * 4

        async def _drive() -> None:
            with respx.mock(assert_all_called=False) as router:
                fake.install(router)
                adapter = build_adapter(fake.clock, SimulatedSleep(fake.clock))
                with pytest.raises(NetworkError, match="transport failure"):
                    await adapter.warm_up(BTC_USDT, _H4, 5)

        anyio.run(_drive)

    def test_blocked_region_fails_loud_at_warm_up(self) -> None:
        """R-6: HTTP 451 at warm-up is permanent: ExchangeError and state FAILED."""
        fake = _fake()
        fake.klines_script = [httpx.Response(451, text="unavailable for legal reasons")]

        async def _drive() -> BinanceLiveKlinesAdapter:
            with respx.mock(assert_all_called=False) as router:
                fake.install(router)
                adapter = build_adapter(fake.clock, SimulatedSleep(fake.clock))
                with pytest.raises(ExchangeError, match="HTTP 451"):
                    await adapter.warm_up(BTC_USDT, _H4, 5)
                return adapter

        assert anyio.run(_drive).health().state == SourceState.FAILED

    def test_rate_limited_warm_up_raises_and_degrades(self) -> None:
        """A 429 at warm-up is not retried: ExchangeError naming the status, state DEGRADED."""
        fake = _fake()
        fake.klines_script = [httpx.Response(429, headers={"Retry-After": "3"})]

        async def _drive() -> BinanceLiveKlinesAdapter:
            with respx.mock(assert_all_called=False) as router:
                fake.install(router)
                adapter = build_adapter(fake.clock, SimulatedSleep(fake.clock))
                with pytest.raises(ExchangeError, match="HTTP 429"):
                    await adapter.warm_up(BTC_USDT, _H4, 5)
                return adapter

        assert anyio.run(_drive).health().state == SourceState.DEGRADED


class TestResume:
    """Where a stream starts: marker, then warm-up, then the latest closed bar."""

    _EVENING = datetime(2026, 10, 1, 21, 0, tzinfo=UTC)

    def _from_marker(self, marker: datetime) -> list[tuple[str, str, datetime]]:
        run = run_stream(
            _fake(self._EVENING),
            until=self._EVENING + timedelta(minutes=30),
            pairs=[BTC_USDT],
            intervals=[_H4],
            resume_after={(BTC_USDT, _H4): marker},
        )
        return keys(run.bars)

    def test_resume_marker_as_open_time_delivers_the_bars_after_it(self) -> None:
        """Marker = open of the 12:00 bar: the 16:00 bar (closed 20:00) is delivered."""
        marker = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
        assert self._from_marker(marker) == [
            ("BTC/USDT", "4h", datetime(2026, 10, 1, 16, 0, tzinfo=UTC))
        ]

    def test_resume_marker_as_close_time_resumes_at_the_same_bar(self) -> None:
        """Marker = close_time of the 12:00 bar (15:59:59.999) resumes at the 16:00 bar too."""
        marker = datetime(2026, 10, 1, 15, 59, 59, 999000, tzinfo=UTC)
        assert self._from_marker(marker) == [
            ("BTC/USDT", "4h", datetime(2026, 10, 1, 16, 0, tzinfo=UTC))
        ]

    def test_resume_marker_at_the_latest_closed_bar_delivers_nothing_old(self) -> None:
        """Marker = open of the 16:00 bar: that bar is not repeated."""
        assert self._from_marker(datetime(2026, 10, 1, 16, 0, tzinfo=UTC)) == []

    def test_stream_without_marker_or_warm_up_starts_after_the_latest_closed_bar(self) -> None:
        """At 21:00 with no marker the first bar is the 20:00 one, closing at 00:00."""
        run = run_stream(
            _fake(self._EVENING),
            until=_MIDNIGHT + timedelta(days=1, minutes=1),
            pairs=[BTC_USDT],
            intervals=[_H4],
        )
        assert (keys(run.bars)[0], run.received_at[0]) == (
            ("BTC/USDT", "4h", datetime(2026, 10, 1, 20, 0, tzinfo=UTC)),
            datetime(2026, 10, 2, 0, 0, 5, tzinfo=UTC),
        )

    def test_naive_resume_marker_is_rejected(self) -> None:
        """A naive marker cannot be placed in time and is refused at the first iteration."""
        naive = datetime(2026, 10, 1, 12, 0)
        with pytest.raises(ValueError, match="must be UTC-aware"):
            self._from_marker(naive)

    def test_stream_with_no_pairs_is_rejected(self) -> None:
        """A stream over nothing is a call-site bug."""
        with pytest.raises(ValueError, match="at least one pair and one interval"):
            run_stream(_fake(), until=START + timedelta(hours=1), pairs=[], intervals=[_H4])


class TestLifecycle:
    """Health reporting, port conformance and shutdown."""

    def test_health_reports_last_close_weight_and_skew_after_a_run(self) -> None:
        """After two days: HEALTHY, newest close_time, weight header 2, zero skew."""
        run = run_stream(_fake(), until=START + _TWO_DAYS)
        health = run.adapter.health()
        assert (
            health.state,
            health.last_closed,
            health.used_weight_1m,
            health.clock_skew_ms,
            health.overdue_seconds,
        ) == (
            SourceState.HEALTHY,
            datetime(2026, 10, 2, 19, 59, 59, 999000, tzinfo=UTC),
            2,
            0,
            0.0,
        )

    @pytest.mark.parametrize("port", [ClosedBarFeedPort, MarketDataPort])
    def test_adapter_satisfies_the_port(self, port: type) -> None:
        """The adapter is a ClosedBarFeedPort and keeps the MarketDataPort shape."""
        adapter = build_adapter(
            SimulatedClock(start=START), SimulatedSleep(SimulatedClock(start=START))
        )
        assert isinstance(adapter, port)

    @pytest.mark.parametrize(
        "call",
        [
            lambda a: a.stream_trades(BTC_USDT),
            lambda a: a.stream_orderbook_diffs(BTC_USDT),
            lambda a: a.get_snapshot(BTC_USDT),
        ],
        ids=["trades", "orderbook-diffs", "snapshot"],
    )
    def test_channels_other_than_bars_raise_not_implemented(self, call: object) -> None:
        """Trades and order books are not served here (diffs stay on BinanceLiveWsAdapter)."""
        clock = SimulatedClock(start=START)

        async def _drive() -> None:
            adapter = build_adapter(clock, SimulatedSleep(clock))
            with pytest.raises(NotImplementedError, match="closed bars only"):
                await call(adapter)  # type: ignore[operator]

        anyio.run(_drive)

    def test_aclose_ends_the_stream_and_marks_the_source_stopped(self) -> None:
        """After aclose the iterator finishes without another poll and health is STOPPED."""
        fake = _fake()

        async def _drive() -> tuple[int, SourceState]:
            with respx.mock(assert_all_called=False) as router:
                fake.install(router)
                adapter = build_adapter(fake.clock, SimulatedSleep(fake.clock))
                seen = 0
                async for _ in adapter.stream_closed_bars([BTC_USDT], [_H4]):
                    seen += 1
                    await adapter.aclose()
                await adapter.aclose()
                return seen, adapter.health().state

        assert anyio.run(_drive) == (1, SourceState.STOPPED)

    def test_stream_started_after_aclose_yields_nothing(self) -> None:
        """A closed source never polls again."""
        fake = _fake()

        async def _drive() -> int:
            with respx.mock(assert_all_called=False) as router:
                fake.install(router)
                adapter = build_adapter(fake.clock, SimulatedSleep(fake.clock))
                await adapter.aclose()
                return len([bar async for bar in adapter.stream_closed_bars([BTC_USDT], [_H4])])

        assert anyio.run(_drive) == 0
        assert fake.requests == []


class TestRateLimitBudget:
    """D-6: a token bucket gates every request and waits through the injected sleep."""

    def test_empty_bucket_waits_through_the_injected_sleep_and_still_delivers(self) -> None:
        """With 3 tokens refilling at 0.05/s a weight-2 request waits 40 s, never blocking."""
        fake = _fake()
        limiter = RateLimiter.for_testing(
            RateLimitConfig(max_weight=3, window_seconds=60),
            clock=lambda: fake.clock.now().timestamp(),
            sleep=lambda seconds: None,
        )
        config = MarketDataConfig(rate_limit_max_weight=3)
        run = run_stream(
            fake,
            until=_MIDNIGHT + timedelta(minutes=30),
            pairs=[BTC_USDT, ETH_USDT],
            intervals=[_H4],
            config=config,
            limiter=limiter,
        )
        assert (40.0 in run.sleeper.calls, len(run.bars)) == (True, 2)

    def test_ample_bucket_adds_no_waits_beyond_the_boundary_schedule(self) -> None:
        """With the default 600 weight bucket the only sleeps are the waits for the boundaries."""
        run = run_stream(_fake(), until=_MIDNIGHT + timedelta(minutes=1))
        assert run.sleeper.calls == [3605.0, 14400.0]


class TestWireEdgeCases:
    """Header parsing, field validation, paging and logging limits."""

    @pytest.mark.parametrize(
        ("header", "expected_wait"),
        [("abc", 5.0), ("-3", 5.0), ("inf", 5.0), ("99999", 3600.0), ("2.5", 2.5)],
        ids=["not-a-number", "negative", "infinite", "capped-at-an-hour", "fractional"],
    )
    def test_retry_after_is_parsed_clamped_or_replaced_by_the_retry_interval(
        self, header: str, expected_wait: float
    ) -> None:
        """Retry-After seconds are honoured up to an hour; unusable values fall back to 5 s."""
        limited = _fake()
        limited.klines_script = [httpx.Response(429, headers={"Retry-After": header})]
        run = run_stream(limited, until=_MIDNIGHT + timedelta(hours=2))
        assert expected_wait in run.sleeper.calls

    @pytest.mark.parametrize(
        ("row", "message"),
        [
            ([0, "abc", "1", "1", "1", "1", 14_399_999], "not a decimal"),
            ([0, True, "1", "1", "1", "1", 14_399_999], "not string or numeric"),
            ([0, "1", "1", "2", "1", "1", 14_399_999], "domain invariants"),
            ([1, "1", "1", "1", "1", "1", 14_400_000], "aligned"),
        ],
        ids=["non-decimal-price", "bool-price", "low-above-high", "unaligned-open"],
    )
    def test_invalid_candle_fields_are_permanent_errors(
        self, row: list[object], message: str
    ) -> None:
        """A candle the domain would reject, or one off the interval grid, fails the source."""
        broken = _fake()
        broken.klines_script = [httpx.Response(200, json=[row])]
        run = run_stream(broken, until=_MIDNIGHT + timedelta(minutes=10))
        assert (message in str(run.error), run.adapter.health().state) == (
            True,
            SourceState.FAILED,
        )

    def test_a_malformed_forming_candle_does_not_fail_warm_up(self) -> None:
        """Issue #850: the forming candle is dropped unread, so the closed bar before it is kept."""
        closed_open = int(datetime(2026, 9, 30, 16, 0, tzinfo=UTC).timestamp() * 1000)
        forming_open = closed_open + 14_400_000
        fake = _fake()
        fake.klines_script = [
            httpx.Response(
                200,
                json=[
                    [closed_open, "1", "2", "1", "2", "3", forming_open - 1],
                    [forming_open, "2", "2", "2", "NaN", "1", forming_open + 14_399_999],
                ],
            )
        ]

        async def _drive() -> list[tuple[datetime, Decimal]]:
            with respx.mock(assert_all_called=False) as router:
                fake.install(router)
                adapter = build_adapter(fake.clock, SimulatedSleep(fake.clock))
                bars = await adapter.warm_up(BTC_USDT, _H4, 5)
                return [(bar.open_time, bar.close) for bar in bars]

        assert anyio.run(_drive) == [(datetime(2026, 9, 30, 16, 0, tzinfo=UTC), Decimal(2))]

    def test_invalid_used_weight_header_is_ignored(self) -> None:
        """A garbled x-mbx-used-weight-1m keeps the last good value instead of raising."""
        fake = _fake()
        fake.klines_script = [httpx.Response(200, json=[], headers={"x-mbx-used-weight-1m": "abc"})]

        async def _drive() -> int | None:
            with respx.mock(assert_all_called=False) as router:
                fake.install(router)
                adapter = build_adapter(fake.clock, SimulatedSleep(fake.clock))
                await adapter.warm_up(BTC_USDT, _H4, 5)
                return adapter.health().used_weight_1m

        assert anyio.run(_drive) == 1

    def test_create_builds_an_adapter_with_a_default_client_and_bucket(self) -> None:
        """create() wires an httpx client and a limiter from the config; health starts STARTING."""
        adapter = BinanceLiveKlinesAdapter.create(MarketDataConfig(), SimulatedClock(start=START))

        async def _drive() -> SourceState:
            state = adapter.health().state
            await adapter.aclose()
            return state

        assert anyio.run(_drive) == SourceState.STARTING

    def test_resume_far_behind_pages_through_the_backlog_in_klines_pages_of_1000(self) -> None:
        """A marker 1100 4h bars behind is healed with two pages, in order, nothing repeated."""
        marker = START - 1100 * interval_duration(_H4)
        fake = _fake()
        run = run_stream(
            fake,
            until=START + timedelta(minutes=1),
            pairs=[BTC_USDT],
            intervals=[_H4],
            resume_after={(BTC_USDT, _H4): marker},
        )
        opens = [bar.open_time for bar in run.bars]
        page_sizes = [r.url.params["limit"] for r in fake.klines_requests()]
        assert (len(opens), opens == sorted(set(opens)), page_sizes) == (
            1099,
            True,
            ["1000", "1000"],
        )

    def test_gap_log_is_aggregated_when_many_bars_are_skipped_at_once(self) -> None:
        """More than 20 skipped bars in one cycle log one summary line, not one line per bar."""
        marker = START - timedelta(days=11)
        gappy = _fake()
        first_missing = datetime(2026, 9, 21, 0, 0, tzinfo=UTC)
        gappy.missing = {("4h", first_missing + i * interval_duration(_H4)) for i in range(25)}
        with structlog.testing.capture_logs() as logs:
            run_stream(
                gappy,
                until=START + timedelta(minutes=1),
                pairs=[BTC_USDT],
                intervals=[_H4],
                resume_after={(BTC_USDT, _H4): marker},
            )
        summaries = [e for e in logs if e["event"] == "market_data.gaps_detected"]
        singles = [e for e in logs if e["event"] == "market_data.gap_detected"]
        assert ([e["count"] for e in summaries], singles) == ([25], [])
