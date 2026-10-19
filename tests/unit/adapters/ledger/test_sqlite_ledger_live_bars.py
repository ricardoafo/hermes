"""Unit tests for the append-only live bar store in the SQLite ledger (#421).

Invariants under test:

- A stored bar reads back with its exact values.
- A second write of the same key keeps the first row; a differing value is
  reported, never overwritten.
- UPDATE and DELETE are refused by the schema.
- Range reads are half-open and ordered by open time.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from hermes.adapters.ledger.sqlite_ledger import SqliteLedger
from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair

_BTC = Pair(base=Currency.BTC, quote=Currency.USDT)
_ETH = Pair(base=Currency.ETH, quote=Currency.USDT)
_T0 = datetime(2026, 10, 3, tzinfo=UTC)
_H4 = timedelta(hours=4)


def _bar(i: int, pair: Pair = _BTC, close: str = "61234.56000000") -> Bar:
    open_time = _T0 + _H4 * i
    return Bar(
        pair=pair,
        interval=KlineInterval.H4,
        open_time=open_time,
        close_time=open_time + _H4 - timedelta(milliseconds=1),
        open=Decimal("61000.01"),
        high=Decimal("61500.00"),
        low=Decimal("60900.99"),
        close=Decimal(close),
        volume=Decimal("123.45678900"),
    )


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    """Yield an in-memory connection, closed afterwards."""
    connection = sqlite3.connect(":memory:")
    yield connection
    connection.close()


def test_stored_bar_reads_back_exactly(conn: sqlite3.Connection) -> None:
    """Every OHLCV value and both times survive storage without rounding."""
    ledger = SqliteLedger(conn)
    assert ledger.record_live_bar(_bar(0)) is None
    (got,) = ledger.live_bars(_BTC, KlineInterval.H4, _T0, _T0 + _H4)
    assert got == _bar(0)
    assert str(got.close) == "61234.56000000"


def test_identical_rewrite_keeps_one_row(conn: sqlite3.Connection) -> None:
    """A restart replaying a handled bar does not add a second row."""
    ledger = SqliteLedger(conn)
    ledger.record_live_bar(_bar(0))
    assert ledger.record_live_bar(_bar(0)) is None
    assert conn.execute("SELECT COUNT(*) FROM live_bars").fetchone() == (1,)


def test_differing_rewrite_reports_and_keeps_the_stored_bar(conn: sqlite3.Connection) -> None:
    """A changed value for a stored key is surfaced, and the first record wins."""
    ledger = SqliteLedger(conn)
    ledger.record_live_bar(_bar(0))
    stored = ledger.record_live_bar(_bar(0, close="61100"))
    assert stored == _bar(0)
    assert ledger.live_bars(_BTC, KlineInterval.H4, _T0, _T0 + _H4) == (_bar(0),)


@pytest.mark.parametrize("statement", ["UPDATE live_bars SET close = '0'", "DELETE FROM live_bars"])
def test_rows_cannot_be_rewritten_or_removed(conn: sqlite3.Connection, statement: str) -> None:
    """The schema refuses UPDATE and DELETE, so stored bars are evidence."""
    SqliteLedger(conn).record_live_bar(_bar(0))
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute(statement)


def test_range_is_half_open_ordered_and_per_stream(conn: sqlite3.Connection) -> None:
    """Reads return [start, end) for one pair and interval, oldest first."""
    ledger = SqliteLedger(conn)
    for i in (3, 1, 2, 0):
        ledger.record_live_bar(_bar(i))
    ledger.record_live_bar(_bar(1, pair=_ETH))
    got = ledger.live_bars(_BTC, KlineInterval.H4, _T0 + _H4, _T0 + _H4 * 3)
    assert got == (_bar(1), _bar(2))
    assert ledger.live_bars(_BTC, KlineInterval.D1, _T0, _T0 + _H4 * 4) == ()
