"""Unit tests for ``hermes archive-check`` (#359).

Invariant under test: the command checks the requested day for every
configured pair and interval and maps the outcome to its exit code (0 match,
1 mismatch, 2 usage, 3 failure). The archive is faked; the ledger is a real
file. Each mismatch alert is recorded once as an ``AnomalyDetected`` row (#136).
"""

from __future__ import annotations

import io
import sqlite3
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import ClassVar

import httpx
import pytest

from hermes.adapters.ledger.sqlite_ledger import SqliteLedger
from hermes.application.archive_check import ALERT_SOURCE
from hermes.domain.errors import InfraError
from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.value_objects.pair import Pair
from hermes.entrypoints import archive_check as module
from hermes.entrypoints import cli
from tests.support.anomaly_rows import recorded_anomalies

_PROJECT_CONFIG = Path(__file__).resolve().parents[3] / "config"


class _EmptyArchive:
    """Publishes an empty day for every stream and records what was asked."""

    asked: ClassVar[list[tuple[Pair, KlineInterval, date]]] = []

    def __init__(self, client: object) -> None:
        del client

    def daily_bars(self, pair: Pair, interval: KlineInterval, day: date) -> tuple[Bar, ...]:
        type(self).asked.append((pair, interval, day))
        return ()


class _FailingArchive(_EmptyArchive):
    def daily_bars(self, pair: Pair, interval: KlineInterval, day: date) -> tuple[Bar, ...]:
        raise InfraError("archive down")


def _ledger(tmp_path: Path) -> Path:
    db = tmp_path / "ledger.sqlite3"
    connection = sqlite3.connect(db)
    SqliteLedger(connection)
    connection.close()
    return db


def _main(argv: list[str]) -> tuple[int, str]:
    out = io.StringIO()
    return module.main(argv, stream=out, env={}), out.getvalue()


