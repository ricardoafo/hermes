"""Integration tests for :class:`BinanceHistoricalAdapter`.

These tests verify the full adapter + fixture Parquet/CSV → bars/trades
roundtrip, including multi-file scenarios and the consolidator integration.

The fixtures are in-memory ZIP files written to a temporary directory —
no real network, no real filesystem mounts, but the full adapter code path
is exercised (including ZIP iteration, CSV parsing, domain construction).

Test pyramid: integration tier (marker: ``integration``).
"""

from __future__ import annotations

import csv
import io
import zipfile
from decimal import Decimal
from pathlib import Path

import anyio
import pytest

from hermes.adapters.market_data.binance_historical import BinanceHistoricalAdapter
from hermes.adapters.market_data.consolidator import BarConsolidator
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair

_BTC_USDT = Pair(base=Currency.BTC, quote=Currency.USDT)


def _write_klines_zip(
    directory: Path,
    filename: str,
    rows: list[list[str]],
) -> Path:
    """Write a Binance Vision klines ZIP to ``directory``."""
    directory.mkdir(parents=True, exist_ok=True)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        csv_buf = io.StringIO()
        writer = csv.writer(csv_buf)
        for row in rows:
            writer.writerow(row)
        zf.writestr(filename.replace(".zip", ".csv"), csv_buf.getvalue())
    zip_path = directory / filename
    zip_path.write_bytes(buf.getvalue())
    return zip_path


def _write_agg_trades_zip(
    directory: Path,
    filename: str,
    rows: list[list[str]],
) -> Path:
    """Write a Binance Vision aggTrades ZIP to ``directory``."""
    directory.mkdir(parents=True, exist_ok=True)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        csv_buf = io.StringIO()
        writer = csv.writer(csv_buf)
        for row in rows:
            writer.writerow(row)
        zf.writestr(filename.replace(".zip", ".csv"), csv_buf.getvalue())
    zip_path = directory / filename
    zip_path.write_bytes(buf.getvalue())
    return zip_path


def _klines_row(
    open_time_ms: int,
    close_time_ms: int,
    open_p: str = "42000.00",
    high_p: str = "42100.00",
    low_p: str = "41900.00",
    close_p: str = "42050.00",
    volume: str = "10.0",
) -> list[str]:
    return [
        str(open_time_ms),
        open_p,
        high_p,
        low_p,
        close_p,
        volume,
        str(close_time_ms),
        "441525000",
        "500",
        "5.0",
        "210000000",
        "0",
    ]


def _agg_trades_row(
    trade_id: str,
    price: str,
    qty: str,
    transact_time_ms: int,
    is_buyer_maker: str = "False",
) -> list[str]:
    return [trade_id, price, qty, "0", trade_id, str(transact_time_ms), is_buyer_maker]


@pytest.mark.integration
def test_stream_bars_yields_bars_from_zip_fixture(tmp_path: Path) -> None:
    """Adapter + ZIP fixture → bars roundtrip: yields correct bar count and values."""
    pair_symbol = "BTCUSDT"
    interval_str = "1m"
    klines_dir = tmp_path / "klines" / pair_symbol / interval_str / "2024" / "01"

    # 2 klines rows — 2 minutes
    rows = [
        _klines_row(
            open_time_ms=1_704_067_200_000,  # 2024-01-01 00:00:00 UTC
            close_time_ms=1_704_067_259_999,  # 2024-01-01 00:00:59.999 UTC
        ),
        _klines_row(
            open_time_ms=1_704_067_260_000,  # 2024-01-01 00:01:00 UTC
            close_time_ms=1_704_067_319_999,  # 2024-01-01 00:01:59.999 UTC
            open_p="42100.00",
            high_p="42200.00",
            low_p="42000.00",
            close_p="42150.00",
            volume="5.0",
        ),
    ]
    _write_klines_zip(klines_dir, "BTCUSDT-1m-2024-01.zip", rows)

    adapter = BinanceHistoricalAdapter.create(data_dir=tmp_path)

    async def _collect() -> list:
        return [bar async for bar in adapter.stream_bars(_BTC_USDT, KlineInterval.M1)]

    bars = anyio.run(_collect)
    assert len(bars) == 2, f"Expected 2 bars from 2 klines rows, got {len(bars)}"
    assert bars[0].open == Decimal("42000.00"), (
        f"First bar open should be 42000.00, got {bars[0].open}"
    )
    assert bars[1].open == Decimal("42100.00"), (
        f"Second bar open should be 42100.00, got {bars[1].open}"
    )
    assert bars[0].open_time < bars[1].open_time, "Bars should be in ascending open_time order"


