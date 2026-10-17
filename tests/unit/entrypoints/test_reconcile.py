"""Tests for ``hermes reconcile`` (change 35-monthly-reconciliation R-4, R-5).

Invariants protected here:

- exit 0 only for CLEAN; REVIEW and DRIFT exit 1; usage 2; config or ledger 3;
- without a month it covers the previous Madrid month;
- a missing ledger is reported and never created;
- ``--json`` carries exact decimal strings;
- ``--notify`` sends one alert for a non-clean month, none for a clean one,
  and a failed send never changes the exit code;
- every ``--notify`` alert is recorded once as an ``AnomalyDetected`` row,
  even when Telegram is not configured, and a run without one records none
  (#136);
- the command is registered and reachable through ``hermes``;
- only enabled roster entries seed the book: the shipped roster's disabled
  ``swing-mvp1-v2`` adds no capital (v2-strategy-config).
"""

from __future__ import annotations

import io
import json
import sqlite3
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

from hermes.adapters.notifier.telegram_adapter import TelegramAdapter
from hermes.domain.errors import InfraError
from hermes.domain.events.base import DomainEvent
from hermes.entrypoints import reconcile as reconcile_module
from hermes.entrypoints.commands import discover
from hermes.ports.notifier import NotifierPort
from tests.support.anomaly_rows import recorded_anomalies
from tests.support.reconciliation_ledger import write_september

_CONFIG = Path(__file__).resolve().parents[3] / "config"
_OCT_4 = datetime(2026, 10, 4, 2, tzinfo=UTC)


class _Recorder:
    """NotifierPort double that records events; integration seam: test_telegram_adapter.py."""

    def __init__(self, *, fail: bool = False) -> None:
        self.sent: list[DomainEvent] = []
        self.fail = fail

    def notify_urgent(self, *, event: str, correlation_id: str, message: str) -> None:
        raise AssertionError("reconcile sends domain events, not raw urgent text")

    def notify_report(
        self,
        *,
        event: str,
        correlation_id: str,
        title: str,
        body: str,
        attachments: list[Path] | None = None,
    ) -> None:
        raise AssertionError("reconcile sends domain events, not reports")

    def notify(self, event: DomainEvent) -> None:
        if self.fail:
            raise InfraError("telegram down")
        self.sent.append(event)


def _run(
    args: list[str],
    *,
    notifier: _Recorder | None = None,
    now: datetime = _OCT_4,
    err: io.StringIO | None = None,
) -> tuple[int, str]:
    out = io.StringIO()

    def factory(_env: Mapping[str, str]) -> NotifierPort | None:
        return notifier

    code = reconcile_module.main(
        ["--config-dir", str(_CONFIG), *args],
        stream=out,
        now=now,
        notifier_factory=factory,
        err=err,
    )
    return code, out.getvalue()


def test_a_clean_month_exits_zero_and_is_ready_for_the_cut(tmp_path: Path) -> None:
    """September reconciles CLEAN with its realized 15.60 EUR shown."""
    db = write_september(tmp_path / "l.sqlite3")

    code, out = _run(["2026-09", "--db", str(db)])

    assert code == 0
    assert "Verdict: CLEAN - ready for the month-end lot cut" in out
    assert "realized 15.60" in out


def test_without_a_month_the_previous_madrid_month_is_covered(tmp_path: Path) -> None:
    """Run on 4 October, the default month is September."""
    db = write_september(tmp_path / "l.sqlite3")

    _, out = _run(["--db", str(db)])

    assert out.startswith("Reconciliation 2026-09 ")


def test_drift_exits_one(tmp_path: Path) -> None:
    """A tampered lot table is DRIFT, exit 1."""
    db = write_september(tmp_path / "l.sqlite3")
    connection = sqlite3.connect(db)
    connection.execute("UPDATE lots SET remaining_qty = '0.005'")
    connection.commit()
    connection.close()

    code, out = _run(["2026-09", "--db", str(db)])

    assert (code, "Verdict: DRIFT" in out) == (1, True)


def test_pending_eur_values_exit_one_with_the_backfill_hint(tmp_path: Path) -> None:
    """REVIEW points at `hermes fx backfill` and shows the P&L as pending."""
    db = write_september(tmp_path / "l.sqlite3", priced=False)

    code, out = _run(["2026-09", "--db", str(db)])

    assert code == 1
    assert "Verdict: REVIEW - run `hermes fx backfill`" in out
    assert "realized pending" in out


def test_json_carries_exact_decimal_strings(tmp_path: Path) -> None:
    """--json: verdict, book of 4659.14 USDT and realized 15.6 as exact strings."""
    db = write_september(tmp_path / "l.sqlite3")

    code, out = _run(["2026-09", "--db", str(db), "--json"])

    payload = json.loads(out)
    usdt = next(c for c in payload["checks"] if c["asset"] == "USDT")
    assert (code, payload["verdict"], payload["ready_for_cut"]) == (0, "clean", True)
    assert (usdt["book"], payload["tax"]["realized_eur"]) == ("4659.14", "15.6")


def test_a_disabled_roster_entry_adds_no_capital_to_the_book(tmp_path: Path) -> None:
    """The shipped roster carries v2 disabled; the USDT book stays v1's 4659.14, not +5000."""
    db = write_september(tmp_path / "l.sqlite3")

    _, out = _run(["2026-09", "--db", str(db), "--json"])

    usdt = next(c for c in json.loads(out)["checks"] if c["asset"] == "USDT")
    assert usdt["book"] == "4659.14"


def test_a_missing_ledger_exits_three_and_creates_nothing(tmp_path: Path) -> None:
    """A wrong --db is a ledger error; no empty file is left behind."""
    db = tmp_path / "absent.sqlite3"

    err = io.StringIO()
    code, out = _run(["2026-09", "--db", str(db)], err=err)

    assert (code, out, err.getvalue().startswith("ERROR: no ledger"), db.exists()) == (
        3,
        "",
        True,
        False,
    )


