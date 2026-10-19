"""Fuzz properties for every Kraken wire parser (issue #842).

For any reply body, ``fetch_klines`` (OHLC), ``get_orderbook`` (Depth),
and ``get_ticker`` (Ticker) return a valid value
or raise ``KrakenPublicError``, and the spot adapter's ``get_fees``
(TradeVolume) returns a schedule or raises ``ExchangeError``; nothing else.
Accepted values satisfy the domain: finite, positive prices, OHLC ordered,
candles on the interval grid, non-negative whole-bps fees.

Seeds copy the shape of ``tests/fixtures/kraken_spot`` and the recorded
public replies with synthetic values. No network: each adapter's client
answers from an ``httpx.MockTransport``.
"""

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Final

import httpx
from hypothesis import example, given
from hypothesis import strategies as st

from hermes.adapters._shared.rate_limiter import RateLimitConfig, RateLimiter
from hermes.adapters.exchanges.kraken_public import KrakenPublicAdapter, KrakenPublicConfig
from hermes.adapters.exchanges.kraken_spot import (
    KRAKEN_BASE_URL,
    KrakenSpotAdapter,
    KrakenSpotConfig,
)
from hermes.domain.errors import ExchangeError, KrakenPublicError
from hermes.domain.fees.fee_schedule import FeeSchedule
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair
from tests.unit.adapters.wire_fuzz import (
    FUZZ,
    accepted,
    clean_violations,
    foreign_error,
    kline_violations,
    mutated,
    numeric_strings,
)

_PAIR: Final = Pair(base=Currency.BTC, quote=Currency.USDT)
_SYMBOL: Final = "XBTUSDT"
_H4: Final = KlineInterval.H4
_OPEN_S: Final = 1_735_689_600  # 2025-01-01 00:00 UTC, a 4h boundary
_SINCE: Final = datetime(2025, 1, 1, tzinfo=UTC)
_KEY: Final = "fuzz-key-not-real"  # gitleaks:allow
_SIGNING_MATERIAL: Final = "ZnV6ei1zZWNyZXQtbm90LXJlYWw="  # gitleaks:allow

_OHLC: Final = {
    "error": [],
    "result": {
        _SYMBOL: [
            [_OPEN_S, "100.0", "110.0", "90.0", "105.0", "102.0", "5.0", 10],
            [_OPEN_S + 14_400, "105.0", "112.0", "101.0", "108.0", "106.0", "4.0", 8],
        ],
        "last": _OPEN_S + 14_400,
    },
}
_DEPTH: Final = {
    "error": [],
    "result": {
        _SYMBOL: {
            "bids": [["100.0", "1.5", _OPEN_S], ["99.0", "2.0", _OPEN_S]],
            "asks": [["101.0", "1.2", _OPEN_S], ["102.0", "3.0", _OPEN_S]],
        }
    },
}
_TICKER: Final = {
    "error": [],
    "result": {
        _SYMBOL: {
            "a": ["101.0", "1", "1.000"],
            "b": ["100.0", "1", "1.000"],
            "c": ["100.5", "0.5"],
            "v": ["10.0", "20.0"],
        }
    },
}
_TRADE_VOLUME: Final = {
    "error": [],
    "result": {
        "currency": "ZUSD",
        "volume": "1250.0000",
        "fees": {_SYMBOL: {"fee": "0.4000", "minfee": "0.0800", "maxfee": "0.4000"}},
        "fees_maker": {_SYMBOL: {"fee": "0.2500", "minfee": "0.0000", "maxfee": "0.2500"}},
    },
}

_numeric_fields: Final = st.one_of(numeric_strings, st.integers(-(2**40), 2**40))
"""Values that keep a field's type plausible, so the domain checks are reached."""


def _with_row(row: list[object]) -> dict[str, object]:
    """Return the OHLC seed with its first candle replaced by ``row``."""
    return {"error": [], "result": {_SYMBOL: [row], "last": _OPEN_S}}


def _with_taker_fee(fee: str) -> dict[str, object]:
    """Return the TradeVolume seed with the taker fee replaced by ``fee``."""
    fees = {"fees": {_SYMBOL: {"fee": fee}}, "fees_maker": {_SYMBOL: {"fee": "0.2500"}}}
    return {"error": [], "result": {"currency": "ZUSD", **fees}}


def _answering(body: object) -> httpx.Client:
    content = json.dumps(body).encode("utf-8")
    transport = httpx.MockTransport(lambda _request: httpx.Response(200, content=content))
    return httpx.Client(base_url=KRAKEN_BASE_URL, transport=transport)


def _public(body: object) -> KrakenPublicAdapter:
    config = KrakenPublicConfig(rate_limit=RateLimitConfig(max_weight=1000, window_seconds=60))
    limiter = RateLimiter.for_testing(config.rate_limit, clock=lambda: 0.0, sleep=lambda _s: None)
    return KrakenPublicAdapter(config=config, client=_answering(body), limiter=limiter)


