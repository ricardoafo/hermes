"""Unit tests for the daily archive check service and its read-only bar reader (#359).

Invariants under test:

- A seeded mismatch produces an alert naming pair, timeframe and bar.
- A clean day sends no alert; an unpublished day is reported, not alerted.
- The check reads the ledger with a read-only connection and never writes it.
"""

import sqlite3
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from hermes.adapters.ledger.live_bar_reader import SqliteLiveBarReader
from hermes.adapters.ledger.sqlite_ledger import SqliteLedger
from hermes.application.archive_check import ALERT_SOURCE, run_archive_check
from hermes.domain.errors import LedgerError
from hermes.domain.events.anomaly_detected import AnomalyDetected
from hermes.domain.events.base import DomainEvent
from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair

_BTC = Pair(base=Currency.BTC, quote=Currency.USDT)
_ETH = Pair(base=Currency.ETH, quote=Currency.USDT)
_DAY = date(2026, 10, 2)
_T0 = datetime(2026, 10, 2, tzinfo=UTC)
_H4 = timedelta(hours=4)
_NOW = datetime(2026, 10, 3, 9, tzinfo=UTC)


def _bar(i: int, pair: Pair = _BTC) -> Bar:
    open_time = _T0 + _H4 * i
    return Bar(
        pair=pair,
        interval=KlineInterval.H4,
        open_time=open_time,
        close_time=open_time + _H4 - timedelta(milliseconds=1),
        open=Decimal("60000"),
        high=Decimal("60500"),
        low=Decimal("59500"),
        close=Decimal("60100.5"),
        volume=Decimal("10"),
    )


class _Archive:
    """Archive fake keyed by pair; a pair absent from the map is unpublished."""

    def __init__(self, days: dict[Pair, list[Bar]]) -> None:
        self.days = days

    def daily_bars(self, pair: Pair, interval: KlineInterval, day: date) -> tuple[Bar, ...] | None:
        bars = self.days.get(pair)
        return None if bars is None else tuple(bars)


def _ledger_with(db: Path, bars: list[Bar]) -> None:
    connection = sqlite3.connect(db)
    ledger = SqliteLedger(connection)
    for bar in bars:
        ledger.record_live_bar(bar)
    connection.close()


def _run(db: Path, archive: _Archive, pairs: tuple[Pair, ...] = (_BTC,)) -> tuple:
    sent: list[DomainEvent] = []
    report = run_archive_check(
        archive=archive,
        live=SqliteLiveBarReader(db),
        pairs=pairs,
        intervals=(KlineInterval.H4,),
        day=_DAY,
        notify=sent.append,
        now=_NOW,
        correlation_id="01TESTCORRELATION",
    )
    return report, sent


def test_seeded_mismatch_alerts_with_pair_timeframe_and_bar(tmp_path: Path) -> None:
    """One changed close in the stored day yields one alert naming BTC/USDT 4h and the bar."""
    db = tmp_path / "ledger.sqlite3"
    day = [_bar(i) for i in range(6)]
    _ledger_with(db, [*day[:3], replace(day[3], close=Decimal("60100.6")), *day[4:]])

    report, sent = _run(db, _Archive({_BTC: day}))

    assert len(report.mismatches) == 1
    (alert,) = sent
    assert isinstance(alert, AnomalyDetected)
    assert alert.source == ALERT_SOURCE
    assert "BTC/USDT 4h" in alert.reason
    assert day[3].open_time.isoformat() in alert.reason
    assert "close live=60100.6 reference=60100.5" in alert.reason


def test_missing_live_bar_alerts(tmp_path: Path) -> None:
    """A bar the archive has but the live store lacks is alerted as missing_live."""
    db = tmp_path / "ledger.sqlite3"
    day = [_bar(i) for i in range(6)]
    _ledger_with(db, day[:5])
    _, sent = _run(db, _Archive({_BTC: day}))
    (alert,) = sent
    assert isinstance(alert, AnomalyDetected)
    assert alert.reason.startswith("Archive check missing_live for BTC/USDT 4h")


def test_clean_day_sends_nothing(tmp_path: Path) -> None:
    """Matching days produce no alert and report the stream as checked."""
    db = tmp_path / "ledger.sqlite3"
    day = [_bar(i) for i in range(6)]
    _ledger_with(db, day)
    report, sent = _run(db, _Archive({_BTC: day}))
    assert (sent, report.checked) == ([], ((_BTC, KlineInterval.H4),))


def test_unpublished_day_is_reported_not_alerted(tmp_path: Path) -> None:
    """An archive not posted yet is listed as unpublished, without an alert."""
    db = tmp_path / "ledger.sqlite3"
    _ledger_with(db, [_bar(0, _ETH)])
    report, sent = _run(db, _Archive({}), pairs=(_ETH,))
    assert (sent, report.unpublished) == ([], ((_ETH, KlineInterval.H4),))


def test_check_does_not_modify_the_ledger(tmp_path: Path) -> None:
    """Read-only: the ledger file is byte-identical after a check that found mismatches."""
    db = tmp_path / "ledger.sqlite3"
    _ledger_with(db, [_bar(0)])
    before = db.read_bytes()
    _run(db, _Archive({_BTC: [_bar(i) for i in range(6)]}))
    assert db.read_bytes() == before


def test_reader_connection_refuses_writes(tmp_path: Path) -> None:
    """The reader opens mode=ro, so even a missing ledger is not created."""
    missing = tmp_path / "absent.sqlite3"
    with pytest.raises(LedgerError):
        SqliteLiveBarReader(missing).live_bars(_BTC, KlineInterval.H4, _T0, _T0 + _H4)
    assert not missing.exists()
