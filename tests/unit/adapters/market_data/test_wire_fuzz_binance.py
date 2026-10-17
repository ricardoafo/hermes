"""Fuzz properties for every Binance wire parser (issue #842).

For any input, each parser returns a valid value or raises ``ExchangeError``,
nothing else; and any value it accepts satisfies the domain (finite numbers,
OHLC ordered, bar times on the interval grid). Seeds copy the shape of the
recorded payloads in ``tests/fixtures/binance_public`` and
``tests/fixtures/binance_vision`` with synthetic values. Non-finite numbers and
out-of-range times are pinned one by one in ``tests/unit/adapters/test_wire_numbers.py``;
these properties add broken structure: wrong types, missing and extra keys,
empty lists, unicode.

No network: parsers are called directly, and the HTTP adapters answer from an
``httpx.MockTransport``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import anyio
import httpx
from hypothesis import example, given
from hypothesis import strategies as st

from hermes.adapters.clock import SimulatedClock
from hermes.adapters.exchanges import binance_klines
from hermes.adapters.exchanges.rate_limiter import RateLimitConfig, RateLimiter
from hermes.adapters.market_data import (
    binance_historical,
    binance_live_klines,
    binance_live_ws,
    binance_ticker,
)
from hermes.adapters.market_data.binance_depth_snapshot import BinanceDepthSnapshotAdapter
from hermes.domain.errors import ExchangeError
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair
from hermes.infrastructure.config.models import MarketDataConfig
from tests.unit.adapters.wire_fuzz import (
    FUZZ,
    accepted,
    bar_violations,
    clean_violations,
    foreign_error,
    kline_violations,
    mutated,
    mutated_text,
    numeric_strings,
    price_violations,
)

_PAIR: Final = Pair(base=Currency.BTC, quote=Currency.USDT)
_H4: Final = KlineInterval.H4
_OPEN_MS: Final = 1_735_689_600_000  # 2025-01-01 00:00 UTC, a 4h boundary
_CLOSE_MS: Final = _OPEN_MS + 4 * 3_600_000 - 1
_SOURCE: Final = "wss://fuzz.invalid/ws"
_VISION_ZIP: Final = Path("BTCUSDT-4h-2025-01-01.zip")

_KLINE_ROW: Final = [
    _OPEN_MS,
    "100.00000000",
    "110.00000000",
    "90.00000000",
    "105.00000000",
    "12.50000000",
    _CLOSE_MS,
    "1312.50000000",
    42,
    "6.00000000",
    "630.00000000",
    "0",
]
_VISION_ROW: Final = [str(field) for field in _KLINE_ROW]
_DEPTH_UPDATE: Final = {
    "e": "depthUpdate",
    "E": _OPEN_MS,
    "s": "BTCUSDT",
    "U": 157,
    "u": 160,
    "b": [["100.00", "0.5"], ["99.00", "0"]],
    "a": [["101.00", "0.3"]],
}
_DEPTH_SNAPSHOT: Final = {
    "lastUpdateId": 1027024,
    "bids": [["100.00000000", "0.50000000"], ["99.00000000", "1.00000000"]],
    "asks": [["101.00000000", "0.30000000"], ["102.00000000", "2.00000000"]],
}
_TICKER: Final = {"symbol": "BTCUSDT", "price": "100.00000000"}

_numeric_fields: Final = st.one_of(numeric_strings, st.integers(-(2**40), 2**40))
"""Values that keep a field's type plausible, so the domain checks are reached."""
_epoch_strings: Final = st.integers(10**12, 10**16).map(str)
"""Archive times of the 13 (ms) and 16 (us) digit lengths the parser reads."""


def _live_row(row: object) -> object:
    return binance_live_klines._row_to_bar(row, _PAIR, _H4)


def _hist_row(row: object) -> object:
    return binance_klines._row_to_kline(row=row, pair=_PAIR, interval=_H4)


def _as_csv_row(row: object) -> list[str]:
    """Flatten a mutated row back to what ``csv.reader`` yields: a list of strings."""
    return [str(cell) for cell in row] if isinstance(row, list) else [str(row)]


