"""Binance Vision filesystem-backed MarketDataPort adapter.

Implements :class:`hermes.ports.market_data.MarketDataPort` against locally
ingested Binance Vision klines and aggTrades ZIP archives. No network I/O —
all data is read from ``$HERMES_DATA_DIR`` (the directory set by the
environment variable of that name, falling back to a provided ``data_dir``
path parameter).

Data layout (per data-ingest-plan.md §1.1):

- Klines: ``<data_dir>/klines/<pair>/<interval>/<yyyy>/<mm>/*.zip``
  Each ZIP contains a single CSV with columns:
  ``open_time, open, high, low, close, volume, close_time,
  quote_volume, count, taker_buy_volume, taker_buy_quote_volume, ignore``
  Timestamps are milliseconds since epoch (UTC).

- AggTrades: ``<data_dir>/agg_trades/<pair>/<yyyy>/<mm>/*.zip``
  Each ZIP contains a single CSV with columns:
  ``agg_trade_id, price, qty, first_trade_id, last_trade_id, transact_time, is_buyer_maker``
  ``transact_time`` is milliseconds since epoch (UTC).

Design decisions:

- D-1: Pure filesystem reads — no network, no async HTTP. All I/O is
  synchronous stdlib (``zipfile``, ``csv``). The stream methods are async
  generators that ``yield`` domain objects; they do NOT open network sockets.
- D-4: L2 methods (``stream_orderbook_diffs``, ``get_snapshot``) raise
  ``NotImplementedError`` — historical archives carry no L2 depth data.
- Bar streaming (``stream_bars``) reads klines CSV rows directly from the
  ZIP and yields :class:`~hermes.domain.market_data.bar.Bar` objects.
  Trade streaming (``stream_trades``) reads aggTrades rows.
- Files are iterated in lexicographic order (year/month/filename) which is
  also chronological for the Binance Vision naming convention.
- Malformed or missing data raises
  :class:`~hermes.domain.errors.InfraError`.

Reference: spec-4c-market-data-adapters change.md D-1, D-4.
"""

from __future__ import annotations

import csv
import io
import os
import zipfile
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Final

import structlog

from hermes.domain.errors import ExchangeError, InfraError
from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.market_data.orderbook import Orderbook
from hermes.domain.market_data.orderbook_diff import OrderbookDiff
from hermes.domain.market_data.trade import Trade, TradeSide
from hermes.domain.value_objects.pair import Pair

__all__ = ["BinanceHistoricalAdapter", "BinanceHistoricalConfig"]

_MS_PER_SECOND: Final[int] = 1_000
_US_PER_SECOND: Final[int] = 1_000_000

# Binance changed the klines CSV archive format starting 2025-01: the
# `open_time` / `close_time` fields went from milliseconds (13-digit
# unix timestamps) to microseconds (16-digit). This adapter detects the
# unit at parse time from the string length. See #271.
_EPOCH_MS_DIGITS: Final[int] = 13
_EPOCH_US_DIGITS: Final[int] = 16
# Minimum CSV columns expected per klines row (Binance Vision spec).
_KLINES_MIN_COLS: Final[int] = 7
# Minimum CSV columns expected per aggTrades row.
_AGG_TRADES_MIN_COLS: Final[int] = 7

_log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class BinanceHistoricalConfig:
    """Wire configuration for :class:`BinanceHistoricalAdapter`.

    Attributes:
        data_dir: Root directory where Binance Vision archives live. When
            ``None``, the adapter resolves the directory from the
            ``HERMES_DATA_DIR`` environment variable at construction time.
            If neither is set a :class:`ValueError` is raised.
    """

    data_dir: Path | None = None

    def resolve_data_dir(self) -> Path:
        """Return the resolved data directory path.

        Resolution order:

        1. ``self.data_dir`` when explicitly set.
        2. ``HERMES_DATA_DIR`` environment variable.

        Returns:
            Resolved :class:`~pathlib.Path`.

        Raises:
            ValueError: When neither ``data_dir`` nor ``HERMES_DATA_DIR``
                is set.
        """
        if self.data_dir is not None:
            return self.data_dir
        env_val = os.environ.get("HERMES_DATA_DIR")
        if not env_val:
            raise ValueError(
                "BinanceHistoricalConfig: data_dir not set and HERMES_DATA_DIR env var is missing"
            )
        return Path(env_val)