def _klines(body: object) -> object:
    window = _public(body).fetch_klines(
        pair=_PAIR, interval=_H4, since=_SINCE, until=_SINCE + timedelta(days=1)
    )
    return list(window)


def _spot_fees(body: object) -> FeeSchedule:
    roomy = RateLimitConfig(max_weight=1000, window_seconds=60)
    adapter = KrakenSpotAdapter(
        config=KrakenSpotConfig(api_key=_KEY, api_secret=_SIGNING_MATERIAL),
        client=_answering(body),
        limiter=RateLimiter.for_testing(roomy, clock=lambda: 0.0, sleep=lambda _s: None),
        public=_public({}),
        clock_ms=lambda: 1_790_000_000_000,
        sleep=lambda _s: None,
    )
    return adapter.get_fees(pair=_PAIR)


def _fee_violations(value: object) -> list[str]:
    if not isinstance(value, FeeSchedule):
        return clean_violations(value)
    return clean_violations(value) + [
        f"negative fee in {tier!r}"
        for tier in value.tiers
        if min(tier.maker_bps, tier.taker_bps) < 0
    ]


def _each_kline(value: object) -> Iterator[str]:
    for kline in value if isinstance(value, list) else []:
        yield from kline_violations(kline)


# --- OHLC ---------------------------------------------------------------------


@FUZZ
@given(body=mutated(_OHLC))
def test_an_ohlc_reply_parses_or_raises_kraken_public_error(body: object) -> None:
    """Invariant: any OHLC reply yields candles or KrakenPublicError, never a raw exception."""
    assert foreign_error(lambda: _klines(body), KrakenPublicError) == ""


@FUZZ
@given(body=mutated(_OHLC, values=_numeric_fields))
@example(body=_with_row([_OPEN_S, "100.0", "110.0", "0", "105.0", "102.0", "5.0", 10]))
@example(body=_with_row([_OPEN_S + 1, "100.0", "110.0", "90.0", "105.0", "102.0", "5.0", 10]))
def test_accepted_ohlc_candles_are_valid_klines(body: object) -> None:
    """Invariant: every accepted Kraken candle is finite, positive, ordered, on the 4h grid."""
    assert list(_each_kline(accepted(lambda: _klines(body), KrakenPublicError))) == []


# --- Depth --------------------------------------------------------------------


@FUZZ
@given(body=mutated(_DEPTH))
def test_a_depth_reply_parses_or_raises_kraken_public_error(body: object) -> None:
    """Invariant: any Depth reply gives an Orderbook or KrakenPublicError."""
    assert foreign_error(lambda: _public(body).get_orderbook(_PAIR), KrakenPublicError) == ""


@FUZZ
@given(body=mutated(_DEPTH, values=_numeric_fields))
def test_an_accepted_depth_reply_holds_only_finite_numbers(body: object) -> None:
    """Invariant: every price and quantity in an accepted Kraken book is finite."""
    book = accepted(lambda: _public(body).get_orderbook(_PAIR), KrakenPublicError)
    assert clean_violations(book) == []


# --- Ticker -------------------------------------------------------------------


@FUZZ
@given(body=mutated(_TICKER))
def test_a_ticker_reply_parses_or_raises_kraken_public_error(body: object) -> None:
    """Invariant: any Ticker reply gives a Ticker or KrakenPublicError."""
    assert foreign_error(lambda: _public(body).get_ticker(_PAIR), KrakenPublicError) == ""


@FUZZ
@given(body=mutated(_TICKER, values=_numeric_fields))
def test_an_accepted_ticker_reply_holds_only_finite_numbers(body: object) -> None:
    """Invariant: bid, ask and last of an accepted Kraken ticker are finite."""
    ticker = accepted(lambda: _public(body).get_ticker(_PAIR), KrakenPublicError)
    assert clean_violations(ticker) == []


# --- account fees -------------------------------------------------------------


@FUZZ
@given(body=mutated(_TRADE_VOLUME))
def test_a_trade_volume_reply_parses_or_raises_exchange_error(body: object) -> None:
    """Invariant: any TradeVolume reply gives the account's FeeSchedule or ExchangeError."""
    assert foreign_error(lambda: _spot_fees(body), ExchangeError) == ""


@FUZZ
@given(body=mutated(_TRADE_VOLUME, values=_numeric_fields))
@example(body=_with_taker_fee("1E+999999"))
def test_an_accepted_trade_volume_schedule_has_non_negative_fees(body: object) -> None:
    """Invariant: an accepted account fee schedule charges >= 0 bps on both sides."""
    assert _fee_violations(accepted(lambda: _spot_fees(body), ExchangeError)) == []
