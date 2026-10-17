"""Unit tests for recording a CLI job's anomaly in the ledger (#136).

Invariants under test:

- a recorded anomaly is exactly one ``AnomalyDetected`` row carrying the
  event's source, severity, reason and correlation id;
- a missing ledger is warned about and never created;
- a file that is not a ledger is warned about and never raises, so the
  alert that follows still goes out.
"""

from __future__ import annotations

import io
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from hermes.adapters.ledger.sqlite_ledger import SqliteLedger
from hermes.domain.events.anomaly_detected import AnomalyDetected, AnomalySeverity
from hermes.entrypoints._anomaly_record import record_anomaly
from tests.support.anomaly_rows import recorded_anomalies

_EVENT = AnomalyDetected(
    occurred_at=datetime(2026, 10, 5, 9, tzinfo=UTC),
    correlation_id="01JABCDEFGHJKMNPQRSTVWXYZ0",
    source="reconciler",
    severity=AnomalySeverity.WARNING,
    reason="2026-09 needs review",
)


def _ledger(tmp_path: Path) -> Path:
    db = tmp_path / "ledger.sqlite3"
    connection = sqlite3.connect(db)
    SqliteLedger(connection)
    connection.close()
    return db


def test_a_recorded_anomaly_is_one_event_row_with_its_fields(tmp_path: Path) -> None:
    """One call appends one AnomalyDetected row holding the event's own fields."""
    db = _ledger(tmp_path)
    err = io.StringIO()

    written = record_anomaly(db, _EVENT, err)

    rows = recorded_anomalies(db)
    assert (written, err.getvalue(), len(rows)) == (True, "", 1)
    assert {k: rows[0][k] for k in ("source", "severity", "reason", "correlation_id")} == {
        "source": "reconciler",
        "severity": "WARNING",
        "reason": "2026-09 needs review",
        "correlation_id": "01JABCDEFGHJKMNPQRSTVWXYZ0",
    }


def test_a_missing_ledger_is_warned_about_and_not_created(tmp_path: Path) -> None:
    """No ledger file: one WARNING line, False, and still no file."""
    db = tmp_path / "absent.sqlite3"
    err = io.StringIO()

    written = record_anomaly(db, _EVENT, err)

    assert (written, err.getvalue().startswith("WARNING: no ledger"), db.exists()) == (
        False,
        True,
        False,
    )


def test_a_file_that_is_not_a_ledger_warns_and_does_not_raise(tmp_path: Path) -> None:
    """A corrupt file makes the record fail with a WARNING, never an exception."""
    db = tmp_path / "ledger.sqlite3"
    db.write_bytes(b"this is not a sqlite database at all, just text" * 4)
    err = io.StringIO()

    written = record_anomaly(db, _EVENT, err)

    assert (written, err.getvalue().startswith("WARNING: the anomaly could not be recorded")) == (
        False,
        True,
    )
