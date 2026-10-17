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
- with ``allow_mainnet`` the current month is compared with the live
  balances, the reader is closed, and a missing read-only key or an
  unreadable exchange exits 3 instead of falling back to the ledger-only
  check; without it no reader is built (#29);
- the command is registered and reachable through ``hermes``.
"""

from __future__ import annotations

import io
import json
import sqlite3
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from hermes.adapters.notifier.telegram_adapter import TelegramAdapter
from hermes.domain.errors import ExchangeError, InfraError
from hermes.domain.events.base import DomainEvent
from hermes.domain.value_objects.currency import Currency
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


# ---------------------------------------------------------------------------
# Live balances behind allow_mainnet (#29)
# ---------------------------------------------------------------------------

# The September ledger leaves 0.006 BTC and 4659.14 USDT in the trade book.
_BOOK = {Currency.BTC: Decimal("0.006"), Currency.USDT: Decimal("4659.14")}


class _FakeBalances:
    """BalanceReadPort double; integration seam: test_binance_balance_reader.py."""

    def __init__(self, held: Mapping[Currency, Decimal], *, fail: bool = False) -> None:
        self.held = held
        self.fail = fail
        self.closed = False

    def balances(self, assets: Iterable[Currency]) -> Mapping[Currency, Decimal]:
        if self.fail:
            raise ExchangeError(msg="Binance refused /api/v3/account (HTTP 401, code -2015)")
        return {asset: self.held.get(asset, Decimal("0")) for asset in assets}

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def mainnet(monkeypatch: pytest.MonkeyPatch) -> None:
    """Load the config as the prod tier with allow_mainnet set."""
    monkeypatch.setenv("HERMES_ENV", "prod")
    monkeypatch.setenv("HERMES_ALLOW_MAINNET", "yes")


def _run_live(
    args: list[str],
    factory: reconcile_module.BalanceReaderFactory,
    *,
    env: Mapping[str, str] | None = None,
) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = reconcile_module.main(
        ["--config-dir", str(_CONFIG), *args],
        stream=out,
        now=_OCT_4,
        env=env,
        balance_reader_factory=factory,
        err=err,
    )
    return code, out.getvalue(), err.getvalue()


@pytest.mark.usefixtures("mainnet")
def test_with_mainnet_allowed_the_live_balances_are_compared(tmp_path: Path) -> None:
    """An exchange that holds exactly the book reads as matching exchange rows, exit 0."""
    db = write_september(tmp_path / "l.sqlite3")

    code, out, _ = _run_live(
        ["2026-10", "--db", str(db), "--json"], lambda _env: _FakeBalances(_BOOK)
    )

    exchange = [
        (c["asset"], c["observed"], c["status"])
        for c in json.loads(out)["checks"]
        if c["view"] == "exchange"
    ]
    assert (code, exchange) == (0, [("BTC", "0.006", "match"), ("USDT", "4659.14", "match")])


@pytest.mark.usefixtures("mainnet")
def test_a_live_balance_short_of_the_book_is_drift(tmp_path: Path) -> None:
    """0.005 BTC on the exchange against 0.006 in the book is DRIFT, exit 1."""
    db = write_september(tmp_path / "l.sqlite3")
    short = {**_BOOK, Currency.BTC: Decimal("0.005")}

    code, out, _ = _run_live(["2026-10", "--db", str(db)], lambda _env: _FakeBalances(short))

    assert (code, "  exchange BTC" in out, "Verdict: DRIFT" in out) == (1, True, True)


@pytest.mark.usefixtures("mainnet")
def test_the_live_reader_is_closed_after_the_run(tmp_path: Path) -> None:
    """The reader's connection is released once the report is built."""
    db = write_september(tmp_path / "l.sqlite3")
    reader = _FakeBalances(_BOOK)

    _run_live(["2026-10", "--db", str(db)], lambda _env: reader)

    assert reader.closed is True


# SEV-1: no silent fallback when the live check cannot run (AGENTS.md rule 7)
@pytest.mark.usefixtures("mainnet")
def test_with_mainnet_allowed_a_missing_read_key_exits_three(tmp_path: Path) -> None:
    """The default factory with no key prints an ERROR naming both variables, no report."""
    db = write_september(tmp_path / "l.sqlite3")

    code, out, err = _run_live(
        ["2026-10", "--db", str(db)], reconcile_module._binance_balance_reader, env={}
    )

    assert (
        code,
        out,
        err.startswith("ERROR: "),
        "HERMES_BINANCE_READ__API_KEY" in err,
        "HERMES_BINANCE_READ__API_SECRET" in err,
    ) == (3, "", True, True, True)


# SEV-1: no silent fallback when the live check cannot run (AGENTS.md rule 7)
@pytest.mark.usefixtures("mainnet")
def test_an_unreadable_exchange_exits_three_and_closes_the_reader(tmp_path: Path) -> None:
    """A venue refusal is an ERROR line and exit 3, never a ledger-only report."""
    db = write_september(tmp_path / "l.sqlite3")
    reader = _FakeBalances(_BOOK, fail=True)

    code, out, err = _run_live(["2026-10", "--db", str(db)], lambda _env: reader)

    assert (
        code,
        out,
        err.startswith("ERROR: could not read the exchange balances"),
        reader.closed,
    ) == (
        3,
        "",
        True,
        True,
    )


def test_without_mainnet_no_balance_reader_is_built(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Shadow mode never builds a reader and reports the exchange as not compared."""
    monkeypatch.delenv("HERMES_ALLOW_MAINNET", raising=False)
    db = write_september(tmp_path / "l.sqlite3")

    def refuse(_env: Mapping[str, str]) -> reconcile_module.ClosableBalanceReader:
        raise AssertionError("no balance reader may be built without allow_mainnet")

    code, out, _ = _run_live(["2026-10", "--db", str(db)], refuse)

    assert (code, "exchange: no account connected (shadow mode)" in out) == (0, True)


@pytest.mark.usefixtures("mainnet")
def test_an_ended_month_builds_no_reader_and_needs_no_key(tmp_path: Path) -> None:
    """September has ended on 4 Oct, so the live balances are never read and no key is asked for."""
    db = write_september(tmp_path / "l.sqlite3")

    code, out, err = _run_live(
        ["2026-09", "--db", str(db)], reconcile_module._binance_balance_reader, env={}
    )

    assert (code, err, "exchange: balances are current, 2026-09 has ended" in out) == (
        0,
        "",
        True,
    )