@dataclass(slots=True)
class BinanceHistoricalAdapter:
    """Filesystem-backed MarketDataPort reading Binance Vision archives.

    Attributes:
        config: Wire configuration (data directory).
        _data_dir: Resolved data directory (lazy — populated on first use).
    """

    config: BinanceHistoricalConfig
    _data_dir: Path | None = field(default=None, init=False, repr=False)

    @classmethod
    def create(cls, data_dir: Path | None = None) -> BinanceHistoricalAdapter:
        """Build an adapter.

        Args:
            data_dir: Explicit data directory. When ``None`` the
                ``HERMES_DATA_DIR`` environment variable is used.

        Returns:
            A configured :class:`BinanceHistoricalAdapter`.
        """
        return cls(config=BinanceHistoricalConfig(data_dir=data_dir))

    def _get_data_dir(self) -> Path:
        """Return the resolved data directory, caching after first call."""
        if self._data_dir is None:
            self._data_dir = self.config.resolve_data_dir()
        return self._data_dir

    # ------------------------------------------------------------------
    # MarketDataPort — bar stream
    # ------------------------------------------------------------------

    async def stream_bars(self, pair: Pair, interval: KlineInterval) -> AsyncIterator[Bar]:
        """Yield closed bars for ``pair`` at ``interval`` from local archives.

        Reads Binance Vision klines ZIP archives in chronological order and
        yields one :class:`~hermes.domain.market_data.bar.Bar` per CSV row.

        Args:
            pair: Trading pair.
            interval: Candle-duration bucket (``1m``, ``1h``, ``4h``, ``1d``).

        Yields:
            :class:`~hermes.domain.market_data.bar.Bar` instances in ascending
            ``open_time`` order.

        Raises:
            hermes.domain.errors.InfraError: On missing directory, corrupt ZIP,
                malformed CSV row, or domain-invariant violation.
        """
        data_dir = self._get_data_dir()
        pair_str = _pair_to_binance_symbol(pair)
        klines_dir = data_dir / "klines" / pair_str / interval.value

        if not klines_dir.exists():
            raise InfraError(f"BinanceHistoricalAdapter: klines directory not found: {klines_dir}")

        for zip_path in _sorted_zip_files(klines_dir):
            _log.debug("stream_bars: reading", path=str(zip_path))
            try:
                rows = _read_csv_from_zip(zip_path)
            except InfraError:
                raise
            except Exception as exc:
                raise InfraError(
                    f"BinanceHistoricalAdapter: failed to read {zip_path}: {exc}"
                ) from exc

            for row in rows:
                bar = _row_to_bar(row=row, pair=pair, interval=interval, source=zip_path)
                yield bar

    # ------------------------------------------------------------------
    # MarketDataPort — trade stream
    # ------------------------------------------------------------------

    async def stream_trades(self, pair: Pair) -> AsyncIterator[Trade]:
        """Yield individual trade ticks for ``pair`` from local aggTrades archives.

        Args:
            pair: Trading pair.

        Yields:
            :class:`~hermes.domain.market_data.trade.Trade` instances in
            ascending ``trade_time`` order.

        Raises:
            hermes.domain.errors.InfraError: On missing directory, corrupt ZIP,
                or malformed CSV row.
        """
        data_dir = self._get_data_dir()
        pair_str = _pair_to_binance_symbol(pair)
        trades_dir = data_dir / "agg_trades" / pair_str

        if not trades_dir.exists():
            raise InfraError(
                f"BinanceHistoricalAdapter: agg_trades directory not found: {trades_dir}"
            )

        for zip_path in _sorted_zip_files(trades_dir):
            _log.debug("stream_trades: reading", path=str(zip_path))
            try:
                rows = _read_csv_from_zip(zip_path)
            except InfraError:
                raise
            except Exception as exc:
                raise InfraError(
                    f"BinanceHistoricalAdapter: failed to read {zip_path}: {exc}"
                ) from exc

            for row in rows:
                trade = _row_to_trade(row=row, pair=pair, source=zip_path)
                yield trade

    # ------------------------------------------------------------------
    # MarketDataPort — L2 methods (not available for historical data)
    # ------------------------------------------------------------------

    async def stream_orderbook_diffs(self, pair: Pair) -> AsyncIterator[OrderbookDiff]:
        """Not supported for historical data.

        Raises:
            NotImplementedError: Always. Historical archives carry no L2 depth.
        """
        raise NotImplementedError(
            "BinanceHistoricalAdapter does not support stream_orderbook_diffs — "
            "historical archives contain no L2 depth data. "
            "Use BinanceLiveWsAdapter for shadow-phase L2 streaming."
        )

    async def get_snapshot(self, pair: Pair) -> Orderbook:
        """Not supported for historical data.

        Raises:
            NotImplementedError: Always. Historical archives carry no L2 depth.
        """
        raise NotImplementedError(
            "BinanceHistoricalAdapter does not support get_snapshot — "
            "historical archives contain no L2 depth data."
        )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _pair_to_binance_symbol(pair: Pair) -> str:
    """Convert a :class:`Pair` to a Binance Vision directory name.

    Args:
        pair: Trading pair.

    Returns:
        Uppercase concatenated symbol, e.g. ``"BTCUSDT"``.
    """
    return f"{pair.base.value}{pair.quote.value}"


