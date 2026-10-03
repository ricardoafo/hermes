"""Tests for the status report to console heartbeat bridge (#454).

Invariants protected here:

- the heartbeat state is the report's verdict, for every verdict;
- last and next check are the newest processed close and the earliest next
  close across streams;
- a down heartbeat carries a "down since" (the last check), the others none;
- a report with no streams falls back to the report time.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from hermes.adapters.ledger.status_reader import SqliteStatusReader
from hermes.application.console.heartbeat import heartbeat_from_report
from hermes.application.console.views import HeartbeatState
from hermes.application.status import (
    StatusReport,
    UnitState,
    VerdictLevel,
    build_report,
    ledger_missing_report,
)
from tests.support.status_ledger import INTERVALS, LAST_4H, NOW, PAIRS, write_ledger


def _report(tmp_path: Path, *, now=NOW, unit: UnitState = UnitState.RUNNING) -> StatusReport:
    tmp_path.mkdir(parents=True, exist_ok=True)
    with SqliteStatusReader.open(write_ledger(tmp_path / "hermes.sqlite3")) as reader:
        return build_report(
            reader,
            pairs=PAIRS,
            intervals=INTERVALS,
            now=now,
            settle=timedelta(seconds=5),
            grace=timedelta(seconds=900),
            unit=unit,
        )


@pytest.mark.parametrize(
    ("now_offset", "unit", "verdict", "state"),
    [
        (timedelta(minutes=10), UnitState.RUNNING, VerdictLevel.OK, HeartbeatState.OK),
        (timedelta(hours=4, minutes=30), UnitState.RUNNING, VerdictLevel.LATE, HeartbeatState.LATE),
        (timedelta(minutes=10), UnitState.FAILED, VerdictLevel.DOWN, HeartbeatState.DOWN),
    ],
    ids=["ok", "late", "down"],
)
def test_heartbeat_state_follows_the_status_verdict(
    tmp_path: Path,
    now_offset: timedelta,
    unit: UnitState,
    verdict: VerdictLevel,
    state: HeartbeatState,
) -> None:
    """`hermes status` and every console surface agree on ok, late and down."""
    report = _report(tmp_path, now=LAST_4H + now_offset, unit=unit)

    heartbeat = heartbeat_from_report(report)

    assert (report.verdict.level, heartbeat.state) == (verdict, state)


def test_checks_are_the_newest_close_and_the_earliest_next_close(tmp_path: Path) -> None:
    """With 4h and 1d streams, last check is the 16:00 close and next is 20:00."""
    heartbeat = heartbeat_from_report(_report(tmp_path))

    assert (heartbeat.as_of, heartbeat.last_check_at, heartbeat.next_check_at) == (
        NOW,
        LAST_4H,
        LAST_4H + timedelta(hours=4),
    )


def test_only_a_down_heartbeat_has_a_down_since(tmp_path: Path) -> None:
    """Down since is the last check, the latest moment hermes is known to have worked."""
    ok = heartbeat_from_report(_report(tmp_path / "ok"))
    down = heartbeat_from_report(_report(tmp_path / "down", unit=UnitState.FAILED))

    assert (ok.down_since, down.down_since) == (None, LAST_4H)


def test_a_report_without_streams_falls_back_to_its_time() -> None:
    """A missing ledger reads as down since the report time."""
    report = ledger_missing_report(NOW, UnitState.UNKNOWN, "/nope")

    heartbeat = heartbeat_from_report(report)

    assert (heartbeat.state, heartbeat.last_check_at, heartbeat.down_since) == (
        HeartbeatState.DOWN,
        NOW,
        NOW,
    )