@pytest.mark.integration
def test_stream_bars_multiple_zip_files_merged_in_order(tmp_path: Path) -> None:
    """Adapter yields bars from multiple ZIP files in chronological order."""
    pair_symbol = "BTCUSDT"
    interval_str = "1m"
    klines_dir_jan = tmp_path / "klines" / pair_symbol / interval_str / "2024" / "01"
    klines_dir_feb = tmp_path / "klines" / pair_symbol / interval_str / "2024" / "02"

    _write_klines_zip(
        klines_dir_jan,
        "BTCUSDT-1m-2024-01.zip",
        [_klines_row(open_time_ms=1_704_067_200_000, close_time_ms=1_704_067_259_999)],
    )
    _write_klines_zip(
        klines_dir_feb,
        "BTCUSDT-1m-2024-02.zip",
        [_klines_row(open_time_ms=1_706_745_600_000, close_time_ms=1_706_745_659_999)],
    )

    adapter = BinanceHistoricalAdapter.create(data_dir=tmp_path)

    async def _collect() -> list:
        return [bar async for bar in adapter.stream_bars(_BTC_USDT, KlineInterval.M1)]

    bars = anyio.run(_collect)
    assert len(bars) == 2, f"Expected 2 bars from 2 ZIP files, got {len(bars)}"
    assert bars[0].open_time < bars[1].open_time, (
        "Bars from multiple ZIP files must be in chronological order"
    )


@pytest.mark.integration
def test_adapter_to_consolidator_pipeline(tmp_path: Path) -> None:
    """Integration: historical adapter → consolidator pipeline produces H1 bars."""
    pair_symbol = "BTCUSDT"
    trades_dir = tmp_path / "agg_trades" / pair_symbol / "2024" / "01"

    # Build trades spanning 2 full H1 windows (00:00 and 01:00 UTC)
    trade_rows = [
        # Window 00:00-01:00 UTC (first hour)
        _agg_trades_row("1", "42000.00", "1.0", 1_704_067_200_000, "False"),  # 00:00:00
        _agg_trades_row("2", "42100.00", "2.0", 1_704_068_000_000, "True"),  # 00:13:20
        _agg_trades_row("3", "41800.00", "0.5", 1_704_069_000_000, "False"),  # 00:30:00
        # Window 01:00-02:00 UTC (second hour)
        _agg_trades_row("4", "42500.00", "1.5", 1_704_070_800_000, "False"),  # 01:00:00
        _agg_trades_row("5", "42600.00", "0.8", 1_704_071_000_000, "True"),  # 01:03:20
    ]
    _write_agg_trades_zip(trades_dir, "BTCUSDT-aggTrades-2024-01.zip", trade_rows)

    adapter = BinanceHistoricalAdapter.create(data_dir=tmp_path)

    async def _collect_trades() -> list:
        return [trade async for trade in adapter.stream_trades(_BTC_USDT)]

    trades = anyio.run(_collect_trades)
    assert len(trades) == 5, f"Expected 5 trades from fixture, got {len(trades)}"

    consolidator = BarConsolidator(pair=_BTC_USDT, interval=KlineInterval.H1)
    bars = list(consolidator.consolidate(trades))

    assert len(bars) == 2, f"Expected 2 H1 bars from 5 trades spanning 2 hours, got {len(bars)}"

    bar0 = bars[0]
    assert bar0.open == Decimal("42000.00"), (
        f"First H1 bar open should be 42000.00 (first trade in window), got {bar0.open}"
    )
    assert bar0.high == Decimal("42100.00"), (
        f"First H1 bar high should be 42100.00 (max price in window), got {bar0.high}"
    )
    assert bar0.low == Decimal("41800.00"), (
        f"First H1 bar low should be 41800.00 (min price in window), got {bar0.low}"
    )
    assert bar0.volume == Decimal("3.5"), (
        f"First H1 bar volume should be 3.5 (1.0+2.0+0.5), got {bar0.volume}"
    )

    bar1 = bars[1]
    assert bar1.open == Decimal("42500.00"), (
        f"Second H1 bar open should be 42500.00, got {bar1.open}"
    )
    assert bar1.volume == Decimal("2.3"), (
        f"Second H1 bar volume should be 2.3 (1.5+0.8), got {bar1.volume}"
    )
