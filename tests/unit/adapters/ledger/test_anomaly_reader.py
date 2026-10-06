"""Unit tests for the read-only anomaly reader (#136).

Invariants under test:

- an appended ``AnomalyDetected`` reads back equal to what was appended;
- only anomalies at or after ``since`` are returned, newest first, and no
  other event type is;
- the reader never creates a missing ledger and never writes an existing one.
"""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from hermes.adapters.ledger.anomaly_reader import SqliteAnomalyReader
from hermes.adapters.ledger.sqlite_ledger import SqliteLedger
from hermes.domain.errors import LedgerError
from hermes.domain.events.anomaly_detected import AnomalyDetected, AnomalySeverity
from hermes.domain.events.shadow_capital_seeded import ShadowCapitalSeeded
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.strategy_id import StrategyId

_T0 = datetime(2026, 10, 5, 9, tzinfo=UTC)


def _anomaly(hours: int, source: str = "reconciler") -> AnomalyDetected:
    return AnomalyDetected(
        occurred_at=_T0 + timedelta(hours=hours),
        correlation_id=f"01JABCDEFGHJKMNPQRSTVWXYZ{hours % 10}",
        source=source,
        severity=AnomalySeverity.CRITICAL if hours % 2 else AnomalySeverity.WARNING,
        reason=f"reason {hours}",
    )


def _ledger(tmp_path: Path, *anomalies: AnomalyDetected) -> Path:
    db = tmp_path / "ledger.sqlite3"
    connection = sqlite3.connect(db)
    ledger = SqliteLedger(connection)
    for anomaly in anomalies:
        ledger.append_event(anomaly)
    connection.close()
    return db


def test_an_appended_anomaly_reads_back_equal(tmp_path: Path) -> None:
    """The round trip through the events table loses no field."""
    event = _anomaly(1)
    db = _ledger(tmp_path, event)

    with SqliteAnomalyReader.open(db) as reader:
        assert reader.since(_T0) == (event,)


def test_only_anomalies_since_the_bound_are_listed_newest_first(tmp_path: Path) -> None:
    """Older anomalies are left out; the rest come newest first."""
    db = _ledger(tmp_path, _anomaly(0, "a"), _anomaly(2, "b"), _anomaly(5, "c"))

    with SqliteAnomalyReader.open(db) as reader:
        listed = reader.since(_T0 + timedelta(hours=2))

    assert [a.source for a in listed] == ["c", "b"]


def test_other_event_types_are_not_listed(tmp_path: Path) -> None:
    """A non-anomaly event in the same table is never read as one."""
    db = _ledger(tmp_path)
    connection = sqlite3.connect(db)
    SqliteLedger(connection).append_event(
        ShadowCapitalSeeded(
            occurred_at=_T0,
            correlation_id="01JABCDEFGHJKMNPQRSTVWXYZ0",
            strategy_id=StrategyId("swing-donchian-4h"),
            amount=Money(amount=Decimal(5000), currency=Currency.USDT),
        )
    )
    connection.close()

    with SqliteAnomalyReader.open(db) as reader:
        assert reader.since(_T0 - timedelta(days=1)) == ()


def test_a_missing_ledger_raises_and_is_not_created(tmp_path: Path) -> None:
    """open() refuses a missing file and leaves none behind."""
    db = tmp_path / "absent.sqlite3"

    with pytest.raises(FileNotFoundError):
        SqliteAnomalyReader.open(db)
    assert not db.exists()


def test_reading_leaves_the_ledger_byte_identical(tmp_path: Path) -> None:
    """The reader only reads: the file digest is unchanged after a listing."""
    db = _ledger(tmp_path, _anomaly(1))
    before = hashlib.sha256(db.read_bytes()).hexdigest()

    with SqliteAnomalyReader.open(db) as reader:
        reader.since(_T0)

    assert hashlib.sha256(db.read_bytes()).hexdigest() == before


def test_a_naive_bound_is_refused(tmp_path: Path) -> None:
    """A naive since would compare against UTC strings wrongly, so it is a LedgerError."""
    db = _ledger(tmp_path)

    with SqliteAnomalyReader.open(db) as reader, pytest.raises(LedgerError):
        reader.since(datetime(2026, 10, 5))
