"""Integration tests for the live klines adapter at the HTTP seam.

Tier 2: ``respx`` replaces the network, everything above it is the real
adapter. Two kinds of evidence:

- **Recorded fixtures** (``tests/fixtures/binance_public/``, recorded on
  2026-10-02 with read-only GETs to ``data-api.binance.vision``): the adapter
  parses real wire rows, drops the forming candle, and reads Binance's
  inclusive ``close_time``.
- **A two-day simulation with an outage**: the post-outage delivery sequence
  equals the no-outage one (change D-5, "backfill leaves no gap").

Unit-level invariants live in
``tests/unit/adapters/market_data/test_binance_live_klines.py``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import anyio
import httpx
import pytest
import respx

from hermes.adapters.clock import SimulatedClock
from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.kline import KlineInterval
from tests.support.paths import REPO_ROOT
from tests.unit.adapters.market_data.fake_binance_public import (
    BTC_USDT,
    KLINES_URL,
    START,
    TIME_URL,
    FakeBinancePublic,
    SimulatedSleep,
    build_adapter,
    keys,
    run_stream,
)

pytestmark = pytest.mark.integration

_FIXTURES = REPO_ROOT / "tests" / "fixtures" / "binance_public"
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_MIDNIGHT = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)


def _fixture(name: str) -> object:
    """Load a recorded response body."""
    return json.loads((_FIXTURES / name).read_text(encoding="utf-8"))


def _recorded_server_time() -> datetime:
    """Return the exchange clock of the recorded ``/time`` response."""
    body = _fixture("time.json")
    assert isinstance(body, dict)
    return _EPOCH + timedelta(milliseconds=int(body["serverTime"]))


def _warm_up_from_fixture(
    klines_fixture: str, interval: KlineInterval, count: int, *, now: datetime
) -> tuple[list[Bar], httpx.Request]:
    """Warm up against a recorded klines body; return the bars and the klines request."""
    clock = SimulatedClock(start=now)

    async def _drive() -> tuple[list[Bar], httpx.Request]:
        with respx.mock(assert_all_called=False) as router:
            router.get(TIME_URL).mock(
                return_value=httpx.Response(
                    200, json={"serverTime": (now - _EPOCH) // timedelta(milliseconds=1)}
                )
            )
            klines = router.get(KLINES_URL).mock(
                return_value=httpx.Response(200, json=_fixture(klines_fixture))
            )
            adapter = build_adapter(clock, SimulatedSleep(clock))
            bars = await adapter.warm_up(BTC_USDT, interval, count)
            return bars, klines.calls.last.request

    return anyio.run(_drive)


class TestRecordedFixtures:
    """Real wire rows parse into the bars the strategy and the Vision archives expect."""

    def test_recorded_4h_sample_drops_the_forming_candle_and_keeps_wire_values(self) -> None:
        """At 22:10:57 the 20:00 candle is forming; 12:00 and 16:00 come back with exact values."""
        bars, request = _warm_up_from_fixture(
            "klines_btcusdt_4h_limit3.json", KlineInterval.H4, 2, now=_recorded_server_time()
        )
        assert [b.open_time for b in bars] == [
            datetime(2026, 10, 2, 12, 0, tzinfo=UTC),
            datetime(2026, 10, 2, 16, 0, tzinfo=UTC),
        ]
        assert (bars[0].low, bars[0].volume) == (
            Decimal("85162.90000000"),
            Decimal("6577.58948000"),
        )
        assert dict(request.url.params) == {"symbol": "BTCUSDT", "interval": "4h", "limit": "3"}

    def test_recorded_1d_head_has_inclusive_close_times_matching_the_wire(self) -> None:
        """Every recorded 1d row's close_time equals open + 1 day - 1 ms, as the archives carry."""
        rows = _fixture("klines_btcusdt_1d_head.json")
        assert isinstance(rows, list)
        bars, _ = _warm_up_from_fixture(
            "klines_btcusdt_1d_head.json", KlineInterval.D1, 5, now=_recorded_server_time()
        )
        wire_close_times = [_EPOCH + timedelta(milliseconds=row[6]) for row in rows]
        assert [b.close_time for b in bars] == wire_close_times

    def test_recorded_1d_head_is_returned_oldest_first(self) -> None:
        """Warm-up hands bars over in ascending open_time, the order buffers are filled in."""
        bars, _ = _warm_up_from_fixture(
            "klines_btcusdt_1d_head.json", KlineInterval.D1, 5, now=_recorded_server_time()
        )
        assert [b.open_time for b in bars] == sorted(b.open_time for b in bars)


class TestOutageScenario:
    """Two simulated days, a 90 minute outage across a 4h boundary."""

    def test_90_minute_outage_across_a_boundary_yields_the_same_sequence(self) -> None:
        """The post-outage sequence equals the no-outage one (4h bar at 04:00 delayed 90 min)."""
        faulty = FakeBinancePublic(SimulatedClock(start=START))
        faulty.outages = [
            (_MIDNIGHT + timedelta(hours=3), _MIDNIGHT + timedelta(hours=4, minutes=30))
        ]
        reference = FakeBinancePublic(SimulatedClock(start=START))
        outage_run = run_stream(faulty, until=START + timedelta(days=2))
        reference_run = run_stream(reference, until=START + timedelta(days=2))
        assert keys(outage_run.bars) == keys(reference_run.bars)