def _vision_row(row: object) -> object:
    return binance_historical._row_to_bar(
        row=_as_csv_row(row), pair=_PAIR, interval=_H4, source=_VISION_ZIP
    )


def _vision_trade(row: object) -> object:
    return binance_historical._row_to_trade(row=_as_csv_row(row), pair=_PAIR, source=_VISION_ZIP)


def _ws_depth(raw: str | bytes) -> object:
    return binance_live_ws._parse_depth_update(raw=raw, pair=_PAIR, source=_SOURCE)


def _ws_snapshot(body: object) -> object:
    return binance_live_ws._parse_snapshot(data=body, pair=_PAIR)  # type: ignore[arg-type]  # fuzzing the declared shape


def _ticker(body: bytes) -> object:
    return binance_ticker._parse_price(httpx.Response(200, content=body), "BTCUSDT")


def _rest_snapshot(body: bytes) -> object:
    transport = httpx.MockTransport(lambda _request: httpx.Response(200, content=body))
    adapter = BinanceDepthSnapshotAdapter(
        config=MarketDataConfig(),
        clock=SimulatedClock(start=datetime(2026, 10, 4, 4, 0, 5, tzinfo=UTC)),
        client=httpx.AsyncClient(transport=transport),
        limiter=RateLimiter.for_testing(
            RateLimitConfig(max_weight=600, window_seconds=60),
            clock=lambda: 0.0,
            sleep=lambda _s: None,
        ),
    )

    async def _go() -> object:
        try:
            return await adapter.snapshot(_PAIR)
        finally:
            await adapter.aclose()

    return anyio.run(_go)


def _json_bytes(payload: object) -> bytes:
    return json.dumps(payload).encode("utf-8")


# --- live klines (serve) -----------------------------------------------------


@FUZZ
@given(row=mutated(_KLINE_ROW))
def test_a_live_kline_row_parses_or_raises_exchange_error(row: object) -> None:
    """Invariant: any klines row gives a Bar or ExchangeError, never a raw exception."""
    assert foreign_error(lambda: _live_row(row), ExchangeError) == ""


@FUZZ
@given(rows=mutated([_KLINE_ROW, _KLINE_ROW]))
def test_a_live_klines_page_parses_or_raises_exchange_error(rows: object) -> None:
    """Invariant: any page of rows (empty, duplicated, nested) gives bars or ExchangeError."""
    page = rows if isinstance(rows, list) else [rows]
    assert (
        foreign_error(
            lambda: binance_live_klines._parse_rows(page, _PAIR, _H4, keep=lambda _: True),
            ExchangeError,
        )
        == ""
    )


@FUZZ
@given(row=mutated(_KLINE_ROW, values=_numeric_fields))
def test_an_accepted_live_kline_row_is_a_valid_bar(row: object) -> None:
    """Invariant: a live bar the parser accepts is finite, OHLC ordered and on the 4h grid."""
    assert bar_violations(accepted(lambda: _live_row(row), ExchangeError)) == []


# --- historical klines (REST backfill) ---------------------------------------


@FUZZ
@given(row=mutated(_KLINE_ROW))
def test_a_historical_kline_row_parses_or_raises_exchange_error(row: object) -> None:
    """Invariant: any REST klines row gives a Kline or ExchangeError, never a raw exception."""
    assert foreign_error(lambda: _hist_row(row), ExchangeError) == ""


@FUZZ
@given(row=mutated(_KLINE_ROW, values=_numeric_fields))
@example(row=[1, *_KLINE_ROW[1:]])
@example(row=[_OPEN_MS, "-5", "-1", "-10", "-3", *_KLINE_ROW[5:]])
def test_an_accepted_historical_kline_row_is_a_valid_kline(row: object) -> None:
    """Invariant: an accepted historical Kline is finite, positive, ordered and on the grid."""
    assert kline_violations(accepted(lambda: _hist_row(row), ExchangeError)) == []


# --- Binance Vision archives --------------------------------------------------


