"""Tests for the read-only SQLite status reader.

Invariants protected here:

- the connection is read-only: any write raises, so ``hermes status`` can
  never change the ledger it reports on;
- a missing file is reported as ``FileNotFoundError``, not created;
- ``last_processed_close`` is per stream and newest-wins;
- ``count_signals_since`` is inclusive of its bound;
- ``open_entries`` lists a pair only while its newest filled entry has no exit
  after it; an entry without a fill (refused or halted) opens nothing (#962).
- ``last_clock_skew`` is the newest clock-skew anomaly, whatever else was recorded.

- ``open_entries`` lists a pair only while its newest signal is an entry;
- ``bar_check_seconds`` is recorded_at minus the bar close, oldest first (#717).
"""

from __future__ import annotations

import sqlite3
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from hermes.adapters.ledger.sqlite_ledger import SqliteLedger
from hermes.adapters.ledger.status_reader import SqliteStatusReader
from hermes.domain.events.anomaly_detected import (
    CLOCK_SKEW_SOURCE,
    AnomalyDetected,
    AnomalySeverity,
)

from hermes.domain.errors import LedgerError
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.strategy.signal import SignalKind
from hermes.ports.status import ShadowEntry, StatusReadPort
from tests.support.status_ledger import (
    BTC,
    ETH,
    LAST_1D,
    LAST_4H,
    SOL,
    bar_processed,
    fill,
    signal,
    write_ledger,
)

_ONE_MS = timedelta(milliseconds=1)


def test_reader_satisfies_the_port() -> None:
    """The adapter is a structural StatusReadPort."""
    assert issubclass(SqliteStatusReader, StatusReadPort)


def test_a_write_through_the_reader_connection_fails(tmp_path: Path) -> None:
    """mode=ro means even a stray INSERT cannot change the ledger."""
    path = write_ledger(tmp_path / "hermes.sqlite3")

    with SqliteStatusReader.open(path) as reader, pytest.raises(sqlite3.OperationalError):
        reader._conn.execute("DELETE FROM events")


def test_opening_a_missing_ledger_raises_and_creates_nothing(tmp_path: Path) -> None:
    """A wrong --db path must not leave an empty SQLite file behind."""
    path = tmp_path / "absent.sqlite3"

    with pytest.raises(FileNotFoundError):
        SqliteStatusReader.open(path)
    assert not path.exists()


def test_last_processed_close_is_per_stream_and_newest_wins(tmp_path: Path) -> None:
    """Each stream returns its own newest inclusive close."""
    path = write_ledger(tmp_path / "hermes.sqlite3", last_4h=LAST_4H - timedelta(hours=4))
    connection = sqlite3.connect(path)
    SqliteLedger(connection).append_event(bar_processed(BTC, KlineInterval.H4, LAST_4H))
    connection.close()

    with SqliteStatusReader.open(path) as reader:
        closes = (
            reader.last_processed_close(BTC, KlineInterval.H4),
            reader.last_processed_close(ETH, KlineInterval.H4),
            reader.last_processed_close(BTC, KlineInterval.D1),
        )

    assert closes == (
        LAST_4H - _ONE_MS,
        LAST_4H - timedelta(hours=4) - _ONE_MS,
        LAST_1D - _ONE_MS,
    )


def test_last_processed_close_is_none_for_a_stream_never_processed(tmp_path: Path) -> None:
    """No row means no close, not an error."""
    path = write_ledger(tmp_path / "hermes.sqlite3", last_1d=None)

    with SqliteStatusReader.open(path) as reader:
        closes = (
            reader.last_processed_close(SOL, KlineInterval.D1),
            reader.last_processed_close(SOL, KlineInterval.H4),
        )

    assert closes == (None, LAST_4H - _ONE_MS)


