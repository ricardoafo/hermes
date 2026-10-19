"""The fake market replays recorded Train-window bars and nothing later.

Issue #971, part 2. Invariants protected here:

- a replay serves exactly the recorded 4h bars that open inside its window,
  over the socket in the production wire format, and a 1d bar only for a UTC
  day with all six of its 4h bars;
- a window that reaches the Validation start (2024-07-01) is refused, by one
  millisecond as by a year, and a refused load leaves the old market serving;
- a Validation-window archive beside the Train ones is never opened;
- a pair with no recorded bars in the window, or a start off 00:00 UTC, is
  refused with a message naming the problem.

Every price below is invented; the archives are written by the test in the
Binance Vision layout, so no recorded market data is read.
"""

import csv
import io
import zipfile
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.value_objects.pair import Pair
from tests.support.fake_market import wire
from tests.support.fake_market.exchange import FakeExchange, SimClock
from tests.support.fake_market.replay import REPLAY_LIMIT, ReplayError, market_from_recording
from tests.support.fake_market.script import H4, MarketScript
from tests.support.fake_market.service import FakeMarketService, market_from_spec
from tests.support.serve_loop import BTC, ETH

pytestmark = pytest.mark.integration

VALIDATION_START = datetime(2024, 7, 1, tzinfo=UTC)
FIRST_OPEN = datetime(2024, 6, 28, tzinfo=UTC)
"""Open of the first recorded bar: 15 bars run to 2024-06-30 08:00, a partial day."""
RECORDED = 15


def _ms(at: datetime) -> int:
    return int(at.timestamp() * 1000)


def _row(open_time: datetime, close: Decimal) -> list[object]:
    return [
        _ms(open_time),
        close - 1,
        close + 2,
        close - 3,
        close,
        "12.5",
        _ms(open_time + H4) - 1,
        "1250",
        42,
        "6",
        "600",
        "0",
    ]


def _write_month(root: Path, symbol: str, year: int, month: int, rows: list[list[object]]) -> Path:
    folder = root / "klines" / symbol / "4h" / f"{year:04d}" / f"{month:02d}"
    folder.mkdir(parents=True)
    text = io.StringIO()
    csv.writer(text).writerows(rows)
    path = folder / f"{symbol}-4h-{year:04d}-{month:02d}.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(f"{symbol}-4h-{year:04d}-{month:02d}.csv", text.getvalue())
    return path


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    """Write 15 invented BTC 4h bars in June 2024 and an unreadable July archive.

    The July file is not a zip: reading it raises, so a replay that succeeds
    proves the reader never opened it.
    """
    rows = [_row(FIRST_OPEN + H4 * i, Decimal(100 + i)) for i in range(RECORDED)]
    _write_month(tmp_path, "BTCUSDT", 2024, 6, rows)
    july = tmp_path / "klines" / "BTCUSDT" / "4h" / "2024" / "07"
    july.mkdir(parents=True)
    (july / "BTCUSDT-4h-2024-07.zip").write_bytes(b"validation window, never read")
    return tmp_path


def _recorded_bar(index: int) -> Bar:
    open_time = FIRST_OPEN + H4 * index
    close = Decimal(100 + index)
    return Bar(
        pair=BTC,
        interval=KlineInterval.H4,
        open_time=open_time,
        open=close - 1,
        high=close + 2,
        low=close - 3,
        close=close,
        volume=Decimal("12.5"),
        close_time=open_time + H4 - timedelta(milliseconds=1),
    )


@pytest.fixture
def service() -> Iterator[FakeMarketService]:
    """Serve a one-day scripted market, to be replaced by a replay."""
    script = MarketScript(start=datetime(2023, 1, 2, tzinfo=UTC), prices={ETH: Decimal(50)})
    market = script.hold(ETH, days=1).build()
    exchange = FakeExchange(market=market, clock=SimClock(market.start, market.end))
    with FakeMarketService(exchange) as running:
        yield running


def _replay_spec(data_dir: Path, end: datetime = VALIDATION_START) -> dict[str, object]:
    return {
        "replay": {
            "dir": str(data_dir),
            "pairs": ["BTC/USDT"],
            "start": FIRST_OPEN.isoformat(),
            "end": end.isoformat(),
        },
        "now": VALIDATION_START.isoformat(),
    }


def test_the_replay_limit_is_the_signed_validation_start() -> None:
    """Nothing from 2024-07-01 on replays: the limit is pre-registration §4's Validation start."""
    assert datetime(2024, 7, 1, tzinfo=UTC) == REPLAY_LIMIT


def test_a_replay_holds_exactly_the_recorded_bars_in_its_window(data_dir: Path) -> None:
    """Bars 6 to 12 open inside the window; the ones before and after it are left out."""
    start = FIRST_OPEN + timedelta(days=1)
    end = FIRST_OPEN + H4 * 13
    market = market_from_recording(data_dir, [BTC], start, end)
    assert market.h4[BTC] == [_recorded_bar(i) for i in range(6, 13)]