@FUZZ
@given(row=mutated(_VISION_ROW))
def test_a_vision_kline_csv_row_parses_or_raises_exchange_error(row: object) -> None:
    """Invariant: any archive klines row gives a Bar or ExchangeError, never a raw exception."""
    assert foreign_error(lambda: _vision_row(row), ExchangeError) == ""


@FUZZ
@given(row=mutated(_VISION_ROW, values=st.one_of(numeric_strings, _epoch_strings)))
@example(row=["1000000000000", *_VISION_ROW[1:]])
def test_an_accepted_vision_kline_csv_row_is_a_valid_bar(row: object) -> None:
    """Invariant: an archive bar the parser accepts is finite, OHLC ordered and on the 4h grid."""
    assert bar_violations(accepted(lambda: _vision_row(row), ExchangeError)) == []


@FUZZ
@given(row=mutated([str(_OPEN_MS), "100.5", "0.25", "1", "2", str(_OPEN_MS), "True"]))
def test_a_vision_agg_trade_csv_row_parses_or_raises_exchange_error(row: object) -> None:
    """Invariant: any archive aggTrades row gives a Trade or ExchangeError, never a raw error."""
    assert foreign_error(lambda: _vision_trade(row), ExchangeError) == ""


# --- depth stream (serve) -----------------------------------------------------


@FUZZ
@given(raw=mutated_text(_DEPTH_UPDATE))
@example(raw='{"e": "depthUpdate", "U": 1, "u": 2, "b": [[null, "1"]], "a": []}')
@example(raw="[]")
@example(raw="[" * 5000)
def test_a_depth_stream_message_parses_or_raises_exchange_error(raw: str | bytes) -> None:
    """Invariant: any depth-stream message gives an OrderbookDiff or ExchangeError."""
    assert foreign_error(lambda: _ws_depth(raw), ExchangeError) == ""


@FUZZ
@given(update=mutated(_DEPTH_UPDATE, values=_numeric_fields))
def test_an_accepted_depth_stream_message_holds_only_finite_numbers(update: object) -> None:
    """Invariant: every price and quantity in an accepted depth diff is finite."""
    assert clean_violations(accepted(lambda: _ws_depth(json.dumps(update)), ExchangeError)) == []


# --- depth snapshot -----------------------------------------------------------


@FUZZ
@given(body=mutated(_DEPTH_SNAPSHOT))
def test_a_websocket_adapter_depth_snapshot_parses_or_raises_exchange_error(body: object) -> None:
    """Invariant: any REST depth body gives an Orderbook or ExchangeError (websocket adapter)."""
    assert foreign_error(lambda: _ws_snapshot(body), ExchangeError) == ""


@FUZZ
@given(body=mutated(_DEPTH_SNAPSHOT))
def test_a_depth_snapshot_body_parses_or_raises_exchange_error(body: object) -> None:
    """Invariant: any depth body served to the snapshot adapter gives a book or ExchangeError."""
    assert foreign_error(lambda: _rest_snapshot(_json_bytes(body)), ExchangeError) == ""


@FUZZ
@given(body=mutated(_DEPTH_SNAPSHOT, values=_numeric_fields))
def test_an_accepted_depth_snapshot_holds_only_finite_numbers(body: object) -> None:
    """Invariant: every level of a book the snapshot adapter accepts is finite."""
    assert (
        clean_violations(accepted(lambda: _rest_snapshot(_json_bytes(body)), ExchangeError)) == []
    )


# --- ticker -------------------------------------------------------------------


@FUZZ
@given(body=mutated_text(_TICKER))
def test_a_ticker_body_parses_or_raises_exchange_error(body: str | bytes) -> None:
    """Invariant: any ticker body gives a price or ExchangeError, never a raw exception."""
    raw = body.encode("utf-8") if isinstance(body, str) else body
    assert foreign_error(lambda: _ticker(raw), ExchangeError) == ""


@FUZZ
@given(body=mutated(_TICKER, values=_numeric_fields))
def test_an_accepted_ticker_price_is_finite_and_positive(body: object) -> None:
    """Invariant: a ticker price the parser accepts is a finite number above zero."""
    assert price_violations(accepted(lambda: _ticker(_json_bytes(body)), ExchangeError)) == []
