"""Tests for ``hermes anomalies`` (#136).

Invariants protected here:

- each recorded anomaly since the bound is one line, newest first, naming
  its UTC time, severity, source and reason;
- ``--json`` is one document with every field, exact;
- an empty window says so and exits 0;
- a relative ``--since`` is exactly that age before now, and a date is read
  as UTC midnight;
- usage errors exit 2; a missing ledger exits 3 and is not created;
- the command is registered and reachable through ``hermes``.
"""

import io
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hermes.adapters.ledger.sqlite_ledger import SqliteLedger
from hermes.domain.events.anomaly_detected import AnomalyDetected, AnomalySeverity
from hermes.entrypoints import anomalies as module
from hermes.entrypoints import cli
from hermes.entrypoints.commands import discover

_NOW = datetime(2026, 10, 6, 12, tzinfo=UTC)


def _anomaly(age: timedelta, source: str, severity: AnomalySeverity) -> AnomalyDetected:
    return AnomalyDetected(
        occurred_at=_NOW - age,
        correlation_id="01JABCDEFGHJKMNPQRSTABC123",
        source=source,
        severity=severity,
        reason=f"{source} fired",
    )


def _ledger(tmp_path: Path, *anomalies: AnomalyDetected) -> Path:
    db = tmp_path / "ledger.sqlite3"
    connection = sqlite3.connect(db)
    ledger = SqliteLedger(connection)
    for anomaly in anomalies:
        ledger.append_event(anomaly)
    connection.close()
    return db


def _run(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = module.main(argv, stream=out, err=err, now=_NOW)
    return code, out.getvalue(), err.getvalue()


def test_recent_anomalies_are_one_line_each_newest_first(tmp_path: Path) -> None:
    """Two anomalies in the default 7 days, one older: two lines, newest first."""
    db = _ledger(
        tmp_path,
        _anomaly(timedelta(days=8), "old", AnomalySeverity.INFO),
        _anomaly(timedelta(hours=30), "reconciler", AnomalySeverity.WARNING),
        _anomaly(timedelta(hours=2), "data.archive_check", AnomalySeverity.CRITICAL),
    )

    code, out, err = _run(["--db", str(db)])

    assert (code, err) == (0, "")
    assert out == (
        "2026-10-06 10:00:00Z  CRITICAL  data.archive_check  data.archive_check fired  …ABC123\n"
        "2026-10-05 06:00:00Z  WARNING   reconciler  reconciler fired  …ABC123\n"
    )


def test_json_carries_every_field_exactly(tmp_path: Path) -> None:
    """--json is one document with the bound and each anomaly's fields."""
    db = _ledger(tmp_path, _anomaly(timedelta(hours=1), "parity", AnomalySeverity.WARNING))

    code, out, _ = _run(["--db", str(db), "--json", "--since", "1d"])

    assert (code, json.loads(out)) == (
        0,
        {
            "since": "2026-10-05T12:00:00+00:00",
            "anomalies": [
                {
                    "occurred_at": "2026-10-06T11:00:00+00:00",
                    "severity": "WARNING",
                    "source": "parity",
                    "reason": "parity fired",
                    "correlation_id": "01JABCDEFGHJKMNPQRSTABC123",
                }
            ],
        },
    )


def test_an_empty_window_says_so_and_exits_zero(tmp_path: Path) -> None:
    """No anomaly since the bound: one plain line, exit 0."""
    db = _ledger(tmp_path, _anomaly(timedelta(days=3), "reconciler", AnomalySeverity.WARNING))

    code, out, _ = _run(["--db", str(db), "--since", "2026-10-06"])

    assert (code, out) == (0, "No anomalies recorded since 2026-10-06 00:00:00Z.\n")


@given(
    amount=st.integers(min_value=0, max_value=10_000),
    unit=st.sampled_from(
        [("h", timedelta(hours=1)), ("d", timedelta(days=1)), ("w", timedelta(weeks=1))]
    ),
)
def test_a_relative_since_is_exactly_that_age_before_now(
    amount: int, unit: tuple[str, timedelta]
) -> None:
    """``N[hdw]`` is now minus N hours, days or weeks, for every N."""
    suffix, step = unit
    assert module.parse_since(f"{amount}{suffix}", _NOW) == _NOW - step * amount


def test_a_naive_date_is_read_as_utc_midnight() -> None:
    """A bare date is the start of that UTC day."""
    assert module.parse_since("2026-10-01", _NOW) == datetime(2026, 10, 1, tzinfo=UTC)


@pytest.mark.parametrize("argv", [["--since", "soon"], ["--since", "7x"], ["--bogus"]])
def test_usage_errors_exit_two(argv: list[str], tmp_path: Path) -> None:
    """A bad --since or an unknown flag is a usage error, never a read."""
    code, out, err = _run([*argv, "--db", str(tmp_path / "absent.sqlite3")])

    assert (code, out, err.startswith("ERROR:")) == (2, "", True)


def test_a_missing_ledger_exits_three_and_is_not_created(tmp_path: Path) -> None:
    """A wrong --db is reported and leaves no file behind."""
    db = tmp_path / "absent.sqlite3"

    code, _, err = _run(["--db", str(db)])

    assert (code, err.startswith("ERROR: no ledger"), db.exists()) == (3, True, False)


def test_help_exits_zero() -> None:
    """--help prints the usage."""
    code, out, _ = _run(["--help"])

    assert (code, out.startswith("Usage: hermes anomalies")) == (0, True)


def test_the_command_is_registered_and_dispatched(tmp_path: Path) -> None:
    """`hermes anomalies` reaches this module through the registry."""
    db = _ledger(tmp_path)
    out = io.StringIO()

    code = cli.main(["anomalies", "--db", str(db), "--since", "1h"], stream=out)

    assert ("anomalies" in discover(), code, out.getvalue().startswith("No anomalies")) == (
        True,
        0,
        True,
    )
