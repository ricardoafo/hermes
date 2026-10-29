"""Every parser that reads a price, quantity, rate or time off the wire refuses impossible values.

A JSON ``true`` is refused the same way: ``bool`` is an ``int``, so a parser
that only checked for numbers read it as 1, and two of the old copies did
(issue #1177).

``Decimal("NaN")`` and ``Decimal("Infinity")`` parse without error, so a
parser that only catches ``InvalidOperation`` from the constructor lets them
through: NaN then raises a raw ``decimal.InvalidOperation`` from the first
domain comparison, and Infinity is accepted as a real price. A timestamp
outside the calendar raises ``OverflowError`` or ``ValueError``. Either way
the caller's retry and degraded-feed handling, which catches the adapter's
own error, is bypassed (issue #841, failure-mode catalogue issue #839).
"""

import json
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

from hermes.adapters.exchanges import binance_klines, binance_spot, kraken_public, kraken_spot
from hermes.adapters.fx import frankfurter
from hermes.adapters.market_data import (
    binance_depth_snapshot,
    binance_historical,
    binance_live_klines,
    binance_live_ws,
)
from hermes.domain.errors import ExchangeError, FxRateError, KrakenPublicError
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair

_PAIR = Pair(base=Currency.BTC, quote=Currency.USDT)
_H4 = KlineInterval("4h")
_OPEN_MS = 1_735_689_600_000  # 2025-01-01 00:00 UTC, a 4h boundary
_CLOSE_MS = _OPEN_MS + 4 * 3_600_000 - 1
_NOT_FINITE = ("NaN", "sNaN", "Infinity", "-Infinity")


def _live_kline(value: object) -> object:
    return binance_live_klines._row_to_bar(
        [_OPEN_MS, "100", "110", "90", value, "5", _CLOSE_MS], _PAIR, _H4
    )


def _hist_kline(value: object) -> object:
    return binance_klines._row_to_kline(
        row=[_OPEN_MS, "100", value, "90", "95", "5", _CLOSE_MS], pair=_PAIR, interval=_H4
    )


def _vision_kline(value: object) -> object:
    row = [str(_OPEN_MS), "100", "110", "90", "105", value, str(_CLOSE_MS), "0", "0", "0", "0", "0"]
    # A CSV row only ever holds strings; the cast lets the bool case reach the parser anyway.
    return binance_historical._row_to_bar(
        row=cast("list[str]", row),
        pair=_PAIR,
        interval=_H4,
        source=Path("BTCUSDT-4h-2025-01-01.zip"),
    )


def _ws_depth(value: object) -> object:
    message = {"e": "depthUpdate", "E": 1, "s": "BTCUSDT", "U": 1, "u": 2}
    message |= {"b": [[value, "1"]], "a": []}
    return binance_live_ws._parse_depth_update(raw=json.dumps(message), pair=_PAIR, source="ws")


def _rest_depth(value: object) -> object:
    return binance_depth_snapshot._levels([["100", value]], side="bids")


def _kraken_kline(value: object) -> object:
    row = [_OPEN_MS // 1000, "100", "110", value, "105", "102", "5", 10]
    return kraken_public._row_to_kline(row=row, pair=_PAIR, interval=_H4)


def _kraken_depth(value: object) -> object:
    result = {"XXBTZUSD": {"bids": [[value, "1", 0]], "asks": []}}
    return kraken_public._parse_depth_response(result=result, pair=_PAIR, symbol="XXBTZUSD")


def _frankfurter(value: object) -> object:
    return frankfurter._parse_response(
        payload={"base": "USD", "date": "2025-01-02", "rates": {"EUR": value}},
        from_currency=Currency.USDT,
        to_currency=Currency.EUR,
        base_key="USD",
        rate_key="EUR",
    )


def _kraken_spot(value: object) -> object:
    return kraken_spot._decimal(value, "cost")


def _binance_spot(value: object) -> object:
    return binance_spot.parse_decimal(value, "price")


_PARSERS: dict[str, tuple[Callable[[object], object], type[Exception]]] = {
    "binance live klines (serve)": (_live_kline, ExchangeError),
    "binance historical klines": (_hist_kline, ExchangeError),
    "binance vision klines": (_vision_kline, ExchangeError),
    "binance depth stream (serve)": (_ws_depth, ExchangeError),
    "binance depth snapshot (serve)": (_rest_depth, ExchangeError),
    "kraken klines": (_kraken_kline, KrakenPublicError),
    "kraken depth": (_kraken_depth, KrakenPublicError),
    "frankfurter rate": (_frankfurter, FxRateError),
    "kraken spot fill": (_kraken_spot, ExchangeError),
    "binance spot fill": (_binance_spot, ExchangeError),
}


@pytest.mark.parametrize("value", _NOT_FINITE)
@pytest.mark.parametrize("parser", sorted(_PARSERS))
def test_a_non_finite_number_is_refused_with_the_adapters_own_error(
    parser: str, value: str
) -> None:
    """A NaN or infinite number on the wire raises the adapter's typed error, naming the value."""
    parse, error = _PARSERS[parser]

    with pytest.raises(error, match=r"not a finite number") as raised:
        parse(value)

    assert repr(value) in str(raised.value)


@pytest.mark.parametrize("value", [True, False])
@pytest.mark.parametrize("parser", sorted(_PARSERS))
def test_a_json_bool_is_refused_with_the_adapters_own_error(parser: str, value: bool) -> None:
    """A true or false where a number belongs raises the adapter's typed error, naming the value."""
    parse, error = _PARSERS[parser]

    with pytest.raises(error, match=rf"is not (numeric|a string): {value!r}"):
        parse(value)


def test_a_live_kline_time_past_the_calendar_is_an_exchange_error() -> None:
    """A kline open time beyond year 9999 raises ExchangeError, not OverflowError."""
    huge = 10**17

    with pytest.raises(ExchangeError, match=r"timestamp out of range: 100000000000000000"):
        binance_live_klines._row_to_bar(
            [huge, "100", "110", "90", "105", "5", huge + 1], _PAIR, _H4
        )


@pytest.mark.parametrize("open_time", [float("inf"), float("nan"), -(10**12), 10**15])
def test_a_kraken_kline_time_past_the_calendar_is_a_kraken_error(open_time: float) -> None:
    """A Kraken OHLC time that is infinite, NaN or outside the calendar raises KrakenPublicError."""
    row = [open_time, "100", "110", "90", "105", "102", "5", 10]

    with pytest.raises(KrakenPublicError, match=r"open_time out of range"):
        kraken_public._row_to_kline(row=row, pair=_PAIR, interval=_H4)


def test_a_historical_kline_time_past_the_calendar_is_an_exchange_error() -> None:
    """A Binance historical kline time beyond year 9999 raises ExchangeError."""
    huge = 10**20

    with pytest.raises(ExchangeError, match=r"timestamp out of range"):
        binance_klines._row_to_kline(
            row=[huge, "100", "110", "90", "105", "5", huge + 1], pair=_PAIR, interval=_H4
        )