def test_empty_day_on_both_sides_exits_0_for_every_stream(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every configured pair and interval is checked for the requested day."""
    _EmptyArchive.asked = []
    monkeypatch.setattr(module, "BinanceVisionDailyArchive", _EmptyArchive)
    db = _ledger(tmp_path)
    code, out = _main(
        ["--date", "2026-10-02", "--db", str(db), "--config-dir", str(_PROJECT_CONFIG)]
    )
    assert code == 0
    assert "0 mismatches" in out
    assert {day for _, _, day in _EmptyArchive.asked} == {date(2026, 10, 2)}
    assert len(_EmptyArchive.asked) >= 1


def test_archive_failure_exits_3(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A download or checksum failure is a failed check, not a clean day."""
    monkeypatch.setattr(module, "BinanceVisionDailyArchive", _FailingArchive)
    out, err = io.StringIO(), io.StringIO()
    code = module.main(
        ["--db", str(_ledger(tmp_path)), "--config-dir", str(_PROJECT_CONFIG)],
        stream=out,
        err=err,
    )
    assert (code, out.getvalue(), err.getvalue()) == (3, "", "ERROR: archive down\n")


def test_bad_arguments_exit_2() -> None:
    """An unparseable date is a usage error."""
    assert _main(["--date", "yesterday"])[0] == 2


def test_help_exits_0() -> None:
    """--help prints usage and does no work."""
    code, out = _main(["--help"])
    assert code == 0
    assert out.startswith("Usage: hermes archive-check")


def test_cli_routes_the_subcommand(monkeypatch: pytest.MonkeyPatch) -> None:
    """`hermes archive-check` reaches this command's main."""
    seen: list[list[str]] = []

    def fake_main(rest: list[str], *, stream: object, err: object) -> int:
        seen.append(rest)
        return 0

    monkeypatch.setattr(module, "main", fake_main)
    assert cli.main(["archive-check", "--help"], stream=io.StringIO()) == 0
    assert seen == [["--help"]]


_PING_URL = "https://hc-ping.com/archive-check-uuid"  # gitleaks:allow


def _recording_ping(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(module, "_ping", lambda url, body: calls.append((url, body)))
    return calls


def test_completed_run_pings_the_dead_mans_switch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#38: a run that checked pings the URL once with the summary line."""
    monkeypatch.setattr(module, "BinanceVisionDailyArchive", _EmptyArchive)
    calls = _recording_ping(monkeypatch)
    out = io.StringIO()
    code = module.main(
        [
            "--date",
            "2026-10-02",
            "--db",
            str(_ledger(tmp_path)),
            "--config-dir",
            str(_PROJECT_CONFIG),
        ],
        stream=out,
        env={"HERMES_HEALTHCHECKS__ARCHIVE_CHECK_PING_URL": _PING_URL},
    )
    assert code == 0
    assert [url for url, _ in calls] == [_PING_URL]
    assert calls[0][1].startswith("2026-10-02: ")


def test_failed_run_pings_fail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """#38: a run that could not check pings <url>/fail with the error."""
    monkeypatch.setattr(module, "BinanceVisionDailyArchive", _FailingArchive)
    calls = _recording_ping(monkeypatch)
    code = module.main(
        ["--db", str(_ledger(tmp_path)), "--config-dir", str(_PROJECT_CONFIG)],
        stream=io.StringIO(),
        env={"HERMES_HEALTHCHECKS__ARCHIVE_CHECK_PING_URL": _PING_URL},
    )
    assert (code, calls) == (3, [(_PING_URL + "/fail", "archive down")])


def test_no_ping_url_means_no_ping(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Unset, the dead-man's switch is off."""
    monkeypatch.setattr(module, "BinanceVisionDailyArchive", _EmptyArchive)
    calls = _recording_ping(monkeypatch)
    code = module.main(
        ["--db", str(_ledger(tmp_path)), "--config-dir", str(_PROJECT_CONFIG)],
        stream=io.StringIO(),
        env={},
    )
    assert (code, calls) == (0, [])


def test_a_failed_ping_never_changes_the_exit_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A healthchecks.io outage is reported as a warning only."""
    monkeypatch.setattr(module, "BinanceVisionDailyArchive", _EmptyArchive)

    def down(url: str, body: str) -> None:
        raise httpx.ConnectError("down")

    monkeypatch.setattr(module, "_ping", down)
    out = io.StringIO()
    code = module.main(
        ["--db", str(_ledger(tmp_path)), "--config-dir", str(_PROJECT_CONFIG)],
        stream=out,
        env={"HERMES_HEALTHCHECKS__ARCHIVE_CHECK_PING_URL": _PING_URL},
    )
    assert code == 0
    assert "WARNING: healthcheck ping failed: ConnectError" in out.getvalue()


class _OneBarArchive(_EmptyArchive):
    """Publishes one 4h BTC bar the empty live store does not hold: one mismatch."""

    def daily_bars(self, pair: Pair, interval: KlineInterval, day: date) -> tuple[Bar, ...]:
        if pair.base.value != "BTC" or interval is not KlineInterval.H4:
            return ()
        open_time = datetime(2026, 10, 2, tzinfo=UTC)
        return (
            Bar(
                pair=pair,
                interval=interval,
                open_time=open_time,
                close_time=open_time + timedelta(hours=4) - timedelta(milliseconds=1),
                open=Decimal(60000),
                high=Decimal(60500),
                low=Decimal(59500),
                close=Decimal(60100),
                volume=Decimal(10),
            ),
        )


def test_each_mismatch_alert_is_recorded_once_in_the_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#136: one mismatch is one AnomalyDetected row from the archive check."""
    monkeypatch.setattr(module, "BinanceVisionDailyArchive", _OneBarArchive)
    db = _ledger(tmp_path)

    code, _ = _main(["--date", "2026-10-02", "--db", str(db), "--config-dir", str(_PROJECT_CONFIG)])

    rows = recorded_anomalies(db)
    assert (code, [(r["source"], r["severity"]) for r in rows]) == (
        1,
        [(ALERT_SOURCE, "WARNING")],
    )


def test_a_matching_day_records_no_anomaly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """#136: no mismatch, no alert, no AnomalyDetected row."""
    monkeypatch.setattr(module, "BinanceVisionDailyArchive", _EmptyArchive)
    db = _ledger(tmp_path)

    code, _ = _main(["--date", "2026-10-02", "--db", str(db), "--config-dir", str(_PROJECT_CONFIG)])

    assert (code, recorded_anomalies(db)) == (0, [])