def test_count_signals_since_includes_its_bound(tmp_path: Path) -> None:
    """A signal exactly at ``since`` counts; one a millisecond before does not."""
    signals = (
        signal(BTC, SignalKind.ENTRY, LAST_4H - _ONE_MS),
        signal(BTC, SignalKind.EXIT, LAST_4H),
        signal(ETH, SignalKind.ENTRY, LAST_4H + timedelta(hours=4)),
    )
    path = write_ledger(tmp_path / "hermes.sqlite3", signals=signals)

    with SqliteStatusReader.open(path) as reader:
        assert reader.count_signals_since(LAST_4H) == 2


def test_open_entries_keep_only_pairs_whose_newest_signal_is_an_entry(tmp_path: Path) -> None:
    """BTC entered then exited (closed); ETH entered (open)."""
    signals = (
        signal(BTC, SignalKind.ENTRY, LAST_4H - timedelta(hours=8), price="61000"),
        signal(ETH, SignalKind.ENTRY, LAST_4H - timedelta(hours=4), price="2618.45"),
        signal(BTC, SignalKind.EXIT, LAST_4H, price="60500"),
    )
    path = write_ledger(
        tmp_path / "hermes.sqlite3", signals=signals, fills=tuple(map(fill, signals))
    )

    with SqliteStatusReader.open(path) as reader:
        entries = reader.open_entries()

    assert entries == (
        ShadowEntry(
            pair=ETH,
            entry_time=LAST_4H - timedelta(hours=4),
            entry_price=Decimal("2618.45"),
        ),
    )


# SEV-1: no rubric row matches; status must never show a position the engine did not open
def test_an_entry_without_a_fill_is_not_an_open_position(tmp_path: Path) -> None:
    """Issue #962: ETH's entry was refused (no fill), so only the filled SOL entry is open."""
    sol = signal(SOL, SignalKind.ENTRY, LAST_4H - timedelta(hours=8), price="190")
    eth = signal(
        ETH,
        SignalKind.ENTRY,
        LAST_4H - timedelta(hours=4),
        price="3714.45",
        correlation_id="01J9Z8T3M5BQR2FVKN4XWY6PH2",
    )
    path = write_ledger(
        tmp_path / "hermes.sqlite3",
        signals=(sol, eth),
        fills=(fill(sol),),
    )

    with SqliteStatusReader.open(path) as reader:
        entries = reader.open_entries()

    assert entries == (
        ShadowEntry(pair=SOL, entry_time=LAST_4H - timedelta(hours=8), entry_price=Decimal("190")),
    )


def test_a_refused_entry_leaves_an_earlier_open_position_open(tmp_path: Path) -> None:
    """An unfilled entry after a filled one changes nothing: the first position stays open."""
    first = signal(ETH, SignalKind.ENTRY, LAST_4H - timedelta(hours=8), price="3600")
    refused = signal(
        ETH,
        SignalKind.ENTRY,
        LAST_4H - timedelta(hours=4),
        price="3714.45",
        correlation_id="01J9Z8T3M5BQR2FVKN4XWY6PH3",
    )
    path = write_ledger(
        tmp_path / "hermes.sqlite3",
        signals=(first, refused),
        fills=(fill(first),),
    )

    with SqliteStatusReader.open(path) as reader:
        entries = reader.open_entries()

    assert entries == (
        ShadowEntry(pair=ETH, entry_time=LAST_4H - timedelta(hours=8), entry_price=Decimal("3600")),
    )


