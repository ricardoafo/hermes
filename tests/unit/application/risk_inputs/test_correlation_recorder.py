"""Unit tests for the per-1D-bar pair correlation recorder (#835).

The bar store and the risk input store are one real in-memory ledger.

Invariants under test:

- When the last pair's 1D bar of a day is stored, every two pairs get one
  correlation of their daily returns over the 30-day window, keyed on that
  bar's close time, with the pair names sorted.
- Before every pair has that day's bar, nothing is recorded.
- A 4H bar, a single followed pair or a replayed day records nothing new.
- Fewer stored days give fewer returns, and the row says how many.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from hermes.adapters.ledger.sqlite_ledger import SqliteLedger
from hermes.application.risk_inputs.correlation_recorder import CorrelationRecorder
from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.bar_schedule import close_time_of
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.portfolio.correlation import aligned_daily_returns, pearson
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair

_BTC = Pair(base=Currency.BTC, quote=Currency.USDT)
_ETH = Pair(base=Currency.ETH, quote=Currency.USDT)
_SOL = Pair(base=Currency.SOL, quote=Currency.USDT)
_DAY0 = datetime(2026, 8, 1, tzinfo=UTC)
_DAY = timedelta(days=1)
_ALL_TIME = (datetime(2000, 1, 1, tzinfo=UTC), datetime(2100, 1, 1, tzinfo=UTC))


def _close(pair: Pair, day: int) -> Decimal:
    """Return a wobbling close per pair, so returns vary and differ between pairs."""
    wobble = {_BTC: (3, 5), _ETH: (2, 7), _SOL: (5, 3)}[pair]
    return Decimal(1000 + 10 * day + wobble[0] * ((day * wobble[1]) % 4))


def _bar(pair: Pair, day: int, interval: KlineInterval = KlineInterval.D1) -> Bar:
    open_time = _DAY0 + _DAY * day
    price = _close(pair, day)
    return Bar(
        pair=pair,
        interval=interval,
        open_time=open_time,
        open=price,
        high=price + 1,
        low=price - 1,
        close=price,
        volume=Decimal(10),
        close_time=close_time_of(open_time, interval),
    )


@pytest.fixture
def ledger() -> Iterator[SqliteLedger]:
    """Yield a migrated in-memory ledger."""
    conn = sqlite3.connect(":memory:")
    try:
        yield SqliteLedger(conn)
    finally:
        conn.close()


def _store_days(ledger: SqliteLedger, pairs: tuple[Pair, ...], days: range) -> None:
    for day in days:
        for pair in pairs:
            ledger.record_live_bar(_bar(pair, day))


def _recorder(ledger: SqliteLedger, pairs: tuple[Pair, ...]) -> CorrelationRecorder:
    return CorrelationRecorder(bars=ledger, store=ledger, pairs=pairs)


def test_last_pair_of_the_day_records_every_two_pairs(ledger: SqliteLedger) -> None:
    """Three pairs with 40 stored days give three 30-return values at the bar's close."""
    pairs = (_SOL, _BTC, _ETH)
    _store_days(ledger, pairs, range(40))
    assert _recorder(ledger, pairs).record_for(_bar(_SOL, 39)) == 3
    got = ledger.pair_correlations(*_ALL_TIME)
    assert [(str(c.pair_a), str(c.pair_b)) for c in got] == [
        ("BTC/USDT", "ETH/USDT"),
        ("BTC/USDT", "SOL/USDT"),
        ("ETH/USDT", "SOL/USDT"),
    ]
    assert {(c.as_of, c.window_days, c.sample_count) for c in got} == {
        (_bar(_SOL, 39).close_time, 30, 30)
    }


def test_value_is_pearson_of_the_window_returns(ledger: SqliteLedger) -> None:
    """The stored value equals the domain math on days 9..39 (31 closes)."""
    _store_days(ledger, (_BTC, _ETH), range(40))
    _recorder(ledger, (_BTC, _ETH)).record_for(_bar(_ETH, 39))
    (stored,) = ledger.pair_correlations(*_ALL_TIME)
    window = range(9, 40)
    a, b = aligned_daily_returns(
        {_DAY0 + _DAY * d: _close(_BTC, d) for d in window},
        {_DAY0 + _DAY * d: _close(_ETH, d) for d in window},
    )
    assert stored.correlation == pytest.approx(pearson(a, b), abs=1e-12)


def test_nothing_is_recorded_until_every_pair_has_the_day(ledger: SqliteLedger) -> None:
    """BTC's bar arrives first: ETH lacks the day, so the call records nothing."""
    _store_days(ledger, (_BTC, _ETH), range(10))
    ledger.record_live_bar(_bar(_BTC, 10))
    assert _recorder(ledger, (_BTC, _ETH)).record_for(_bar(_BTC, 10)) == 0
    assert ledger.pair_correlations(*_ALL_TIME) == ()


def test_short_history_records_its_return_count(ledger: SqliteLedger) -> None:
    """Ten stored days give nine returns, and the row says nine."""
    _store_days(ledger, (_BTC, _ETH), range(10))
    _recorder(ledger, (_BTC, _ETH)).record_for(_bar(_ETH, 9))
    (stored,) = ledger.pair_correlations(*_ALL_TIME)
    assert stored.sample_count == 9


def test_replayed_day_records_nothing_new(ledger: SqliteLedger) -> None:
    """A restart that replays the 1D bar keeps one row."""
    _store_days(ledger, (_BTC, _ETH), range(10))
    recorder = _recorder(ledger, (_BTC, _ETH))
    assert recorder.record_for(_bar(_ETH, 9)) == 1
    assert recorder.record_for(_bar(_ETH, 9)) == 0
    assert len(ledger.pair_correlations(*_ALL_TIME)) == 1


@pytest.mark.parametrize(
    ("pairs", "bar"),
    [
        ((_BTC, _ETH), _bar(_ETH, 9, KlineInterval.H4)),
        ((_BTC,), _bar(_BTC, 9)),
    ],
)
def test_four_hour_bar_or_single_pair_records_nothing(
    ledger: SqliteLedger, pairs: tuple[Pair, ...], bar: Bar
) -> None:
    """Correlations come from 1D bars and need two pairs."""
    _store_days(ledger, (_BTC, _ETH), range(10))
    assert _recorder(ledger, pairs).record_for(bar) == 0
    assert ledger.pair_correlations(*_ALL_TIME) == ()


def test_window_must_be_positive(ledger: SqliteLedger) -> None:
    """A zero window has no returns to correlate."""
    with pytest.raises(ValueError, match="window_days"):
        CorrelationRecorder(bars=ledger, store=ledger, pairs=(_BTC, _ETH), window_days=0)
