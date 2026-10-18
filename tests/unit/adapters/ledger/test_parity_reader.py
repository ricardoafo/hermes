"""Tests for the read-only parity reader.

Invariants protected here:

- the connection is read-only: any write raises, so the parity check can never
  change the ledger it reads;
- a missing file raises ``FileNotFoundError`` and creates nothing;
- bars are selected by close time, ``after < close <= upto`` (a bar belongs to
  the day it closes in);
- signals are selected by ``after < occurred_at <= upto`` and the latest signal
  is per pair and bounded; asked for one kind, it skips newer signals of the
  other kind (#706);
- trades come back with their stored fields, oldest first;
- missed bars are counted by close time, ``after < close <= upto`` (#1078).
"""

from __future__ import annotations

import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest

from hermes.adapters.ledger.parity_reader import SqliteParityReader
from hermes.adapters.ledger.sqlite_ledger import SqliteLedger
from hermes.domain.events.bar_missed import BarMissed
from hermes.domain.events.trade_executed import TradeSide
from hermes.domain.strategy.signal import SignalKind
from hermes.ports.parity_source import ParitySourcePort
from tests.support.fill_kernel import BTC_USDT, STRATEGY
from tests.support.parity import (
    D1,
    DAY_END,
    DAY_START,
    H4,
    bar,
    signal_event,
    trade_event,
)


@pytest.fixture
def db(tmp_path: Path) -> Path:
    """Build a migrated ledger holding bars, signals and trades around the test day."""
    path = tmp_path / "ledger.sqlite3"
    connection = sqlite3.connect(path)
    ledger = SqliteLedger(connection)
    for close in (DAY_START, DAY_START + timedelta(hours=4), DAY_END):
        ledger.record_live_bar(bar(close))
    ledger.record_live_bar(bar(DAY_END, D1))
    ledger.append_event(signal_event(DAY_START))
    ledger.append_event(signal_event(DAY_START + timedelta(hours=8)))
    ledger.append_event(signal_event(DAY_END + timedelta(hours=4), SignalKind.EXIT))
    ledger.append_event(trade_event(DAY_START + timedelta(hours=4, seconds=5)))
    ledger.append_event(trade_event(DAY_START + timedelta(hours=1), TradeSide.SELL))
    for close in (DAY_START, DAY_START + timedelta(hours=4), DAY_END):
        ledger.append_event(
            BarMissed.from_bar(
                bar(close), handled_at=DAY_END + timedelta(hours=1), correlation_id=f"m{close:%H}"
            )
        )
    connection.close()
    return path


def test_reader_satisfies_the_port() -> None:
    """The adapter is a structural ParitySourcePort."""
    assert issubclass(SqliteParityReader, ParitySourcePort)


def test_a_write_through_the_reader_connection_fails(db: Path) -> None:
    """mode=ro means even a stray DELETE cannot change the ledger."""
    with SqliteParityReader.open(db) as reader, pytest.raises(sqlite3.OperationalError):
        reader._conn.execute("DELETE FROM events")


def test_opening_a_missing_ledger_raises_and_creates_nothing(tmp_path: Path) -> None:
    """A wrong --db path must not leave an empty SQLite file behind."""
    path = tmp_path / "absent.sqlite3"

    with pytest.raises(FileNotFoundError):
        SqliteParityReader.open(path)
    assert not path.exists()


def test_bars_are_selected_by_close_time_after_exclusive_upto_inclusive(db: Path) -> None:
    """The bar closing at the day's start belongs to the previous day; the one at its end to it."""
    with SqliteParityReader.open(db) as reader:
        day = reader.bars_closing_between(BTC_USDT, H4, DAY_START, DAY_END)
        before = reader.bars_closing_between(BTC_USDT, H4, DAY_START - timedelta(days=1), DAY_START)
        daily = reader.bars_closing_between(BTC_USDT, D1, DAY_START, DAY_END)

    assert [b.close_time for b in day] == [DAY_START + timedelta(hours=4), DAY_END]
    assert [b.close_time for b in before] == [DAY_START]
    assert [(b.interval, b.close_time) for b in daily] == [(D1, DAY_END)]
    assert day[0] == bar(DAY_START + timedelta(hours=4))


def test_signals_between_excludes_the_start_and_includes_the_end(db: Path) -> None:
    """A signal at the day's start is the previous day's; one at its end is the day's."""
    with SqliteParityReader.open(db) as reader:
        found = reader.signals_between(STRATEGY, DAY_START, DAY_END)

    assert [s.occurred_at for s in found] == [DAY_START + timedelta(hours=8)]


def test_latest_signal_is_the_newest_up_to_the_bound(db: Path) -> None:
    """The newest signal at or before the bound, and none before the first one."""
    with SqliteParityReader.open(db) as reader:
        latest = reader.latest_signal(
            STRATEGY, BTC_USDT, at_or_before=DAY_START + timedelta(hours=9)
        )
        none = reader.latest_signal(STRATEGY, BTC_USDT, at_or_before=DAY_START - timedelta(hours=1))

    assert latest is not None
    assert (latest.occurred_at, latest.kind) == (DAY_START + timedelta(hours=8), SignalKind.ENTRY)
    assert none is None


def test_latest_signal_of_a_kind_skips_the_newer_other_kind(db: Path) -> None:
    """Asked for an entry after the stored exit, the newest entry comes back, not the exit."""
    with SqliteParityReader.open(db) as reader:
        entry = reader.latest_signal(
            STRATEGY, BTC_USDT, at_or_before=DAY_END + timedelta(hours=4), kind=SignalKind.ENTRY
        )

    assert entry == signal_event(DAY_START + timedelta(hours=8))


def test_trades_come_back_oldest_first_with_their_side(db: Path) -> None:
    """Fills are rehydrated in time order."""
    with SqliteParityReader.open(db) as reader:
        trades = reader.trades(STRATEGY)

    assert [(t.side, t.occurred_at) for t in trades] == [
        (TradeSide.SELL, DAY_START + timedelta(hours=1)),
        (TradeSide.BUY, DAY_START + timedelta(hours=4, seconds=5)),
    ]


def test_missed_bars_are_counted_by_close_time_after_exclusive_upto_inclusive(db: Path) -> None:
    """#1078: the bar closing at the day's start is the day before's; the one at its end counts."""
    with SqliteParityReader.open(db) as reader:
        count = reader.missed_bars_between(DAY_START, DAY_END)

    assert count == 2