def test_last_clock_skew_is_the_newest_skew_anomaly_and_ignores_others(tmp_path: Path) -> None:
    """A later anomaly from another source does not hide the clock's own state (#859)."""
    path = write_ledger(tmp_path / "hermes.sqlite3")
    at = LAST_4H

    def anomaly(source: str, severity: AnomalySeverity, minutes: int) -> AnomalyDetected:
        return AnomalyDetected(
            occurred_at=at + timedelta(minutes=minutes),
            correlation_id=f"c-{minutes}",
            source=source,
            severity=severity,
            reason=f"r{minutes}",
        )

    connection = sqlite3.connect(path)
    try:
        ledger = SqliteLedger(connection)
        ledger.append_event(anomaly(CLOCK_SKEW_SOURCE, AnomalySeverity.CRITICAL, 1))
        ledger.append_event(anomaly(CLOCK_SKEW_SOURCE, AnomalySeverity.INFO, 2))
        ledger.append_event(anomaly(CLOCK_SKEW_SOURCE, AnomalySeverity.CRITICAL, 3))
        ledger.append_event(anomaly("serve.bar_gap", AnomalySeverity.INFO, 4))
        connection.commit()
    finally:
        connection.close()

    with SqliteStatusReader.open(path) as reader:
        latest = reader.last_clock_skew()

    assert latest is not None
    assert (latest.severity, latest.reason) == (AnomalySeverity.CRITICAL, "r3")


def test_last_clock_skew_is_none_when_only_other_anomalies_were_recorded(tmp_path: Path) -> None:
    """A CRITICAL anomaly from another source never reads as an off clock."""
    path = write_ledger(tmp_path / "hermes.sqlite3")
    connection = sqlite3.connect(path)
    try:
        SqliteLedger(connection).append_event(
            AnomalyDetected(
                occurred_at=LAST_4H,
                correlation_id="c-gap",
                source="serve.bar_gap",
                severity=AnomalySeverity.CRITICAL,
                reason="bar never published",
            )
        )
        connection.commit()
    finally:
        connection.close()

    with SqliteStatusReader.open(path) as reader:
        skew = reader.last_clock_skew()
        recorded = reader._conn.execute(
            "SELECT COUNT(*) FROM events WHERE event_type = 'AnomalyDetected'"
        ).fetchone()[0]

    assert (skew, recorded) == (None, 1)

def _insert_bar_check(path: Path, occurred: str, recorded: str) -> None:
    connection = sqlite3.connect(path)
    with connection:
        connection.execute(
            "INSERT INTO events (event_type, event_schema_version, correlation_id, "
            "occurred_at, payload_json, recorded_at) VALUES ('BarProcessed', 1, 'c', ?, '{}', ?)",
            (occurred, recorded),
        )
    connection.close()


def test_bar_check_seconds_is_recorded_minus_close_oldest_first(tmp_path: Path) -> None:
    """Each BarProcessed row's recorded_at minus its close, in whole seconds, oldest first.

    The System screen draws these as the bar-check time sparkline (#717).
    """
    path = write_ledger(tmp_path / "hermes.sqlite3", last_4h=None, last_1d=None)
    _insert_bar_check(path, "2026-10-03T12:00:00+00:00", "2026-10-03T12:00:04.900000+00:00")
    _insert_bar_check(path, "2026-10-03T16:00:00+00:00", "2026-10-03T16:00:09+00:00")
    _insert_bar_check(path, "2026-10-03T20:00:00+00:00", "2026-10-03T20:00:03+00:00")

    with SqliteStatusReader.open(path) as reader:
        assert reader.bar_check_seconds(2) == (Decimal(9), Decimal(3))


def test_bar_check_seconds_is_empty_before_the_first_bar(tmp_path: Path) -> None:
    """No BarProcessed row yet: an empty tuple, which the screen shows as not available yet."""
    path = write_ledger(tmp_path / "hermes.sqlite3", last_4h=None, last_1d=None)

    with SqliteStatusReader.open(path) as reader:
        assert reader.bar_check_seconds(30) == ()


def test_bar_check_seconds_reports_an_unreadable_time(tmp_path: Path) -> None:
    """A row whose time does not parse raises LedgerError, so the panel degrades."""
    path = write_ledger(tmp_path / "hermes.sqlite3", last_4h=None, last_1d=None)
    _insert_bar_check(path, "not a time", "2026-10-03T20:00:03+00:00")

    with SqliteStatusReader.open(path) as reader, pytest.raises(LedgerError):
        reader.bar_check_seconds(30)
