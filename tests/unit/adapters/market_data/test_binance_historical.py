"""Unit tests for :class:`BinanceHistoricalAdapter`.

Covers:

- Configuration resolution (data_dir, env-var fallback).
- Parquet/CSV reader roundtrip via in-memory ZIP fixtures.
- Row-to-Bar parsing (happy path + malformed rows).
- Row-to-Trade parsing (happy path + is_buyer_maker variants).
- Streaming: missing directories raise InfraError.
- L2 methods raise NotImplementedError.
"""

from __future__ import annotations

import csv
import io
import zipfile
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import anyio
import pytest

from hermes.adapters.market_data.binance_historical import (
    BinanceHistoricalAdapter,
    BinanceHistoricalConfig,
    _read_csv_from_zip,
    _row_to_bar,
    _row_to_trade,
)
from hermes.domain.errors import ExchangeError, InfraError
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.market_data.trade import TradeSide
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair

_BTC_USDT = Pair(base=Currency.BTC, quote=Currency.USDT)


def _make_klines_zip(rows: list[list[str]], *, with_header: bool = False) -> bytes:
    """Build an in-memory Binance Vision klines ZIP+CSV.

    Args:
        rows: Data rows (without header).
        with_header: When True, prepend a header row.

    Returns:
        ZIP file bytes.

    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        csv_buf = io.StringIO()
        writer = csv.writer(csv_buf)
        if with_header:
            writer.writerow(
                [
                    "open_time",
                    "open",
                    "high",
                    "low",
                    "close",
                    "volume",
                    "close_time",
                    "quote_volume",
                    "count",
                    "taker_buy_volume",
                    "taker_buy_quote_volume",
                    "ignore",
                ]
            )
        for row in rows:
            writer.writerow(row)
        zf.writestr("BTCUSDT-1m-2024-01.csv", csv_buf.getvalue())
    return buf.getvalue()


def _make_agg_trades_zip(rows: list[list[str]]) -> bytes:
    """Build an in-memory Binance Vision aggTrades ZIP+CSV."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        csv_buf = io.StringIO()
        writer = csv.writer(csv_buf)
        for row in rows:
            writer.writerow(row)
        zf.writestr("BTCUSDT-aggTrades-2024-01.csv", csv_buf.getvalue())
    return buf.getvalue()


def _klines_row(
    open_time_ms: int = 1_704_067_200_000,  # 2024-01-01 00:00:00 UTC
    close_time_ms: int = 1_704_067_259_999,  # 2024-01-01 00:00:59.999 UTC
    open_p: str = "42000.00",
    high_p: str = "42100.00",
    low_p: str = "41900.00",
    close_p: str = "42050.00",
    volume: str = "10.5",
) -> list[str]:
    """Build a single valid klines row."""
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
        "5.25",
        "220762500",
        "0",
    ]


def _agg_trades_row(
    trade_id: str = "100000",
    price: str = "42000.00",
    qty: str = "0.5",
    transact_time_ms: int = 1_704_067_200_000,
    is_buyer_maker: str = "False",
) -> list[str]:
    """Build a single valid aggTrades row."""
    return [trade_id, price, qty, "99990", "100000", str(transact_time_ms), is_buyer_maker]


@pytest.mark.unit
def test_config_resolve_data_dir_explicit(tmp_path: Path) -> None:
    """Config uses the explicitly set data_dir when provided."""
    config = BinanceHistoricalConfig(data_dir=tmp_path)
    assert config.resolve_data_dir() == tmp_path, (
        f"Expected data_dir to be {tmp_path}, got {config.resolve_data_dir()}"
    )


@pytest.mark.unit
def test_config_resolve_data_dir_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    """Config falls back to HERMES_DATA_DIR env var when data_dir is None."""
    monkeypatch.setenv("HERMES_DATA_DIR", "/env/data")
    config = BinanceHistoricalConfig(data_dir=None)
    assert config.resolve_data_dir() == Path("/env/data"), (
        "Expected data_dir from HERMES_DATA_DIR env var"
    )


@pytest.mark.unit
def test_config_resolve_data_dir_missing_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """Config raises ValueError when neither data_dir nor env var is set."""
    monkeypatch.delenv("HERMES_DATA_DIR", raising=False)
    config = BinanceHistoricalConfig(data_dir=None)
    with pytest.raises(ValueError, match="HERMES_DATA_DIR"):
        config.resolve_data_dir()


@pytest.mark.unit
def test_read_csv_from_zip_returns_rows(tmp_path: Path) -> None:
    """_read_csv_from_zip returns the correct number of data rows."""
    rows = [
        _klines_row(),
        _klines_row(open_time_ms=1_704_067_260_000, close_time_ms=1_704_067_319_999),
    ]
    zip_bytes = _make_klines_zip(rows)
    zip_path = tmp_path / "test.zip"
    zip_path.write_bytes(zip_bytes)

    result = _read_csv_from_zip(zip_path)
    assert len(result) == 2, f"Expected 2 rows, got {len(result)}"