def test_a_day_gets_a_1d_bar_only_with_all_six_of_its_4h_bars(data_dir: Path) -> None:
    """28 and 29 June are whole; 30 June has three bars, so it has no daily bar."""
    market = market_from_recording(data_dir, [BTC], FIRST_OPEN, VALIDATION_START)
    assert [(b.open_time, b.open, b.high, b.low, b.close) for b in market.d1[BTC]] == [
        (FIRST_OPEN, Decimal(99), Decimal(107), Decimal(97), Decimal(105)),
        (FIRST_OPEN + timedelta(days=1), Decimal(105), Decimal(113), Decimal(103), Decimal(111)),
    ]


def test_the_socket_serves_the_replayed_bars_in_the_production_wire_format(
    service: FakeMarketService, data_dir: Path
) -> None:
    """Loaded through the control API, the replay answers klines with the recorded rows."""
    loaded = httpx.post(f"{service.url}/_fake/script", json=_replay_spec(data_dir), timeout=5)
    rows = httpx.get(
        f"{service.binance_base}/api/v3/klines",
        params={"symbol": "BTCUSDT", "interval": "4h", "limit": "1000"},
        timeout=5,
    ).json()
    assert (loaded.status_code, rows) == (
        200,
        [wire.kline_row(_recorded_bar(i)) for i in range(RECORDED)],
    )


@pytest.mark.parametrize(
    "end",
    [VALIDATION_START + timedelta(milliseconds=1), datetime(2025, 7, 1, tzinfo=UTC)],
)
def test_a_window_reaching_the_validation_start_is_refused_and_the_market_stays(
    service: FakeMarketService, data_dir: Path, end: datetime
) -> None:
    """One millisecond past 2024-07-01 is refused as a year past is; the old market serves on."""
    before = service.exchange.market
    response = httpx.post(
        f"{service.url}/_fake/script", json=_replay_spec(data_dir, end), timeout=5
    )
    assert (response.status_code, response.json(), service.exchange.market) == (
        400,
        {
            "error": f"replay end {end.isoformat()} reaches the Validation window, which "
            "starts 2024-07-01T00:00:00+00:00: only Train-window bars replay"
        },
        before,
    )


def test_a_validation_archive_beside_the_train_ones_is_never_opened(data_dir: Path) -> None:
    """The unreadable July archive fails any read of it, so this load proves it stayed shut."""
    market = market_from_spec(_replay_spec(data_dir))
    assert len(market.h4[BTC]) == RECORDED


def test_an_archive_inside_the_window_that_fails_to_read_is_refused(data_dir: Path) -> None:
    """The July file is unreadable; the same failure inside a window is a refusal, not a crash."""
    july = data_dir / "klines" / "BTCUSDT" / "4h" / "2024" / "07" / "BTCUSDT-4h-2024-07.zip"
    june = data_dir / "klines" / "BTCUSDT" / "4h" / "2024" / "06" / "BTCUSDT-4h-2024-06.zip"
    june.write_bytes(july.read_bytes())
    with pytest.raises(ReplayError, match="recorded BTC/USDT bars failed to read"):
        market_from_recording(data_dir, [BTC], FIRST_OPEN, VALIDATION_START)


def test_a_replay_without_a_directory_is_a_400(service: FakeMarketService, data_dir: Path) -> None:
    """The control API names the missing field before anything is read."""
    spec = _replay_spec(data_dir)
    replay = spec["replay"]
    assert isinstance(replay, dict)
    del replay["dir"]
    response = httpx.post(f"{service.url}/_fake/script", json=spec, timeout=5)
    assert (response.status_code, response.json()) == (
        400,
        {"error": "replay dir must be a path, got None"},
    )


@pytest.mark.parametrize(
    ("pairs", "start", "error"),
    [
        (
            [ETH],
            FIRST_OPEN,
            "no recorded 4h bars for ETH/USDT from 2024-06-28T00:00:00+00:00 "
            "to 2024-07-01T00:00:00+00:00 in ",
        ),
        ([BTC], FIRST_OPEN + H4, "replay start must be 00:00 UTC, got 2024-06-28T04:00:00+00:00"),
        ([], FIRST_OPEN, "replay needs at least one pair"),
        (
            [BTC],
            VALIDATION_START,
            "replay end 2024-07-01T00:00:00+00:00 is not after start 2024-07-01T00:00:00+00:00",
        ),
    ],
)
def test_a_malformed_replay_is_refused_naming_the_problem(
    data_dir: Path, pairs: list[Pair], start: datetime, error: str
) -> None:
    """Each refusal says what is wrong, so a scenario author can fix the spec."""
    with pytest.raises(ReplayError) as refused:
        market_from_recording(data_dir, pairs, start, VALIDATION_START)
    assert str(refused.value).startswith(error)