def test_a_broken_config_exits_three(tmp_path: Path) -> None:
    """Seeds come from the roster; without a config there is no book."""
    out, err = io.StringIO(), io.StringIO()

    code = reconcile_module.main(
        ["--config-dir", str(tmp_path), "--db", str(tmp_path / "x"), "2026-09"],
        stream=out,
        now=_OCT_4,
        err=err,
    )

    assert (code, out.getvalue(), err.getvalue().startswith("ERROR: config load failed")) == (
        3,
        "",
        True,
    )


def test_a_malformed_month_exits_two() -> None:
    """`2026-9` is not YYYY-MM."""
    err = io.StringIO()
    code, out = _run(["2026-9"], err=err)

    assert (code, out, "expected YYYY-MM" in err.getvalue()) == (2, "", True)


def test_a_future_month_exits_two() -> None:
    """November has not started on 4 October."""
    err = io.StringIO()
    code, out = _run(["2026-11"], err=err)

    assert (code, out, "has not started yet" in err.getvalue()) == (2, "", True)


def test_a_negative_tolerance_exits_two() -> None:
    """A negative tolerance would flag exact matches as drift."""
    code, _ = _run(["2026-09", "--tolerance", "-1"])

    assert code == 2


def test_an_unknown_flag_exits_two() -> None:
    """Unparseable arguments are a usage error, with the usage text."""
    err = io.StringIO()
    code, out = _run(["--bogus"], err=err)

    assert (code, out, "Usage: hermes reconcile" in err.getvalue()) == (2, "", True)


def test_help_exits_zero() -> None:
    """--help prints the usage."""
    code, out = _run(["--help"])

    assert (code, out.startswith("Usage: hermes reconcile")) == (0, True)


def test_notify_sends_one_alert_for_a_month_needing_review(tmp_path: Path) -> None:
    """--notify on a REVIEW month sends exactly one reconciler alert."""
    db = write_september(tmp_path / "l.sqlite3", priced=False)
    notifier = _Recorder()

    code, out = _run(["2026-09", "--db", str(db), "--notify"], notifier=notifier)

    assert (code, len(notifier.sent), "Alert sent." in out) == (1, 1, True)


def test_notify_stays_quiet_for_a_clean_month(tmp_path: Path) -> None:
    """A CLEAN month never reaches Telegram."""
    db = write_september(tmp_path / "l.sqlite3")
    notifier = _Recorder()

    code, _ = _run(["2026-09", "--db", str(db), "--notify"], notifier=notifier)

    assert (code, notifier.sent) == (0, [])


def test_notify_without_telegram_warns_and_keeps_the_exit_code(tmp_path: Path) -> None:
    """No credentials: a warning, and the verdict still decides the exit code."""
    db = write_september(tmp_path / "l.sqlite3", priced=False)

    code, out = _run(["2026-09", "--db", str(db), "--notify"], notifier=None)

    assert (code, "Telegram is not configured" in out) == (1, True)


def test_a_notify_alert_is_recorded_once_and_matches_the_sent_alert(tmp_path: Path) -> None:
    """#136: the alert sent for a REVIEW month is the one AnomalyDetected row in the ledger."""
    db = write_september(tmp_path / "l.sqlite3", priced=False)
    notifier = _Recorder()

    _run(["2026-09", "--db", str(db), "--notify"], notifier=notifier)

    rows = recorded_anomalies(db)
    (sent,) = notifier.sent
    assert [(r["source"], r["reason"], r["correlation_id"]) for r in rows] == [
        (
            getattr(sent, "source", None),
            getattr(sent, "reason", None),
            sent.correlation_id,
        )
    ]


def test_a_notify_alert_is_recorded_even_without_telegram(tmp_path: Path) -> None:
    """#136: no channel configured still leaves the alert in the ledger."""
    db = write_september(tmp_path / "l.sqlite3", priced=False)

    _run(["2026-09", "--db", str(db), "--notify"], notifier=None)

    assert [r["source"] for r in recorded_anomalies(db)] == ["reconciler"]


def test_a_clean_month_with_notify_records_no_anomaly(tmp_path: Path) -> None:
    """#136: no alert, no AnomalyDetected row."""
    db = write_september(tmp_path / "l.sqlite3")

    _run(["2026-09", "--db", str(db), "--notify"], notifier=_Recorder())

    assert recorded_anomalies(db) == []


def test_a_failed_send_warns_and_keeps_the_exit_code(tmp_path: Path) -> None:
    """A Telegram outage is reported, never mistaken for a verdict."""
    db = write_september(tmp_path / "l.sqlite3", priced=False)

    code, out = _run(["2026-09", "--db", str(db), "--notify"], notifier=_Recorder(fail=True))

    assert (code, "could not be sent" in out) == (1, True)


def test_the_default_factory_builds_telegram_only_with_credentials() -> None:
    """Without HERMES_TELEGRAM__* there is no channel; with them, a Telegram adapter."""
    creds = {
        "HERMES_TELEGRAM__BOT_TOKEN": "123456:test-token",  # gitleaks:allow
        "HERMES_TELEGRAM__CHAT_ID": "42",
    }
    built = [reconcile_module._telegram_notifier(env) for env in ({}, creds)]
    for notifier in built:
        if isinstance(notifier, TelegramAdapter):
            notifier.close()

    assert [type(n) for n in built] == [type(None), TelegramAdapter]


def test_the_command_is_registered() -> None:
    """`hermes reconcile` is discovered from its own module (#457)."""
    command = discover()["reconcile"]

    assert (command.name, command.stub) == ("reconcile", False)