@pytest.mark.unit
def test_read_csv_from_zip_strips_header_row(tmp_path: Path) -> None:
    """_read_csv_from_zip skips the header row when present."""
    rows = [_klines_row()]
    zip_bytes = _make_klines_zip(rows, with_header=True)
    zip_path = tmp_path / "test_header.zip"
    zip_path.write_bytes(zip_bytes)

    result = _read_csv_from_zip(zip_path)
    assert len(result) == 1, f"Expected 1 data row after stripping header, got {len(result)}"
    # First field should be numeric (open_time_ms), not the header string.
    assert result[0][0].strip().isdigit(), (
        f"First field of first row should be numeric, got {result[0][0]!r}"
    )


@pytest.mark.unit
def test_read_csv_from_zip_bad_zip_raises_infra_error(tmp_path: Path) -> None:
    """_read_csv_from_zip raises InfraError on corrupt ZIP."""
    zip_path = tmp_path / "corrupt.zip"
    zip_path.write_bytes(b"this is not a zip file")

    with pytest.raises(InfraError, match="corrupt ZIP"):
        _read_csv_from_zip(zip_path)


@pytest.mark.unit
def test_read_csv_from_zip_empty_zip_raises_infra_error(tmp_path: Path) -> None:
    """_read_csv_from_zip raises InfraError when ZIP has no CSV file."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("not_a_csv.txt", "irrelevant content")
    zip_path = tmp_path / "no_csv.zip"
    zip_path.write_bytes(buf.getvalue())

    with pytest.raises(InfraError, match="no CSV"):
        _read_csv_from_zip(zip_path)


@pytest.mark.unit
def test_row_to_bar_happy_path(tmp_path: Path) -> None:
    """_row_to_bar correctly parses a valid klines row."""
    row = _klines_row()
    source = tmp_path / "test.zip"
    bar = _row_to_bar(row=row, pair=_BTC_USDT, interval=KlineInterval.M1, source=source)

    assert bar.pair == _BTC_USDT, f"bar.pair should be {_BTC_USDT}, got {bar.pair}"
    assert bar.interval == KlineInterval.M1, f"bar.interval should be M1, got {bar.interval}"
    assert bar.open == Decimal("42000.00"), f"bar.open should be 42000.00, got {bar.open}"
    assert bar.high == Decimal("42100.00"), f"bar.high should be 42100.00, got {bar.high}"
    assert bar.low == Decimal("41900.00"), f"bar.low should be 41900.00, got {bar.low}"
    assert bar.close == Decimal("42050.00"), f"bar.close should be 42050.00, got {bar.close}"
    assert bar.volume == Decimal("10.5"), f"bar.volume should be 10.5, got {bar.volume}"


@pytest.mark.unit
def test_row_to_bar_timestamps_are_utc(tmp_path: Path) -> None:
    """_row_to_bar converts ms timestamps to UTC-aware datetimes."""
    row = _klines_row(open_time_ms=1_704_067_200_000, close_time_ms=1_704_067_259_999)
    source = tmp_path / "test.zip"
    bar = _row_to_bar(row=row, pair=_BTC_USDT, interval=KlineInterval.M1, source=source)

    assert bar.open_time.tzinfo is not None, "bar.open_time must be timezone-aware"
    assert bar.open_time.tzinfo.utcoffset(None).total_seconds() == 0, "bar.open_time must be UTC"
    expected_open = datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)
    assert bar.open_time == expected_open, (
        f"bar.open_time should be {expected_open}, got {bar.open_time}"
    )


@pytest.mark.unit
def test_row_to_bar_too_few_fields_raises_exchange_error(tmp_path: Path) -> None:
    """_row_to_bar raises ExchangeError when row has fewer than 7 fields."""
    row = ["1704067200000", "42000", "42100"]  # only 3 fields
    source = tmp_path / "test.zip"
    with pytest.raises(ExchangeError, match="fields"):
        _row_to_bar(row=row, pair=_BTC_USDT, interval=KlineInterval.M1, source=source)


@pytest.mark.unit
def test_row_to_bar_bad_decimal_raises_exchange_error(tmp_path: Path) -> None:
    """_row_to_bar raises ExchangeError when a price field is not a decimal."""
    row = _klines_row()
    row[1] = "not_a_number"  # corrupt open price
    source = tmp_path / "test.zip"
    with pytest.raises(ExchangeError, match="decimal"):
        _row_to_bar(row=row, pair=_BTC_USDT, interval=KlineInterval.M1, source=source)


@pytest.mark.unit
def test_row_to_bar_bad_timestamp_raises_exchange_error(tmp_path: Path) -> None:
    """_row_to_bar raises ExchangeError when open_time is not an integer."""
    row = _klines_row()
    row[0] = "not_a_timestamp"
    source = tmp_path / "test.zip"
    with pytest.raises(ExchangeError, match="integer"):
        _row_to_bar(row=row, pair=_BTC_USDT, interval=KlineInterval.M1, source=source)


@pytest.mark.unit
def test_row_to_trade_buy_side(tmp_path: Path) -> None:
    """_row_to_trade: is_buyer_maker=False → TradeSide.BUY (taker is buyer)."""
    row = _agg_trades_row(is_buyer_maker="False")
    source = tmp_path / "test.zip"
    trade = _row_to_trade(row=row, pair=_BTC_USDT, source=source)
    assert trade.side == TradeSide.BUY, (
        f"is_buyer_maker=False should produce TradeSide.BUY, got {trade.side}"
    )


@pytest.mark.unit
def test_row_to_trade_sell_side(tmp_path: Path) -> None:
    """_row_to_trade: is_buyer_maker=True → TradeSide.SELL (taker is seller)."""
    row = _agg_trades_row(is_buyer_maker="True")
    source = tmp_path / "test.zip"
    trade = _row_to_trade(row=row, pair=_BTC_USDT, source=source)
    assert trade.side == TradeSide.SELL, (
        f"is_buyer_maker=True should produce TradeSide.SELL, got {trade.side}"
    )


@pytest.mark.unit
def test_row_to_trade_correct_fields(tmp_path: Path) -> None:
    """_row_to_trade correctly parses price, qty, trade_time, trade_id."""
    row = _agg_trades_row(
        trade_id="999",
        price="42500.00",
        qty="1.25",
        transact_time_ms=1_704_067_200_000,
    )
    source = tmp_path / "test.zip"
    trade = _row_to_trade(row=row, pair=_BTC_USDT, source=source)

    assert trade.trade_id == "999", f"trade_id should be '999', got {trade.trade_id}"
    assert trade.price == Decimal("42500.00"), f"price should be 42500.00, got {trade.price}"
    assert trade.quantity == Decimal("1.25"), f"quantity should be 1.25, got {trade.quantity}"
    expected_time = datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)
    assert trade.trade_time == expected_time, (
        f"trade_time should be {expected_time}, got {trade.trade_time}"
    )


@pytest.mark.unit
def test_row_to_trade_too_few_fields_raises_exchange_error(tmp_path: Path) -> None:
    """_row_to_trade raises ExchangeError when row has fewer than 7 fields."""
    row = ["100000", "42000", "1.0"]  # only 3 fields
    source = tmp_path / "test.zip"
    with pytest.raises(ExchangeError, match="fields"):
        _row_to_trade(row=row, pair=_BTC_USDT, source=source)


@pytest.mark.unit
def test_row_to_trade_bad_is_buyer_maker_raises_exchange_error(tmp_path: Path) -> None:
    """_row_to_trade raises ExchangeError on unexpected is_buyer_maker value."""
    row = _agg_trades_row(is_buyer_maker="maybe")
    source = tmp_path / "test.zip"
    with pytest.raises(ExchangeError, match="is_buyer_maker"):
        _row_to_trade(row=row, pair=_BTC_USDT, source=source)


@pytest.mark.unit
def test_stream_bars_missing_dir_raises_infra_error(tmp_path: Path) -> None:
    """stream_bars raises InfraError when the klines directory does not exist."""
    adapter = BinanceHistoricalAdapter.create(data_dir=tmp_path)

    async def _collect() -> None:
        gen = adapter.stream_bars(_BTC_USDT, KlineInterval.M1)
        with pytest.raises(InfraError, match="klines directory not found"):
            async for _ in gen:
                pass

    anyio.run(_collect)


@pytest.mark.unit
def test_stream_trades_missing_dir_raises_infra_error(tmp_path: Path) -> None:
    """stream_trades raises InfraError when the agg_trades directory does not exist."""
    adapter = BinanceHistoricalAdapter.create(data_dir=tmp_path)

    async def _collect() -> None:
        gen = adapter.stream_trades(_BTC_USDT)
        with pytest.raises(InfraError, match="agg_trades directory not found"):
            async for _ in gen:
                pass

    anyio.run(_collect)


@pytest.mark.unit
def test_stream_orderbook_diffs_raises_not_implemented(tmp_path: Path) -> None:
    """stream_orderbook_diffs raises NotImplementedError for historical adapter."""
    adapter = BinanceHistoricalAdapter.create(data_dir=tmp_path)

    async def _run() -> None:
        with pytest.raises(NotImplementedError):
            async for _ in adapter.stream_orderbook_diffs(_BTC_USDT):
                pass

    anyio.run(_run)


@pytest.mark.unit
def test_get_snapshot_raises_not_implemented(tmp_path: Path) -> None:
    """get_snapshot raises NotImplementedError for historical adapter."""
    adapter = BinanceHistoricalAdapter.create(data_dir=tmp_path)

    async def _run() -> None:
        with pytest.raises(NotImplementedError):
            await adapter.get_snapshot(_BTC_USDT)

    anyio.run(_run)