def _sorted_zip_files(directory: Path) -> list[Path]:
    """Return all ``.zip`` files under ``directory`` in sorted order.

    Binance Vision organises archives as ``<yyyy>/<mm>/<filename>.zip``.
    Sorting lexicographically gives chronological order.

    Args:
        directory: Root to search.

    Returns:
        Sorted list of ``.zip`` :class:`~pathlib.Path` objects.
    """
    return sorted(directory.rglob("*.zip"))


def _read_csv_from_zip(zip_path: Path) -> list[list[str]]:
    """Open a Binance Vision ZIP and parse the contained CSV.

    Binance Vision archives contain exactly one CSV file. The first line
    MAY be a header row (some months ship with headers, others without).
    The heuristic: if the first field of the first row is non-numeric
    (cannot be parsed as int/Decimal), skip it as a header.

    Args:
        zip_path: Path to the ``.zip`` archive.

    Returns:
        List of parsed CSV rows (each row is a list of string fields).
        Header row (if present) is excluded.

    Raises:
        hermes.domain.errors.InfraError: On corrupt or empty ZIP.
    """
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            csv_names = [n for n in zf.namelist() if n.endswith(".csv")]
            if not csv_names:
                raise InfraError(f"BinanceHistoricalAdapter: no CSV found in {zip_path}")
            with zf.open(csv_names[0]) as raw:
                text = io.TextIOWrapper(raw, encoding="utf-8", newline="")
                reader = csv.reader(text)
                rows: list[list[str]] = []
                first = True
                for row in reader:
                    if first:
                        first = False
                        # Skip header row: first field should be a numeric
                        # timestamp (ms). If it isn't, treat as header.
                        if row and not _is_numeric(row[0]):
                            continue
                    rows.append(row)
                return rows
    except zipfile.BadZipFile as exc:
        raise InfraError(f"BinanceHistoricalAdapter: corrupt ZIP at {zip_path}: {exc}") from exc


def _is_numeric(s: str) -> bool:
    """Return True if ``s`` can be parsed as an integer.

    Args:
        s: String to test.

    Returns:
        ``True`` when ``s`` is a valid integer string, ``False`` otherwise.
    """
    try:
        int(s)
        return True
    except ValueError:
        return False


def _from_epoch_ms(ms_str: str, *, field_name: str, source: Path) -> datetime:
    """Parse an epoch timestamp string into a UTC-aware datetime.

    Handles both Binance archive formats:

    - Pre-2025-01: 13-digit millisecond timestamps.
    - Post-2025-01: 16-digit microsecond timestamps (see #271).

    Detection is by digit-count of the trimmed input. Anything shorter
    than 13 or longer than 16 digits raises `ExchangeError` — a defense
    against parser drift.

    Args:
        ms_str: Raw string field from the CSV.
        field_name: Descriptive name used in error messages.
        source: ZIP file path — included in error messages for traceability.

    Returns:
        UTC-aware :class:`~datetime.datetime`.

    Raises:
        hermes.domain.errors.ExchangeError: On non-integer input or a
            digit-count outside the {13, 16} allowlist.
    """
    stripped = ms_str.strip()
    try:
        raw = int(stripped)
    except ValueError as exc:
        raise ExchangeError(
            f"BinanceHistoricalAdapter: {field_name} is not an integer ({ms_str!r}) in {source}"
        ) from exc
    n_digits = len(stripped.lstrip("-"))
    if n_digits == _EPOCH_MS_DIGITS:
        seconds = raw / _MS_PER_SECOND
    elif n_digits == _EPOCH_US_DIGITS:
        seconds = raw / _US_PER_SECOND
    else:
        raise ExchangeError(
            f"BinanceHistoricalAdapter: {field_name} has unexpected timestamp "
            f"digit-count {n_digits} ({stripped!r}) in {source} — expected 13 (ms) or 16 (µs)"
        )
    return datetime.fromtimestamp(seconds, tz=UTC)


