"""Unit tests for the per-bar stop marks in the SQLite ledger (#388).

Invariants under test:

- A stored mark reads back exactly, unknown values included.
- One mark per (strategy, pair, bar close): a replayed bar writes nothing.
- The latest read is keyed on the position's entry time, so a new position
  never inherits an earlier position's stop.
"""

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from hermes.adapters.ledger.sqlite_ledger import SqliteLedger
from hermes.domain.errors import LedgerError
from hermes.domain.strategy.stop_mark import StopMark, StopRecord
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.strategy_id import StrategyId

_BTC = Pair(base=Currency.BTC, quote=Currency.USDT)
_ETH = Pair(base=Currency.ETH, quote=Currency.USDT)
_SWING = StrategyId("swing-donchian-4h")
_ENTRY = datetime(2026, 10, 3, 12, tzinfo=UTC) - timedelta(milliseconds=1)
_H4 = timedelta(hours=4)


def _mark(
    i: int,
    *,
    entry: datetime = _ENTRY,
    pair: Pair = _BTC,
    stop: str = "61000.12345678",
    moved: bool | None = True,
    high: str | None = "64000.5",
) -> StopMark:
    return StopMark(
        strategy_id=_SWING,
        pair=pair,
        bar_close_time=entry + _H4 * i,
        record=StopRecord(
            entry_time=entry,
            stop_price=Decimal(stop),
            chandelier_level=Decimal(stop),
            atr=Decimal("812.000001"),
            high_since_entry=None if high is None else Decimal(high),
            stop_moved=moved,
        ),
    )


@pytest.fixture
def ledger() -> Iterator[SqliteLedger]:
    """Yield a ledger over an in-memory database."""
    conn = sqlite3.connect(":memory:")
    yield SqliteLedger(conn)
    conn.close()


@pytest.mark.parametrize(("moved", "high"), [(True, "64000.5"), (False, "1"), (None, None)])
def test_mark_round_trips_exactly(
    ledger: SqliteLedger, moved: bool | None, high: str | None
) -> None:
    """Every value survives storage without rounding, and unknown stays unknown."""
    mark = _mark(0, moved=moved, high=high)
    assert ledger.record_stop_mark(mark) is True
    assert ledger.latest_stop_mark(_SWING, _BTC, _ENTRY) == mark


def test_replayed_bar_writes_nothing(ledger: SqliteLedger) -> None:
    """A restart that re-handles a bar keeps the first mark and reports it."""
    ledger.record_stop_mark(_mark(1))
    assert ledger.record_stop_mark(_mark(1, stop="1")) is False
    assert ledger.latest_stop_mark(_SWING, _BTC, _ENTRY) == _mark(1)


def test_latest_is_the_newest_bar_of_that_position(ledger: SqliteLedger) -> None:
    """Rows written out of order still answer with the newest bar."""
    for i in (2, 0, 1):
        ledger.record_stop_mark(_mark(i, stop=f"6100{i}"))
    assert ledger.latest_stop_mark(_SWING, _BTC, _ENTRY) == _mark(2, stop="61002")


def test_a_new_position_does_not_inherit_the_previous_stop(ledger: SqliteLedger) -> None:
    """A later entry on the same pair finds nothing until its own bars are recorded."""
    ledger.record_stop_mark(_mark(3))
    later_entry = _ENTRY + _H4 * 10
    assert ledger.latest_stop_mark(_SWING, _BTC, later_entry) is None


def test_latest_is_per_pair(ledger: SqliteLedger) -> None:
    """Another pair's stop never answers for this pair's position."""
    ledger.record_stop_mark(_mark(1, pair=_ETH))
    assert ledger.latest_stop_mark(_SWING, _BTC, _ENTRY) is None


def test_unreadable_row_raises_ledger_error() -> None:
    """A corrupt stored price surfaces as a LedgerError, not a bad stop."""
    conn = sqlite3.connect(":memory:")
    try:
        ledger = SqliteLedger(conn)
        ledger.record_stop_mark(_mark(0))
        conn.execute("UPDATE stop_marks SET stop_price = 'garbage'")
        with pytest.raises(LedgerError, match="unreadable"):
            ledger.latest_stop_mark(_SWING, _BTC, _ENTRY)
    finally:
        conn.close()