def _decimal_field(raw: str, *, field_name: str, source: Path) -> Decimal:
    """Coerce a raw CSV string field to :class:`~decimal.Decimal`.

    Args:
        raw: Raw string from the CSV.
        field_name: Descriptive name for error messages.
        source: ZIP file path for traceability.

    Returns:
        The value as :class:`~decimal.Decimal`.

    Raises:
        hermes.domain.errors.ExchangeError: On unparseable input.
    """
    try:
        return Decimal(raw)
    except InvalidOperation as exc:
        raise ExchangeError(
            f"BinanceHistoricalAdapter: {field_name} is not a decimal ({raw!r}) in {source}"
        ) from exc


def _row_to_bar(
    *,
    row: list[str],
    pair: Pair,
    interval: KlineInterval,
    source: Path,
) -> Bar:
    """Convert one klines CSV row to a :class:`~hermes.domain.market_data.bar.Bar`.

    Binance Vision klines columns (0-indexed):

    0. open_time (ms)
    1. open
    2. high
    3. low
    4. close
    5. volume
    6. close_time (ms)
    7+ other fields (ignored)

    Args:
        row: CSV row as list of strings.
        pair: Trading pair.
        interval: Candle-duration bucket.
        source: Source ZIP path for error attribution.

    Returns:
        A validated :class:`~hermes.domain.market_data.bar.Bar`.

    Raises:
        hermes.domain.errors.ExchangeError: On missing fields or unparseable values.
    """
    if len(row) < _KLINES_MIN_COLS:
        raise ExchangeError(
            f"BinanceHistoricalAdapter: klines row has {len(row)} fields "
            f"(expected ≥{_KLINES_MIN_COLS}) in {source}: {row!r}"
        )

    open_time = _from_epoch_ms(row[0], field_name="open_time", source=source)
    open_price = _decimal_field(row[1], field_name="open", source=source)
    high_price = _decimal_field(row[2], field_name="high", source=source)
    low_price = _decimal_field(row[3], field_name="low", source=source)
    close_price = _decimal_field(row[4], field_name="close", source=source)
    volume = _decimal_field(row[5], field_name="volume", source=source)
    close_time = _from_epoch_ms(row[6], field_name="close_time", source=source)

    try:
        return Bar(
            pair=pair,
            interval=interval,
            open_time=open_time,
            open=open_price,
            high=high_price,
            low=low_price,
            close=close_price,
            volume=volume,
            close_time=close_time,
        )
    except ValueError as exc:
        raise ExchangeError(
            f"BinanceHistoricalAdapter: klines row fails domain invariants in {source}: {exc}"
        ) from exc


def _row_to_trade(
    *,
    row: list[str],
    pair: Pair,
    source: Path,
) -> Trade:
    """Convert one aggTrades CSV row to a :class:`~hermes.domain.market_data.trade.Trade`.

    Binance Vision aggTrades columns (0-indexed):

    0. agg_trade_id
    1. price
    2. qty
    3. first_trade_id  (ignored)
    4. last_trade_id   (ignored)
    5. transact_time (ms)
    6. is_buyer_maker  ("True" / "False" — inverted vs TradeSide.BUY)

    Args:
        row: CSV row as list of strings.
        pair: Trading pair.
        source: Source ZIP path for error attribution.

    Returns:
        A validated :class:`~hermes.domain.market_data.trade.Trade`.

    Raises:
        hermes.domain.errors.ExchangeError: On missing fields or unparseable values.
    """
    if len(row) < _AGG_TRADES_MIN_COLS:
        raise ExchangeError(
            f"BinanceHistoricalAdapter: aggTrades row has {len(row)} fields "
            f"(expected ≥{_AGG_TRADES_MIN_COLS}) in {source}: {row!r}"
        )

    trade_id = row[0].strip()
    price = _decimal_field(row[1], field_name="price", source=source)
    quantity = _decimal_field(row[2], field_name="qty", source=source)
    trade_time = _from_epoch_ms(row[5], field_name="transact_time", source=source)

    # Binance aggTrades: is_buyer_maker=True means the buyer was the maker,
    # i.e. the taker is the SELLER. So TradeSide.SELL when is_buyer_maker is True.
    is_buyer_maker_str = row[6].strip().lower()
    if is_buyer_maker_str in ("true", "1"):
        side = TradeSide.SELL
    elif is_buyer_maker_str in ("false", "0"):
        side = TradeSide.BUY
    else:
        raise ExchangeError(
            f"BinanceHistoricalAdapter: is_buyer_maker has unexpected value "
            f"({row[6]!r}) in {source}"
        )

    try:
        return Trade(
            pair=pair,
            price=price,
            quantity=quantity,
            side=side,
            trade_time=trade_time,
            trade_id=trade_id,
        )
    except ValueError as exc:
        raise ExchangeError(
            f"BinanceHistoricalAdapter: aggTrades row fails domain invariants in {source}: {exc}"
        ) from exc
