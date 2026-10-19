"""Tests for the monthly reconciliation use case (change 35-monthly-reconciliation).

The ledger side is the real read-only reader over a ledger written through
SqliteLedger (integration seam: tests/unit/adapters/ledger/test_reconciliation_reader.py);
the exchange is a stub of BalanceReadPort, since no live adapter exists yet.

Invariants protected here:

- a ledger written through record_trade reconciles CLEAN in every view;
- the event replay and the lot table each detect drift on their own;
- a past month is read at its end; later fills skip the lot table, not the events;
- the exchange is compared only for the current month;
- events of an unseeded strategy and an unreplayable book are problems;
- the alert is None when clean, INFO for pending EUR values, WARNING for drift;
- the pushed alert carries no amount (pushed surfaces stay blind to shadow money).
"""

import sqlite3
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from hermes.adapters.ledger.reconciliation_reader import SqliteReconciliationReader
from hermes.adapters.ledger.sqlite_ledger import SqliteLedger
from hermes.application.reconciliation import (
    StrategySeed,
    reconcile_month,
    reconciliation_alert,
)
from hermes.domain.events.anomaly_detected import AnomalySeverity
from hermes.domain.reconciliation import CheckStatus, ReconciliationMonth, ReconciliationReport
from hermes.domain.reconciliation.report import Verdict
from hermes.domain.trading.order import OrderSide
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.strategy_id import StrategyId
from tests.support.reconciliation_ledger import STRATEGY, record, write_september

_SEPT = ReconciliationMonth(2026, 9)
_AFTER_SEPT = datetime(2026, 10, 4, tzinfo=UTC)
_MID_SEPT = datetime(2026, 9, 25, tzinfo=UTC)
_SEEDS = (StrategySeed(STRATEGY, Money(amount=Decimal(5000), currency=Currency.USDT)),)
_TOL = Decimal("1e-8")


class _Exchange:
    """BalanceReadPort stub returning fixed balances."""

    def __init__(self, held: Mapping[Currency, Decimal]) -> None:
        self.held = dict(held)
        self.asked: list[Currency] = []

    def balances(self, assets: Iterable[Currency]) -> Mapping[Currency, Decimal]:
        self.asked = list(assets)
        return {a: self.held.get(a, Decimal(0)) for a in self.asked}


def _run(
    path: Path, *, now: datetime = _AFTER_SEPT, exchange: _Exchange | None = None
) -> ReconciliationReport:
    with SqliteReconciliationReader.open(path) as reader:
        return reconcile_month(
            reader, month=_SEPT, seeds=_SEEDS, now=now, tolerance=_TOL, exchange=exchange
        )


def _sql(path: Path, statement: str) -> None:
    connection = sqlite3.connect(path)
    connection.execute(statement)
    connection.commit()
    connection.close()


def test_a_consistent_ledger_is_clean_in_every_view(tmp_path: Path) -> None:
    """Book, events and lots agree on 0.006 BTC and 4659.14 USDT."""
    report = _run(write_september(tmp_path / "l.sqlite3"))

    assert report.verdict is Verdict.CLEAN
    assert {(c.source, c.asset, c.book) for c in report.checks} == {
        ("events", Currency.BTC, Decimal("0.006")),
        ("events", Currency.USDT, Decimal("4659.14")),
        ("lots", Currency.BTC, Decimal("0.006")),
    }
    assert all(c.status is CheckStatus.MATCH for c in report.checks)


def test_the_tax_view_reports_the_months_realized_gain(tmp_path: Path) -> None:
    """September realizes 15.6 EUR and closes with one lot of 0.006 BTC."""
    report = _run(write_september(tmp_path / "l.sqlite3"))

    assert report.tax is not None
    assert report.tax.pnl.realized_eur == Decimal("15.6")
    assert [lot.remaining_qty for lot in report.tax.open_lots] == [Decimal("0.006")]


def test_a_tampered_lot_table_is_drift_in_the_lots_view_only(tmp_path: Path) -> None:
    """Lots holding 0.005 instead of 0.006 BTC: lots drifts by -0.001, events still match."""
    path = write_september(tmp_path / "l.sqlite3")
    _sql(path, "UPDATE lots SET remaining_qty = '0.005'")

    report = _run(path)

    assert report.verdict is Verdict.DRIFT
    assert [(c.source, c.asset, c.drift) for c in report.drifts] == [
        ("lots", Currency.BTC, Decimal("-0.001"))
    ]


def test_a_trade_row_without_its_event_is_drift_in_the_events_view(tmp_path: Path) -> None:
    """Deleting the SELL event leaves the replay holding 0.004 BTC more than the book."""
    path = write_september(tmp_path / "l.sqlite3")
    _sql(
        path,
        "DELETE FROM events WHERE event_type = 'TradeExecuted' "
        "AND json_extract(payload_json, '$.side') = 'SELL'",
    )

    report = _run(path)

    drifts = {(c.source, c.asset): c.drift for c in report.drifts}
    assert drifts == {
        ("events", Currency.BTC): Decimal("0.004"),
        ("events", Currency.USDT): Decimal("-259.74"),
    }


def test_a_past_month_ignores_later_fills_and_skips_the_lot_table(tmp_path: Path) -> None:
    """An October fill leaves September's book intact and makes the lot table incomparable."""
    path = write_september(tmp_path / "l.sqlite3")
    connection = sqlite3.connect(path)
    record(
        SqliteLedger(connection),
        OrderSide.SELL,
        "0.006",
        "70000",
        "0",
        datetime(2026, 10, 2, tzinfo=UTC),
    )
    connection.close()

    report = _run(path)

    assert report.verdict is Verdict.CLEAN
    assert {c.source for c in report.checks} == {"events"}
    assert any(s.startswith("lots: 1 trade(s) after 2026-09") for s in report.skipped)
    assert {c.asset: c.book for c in report.checks}[Currency.BTC] == Decimal("0.006")


def test_the_exchange_is_compared_for_the_current_month(tmp_path: Path) -> None:
    """Mid-September, an account holding 0.0059 BTC drifts by -0.0001."""
    exchange = _Exchange({Currency.BTC: Decimal("0.0059"), Currency.USDT: Decimal("4659.14")})

    report = _run(write_september(tmp_path / "l.sqlite3"), now=_MID_SEPT, exchange=exchange)

    assert [(c.source, c.asset, c.drift) for c in report.drifts] == [
        ("exchange", Currency.BTC, Decimal("-0.0001"))
    ]
    assert report.as_of == _MID_SEPT


def test_the_exchange_is_not_compared_for_an_ended_month(tmp_path: Path) -> None:
    """A venue's balances are current, so a past month skips them without asking."""
    exchange = _Exchange({})

    report = _run(write_september(tmp_path / "l.sqlite3"), exchange=exchange)

    assert (exchange.asked, report.verdict) == ([], Verdict.CLEAN)
    assert any(s.startswith("exchange: balances are current") for s in report.skipped)


def test_events_of_an_unseeded_strategy_are_a_problem(tmp_path: Path) -> None:
    """Trades the roster cannot seed cannot be replayed into an account."""
    path = write_september(tmp_path / "l.sqlite3")
    connection = sqlite3.connect(path)
    record(
        SqliteLedger(connection),
        OrderSide.BUY,
        "0.001",
        "60000",
        "0",
        datetime(2026, 9, 21, tzinfo=UTC),
        strategy=StrategyId("retired"),
    )
    connection.close()

    report = _run(path)

    assert report.verdict is Verdict.DRIFT
    assert any("'retired'" in p for p in report.problems)


def test_an_event_replay_that_overdraws_is_a_problem(tmp_path: Path) -> None:
    """A seed too small for the recorded BUY cannot be replayed; the view is dropped."""
    path = write_september(tmp_path / "l.sqlite3")
    tiny = (StrategySeed(STRATEGY, Money(amount=Decimal(1), currency=Currency.USDT)),)

    with SqliteReconciliationReader.open(path) as reader:
        report = reconcile_month(reader, month=_SEPT, seeds=tiny, now=_AFTER_SEPT, tolerance=_TOL)

    assert report.verdict is Verdict.DRIFT
    assert {c.source for c in report.checks} == {"lots"}
    assert any(p.startswith("events:") for p in report.problems)


def test_a_book_selling_more_than_it_holds_is_a_problem(tmp_path: Path) -> None:
    """A SELL row with no lot to consume fails the FIFO replay."""
    path = write_september(tmp_path / "l.sqlite3")
    _sql(path, "UPDATE trades SET qty_amount = '0.02' WHERE side = 'SELL'")

    report = _run(path)

    assert (report.verdict, report.tax) == (Verdict.DRIFT, None)
    assert any(p.startswith("book: FIFO replay failed") for p in report.problems)


def test_a_month_not_started_is_refused(tmp_path: Path) -> None:
    """Reconciling the future would report an empty month as CLEAN."""
    path = write_september(tmp_path / "l.sqlite3")

    with SqliteReconciliationReader.open(path) as reader, pytest.raises(ValueError, match="not"):
        reconcile_month(
            reader,
            month=ReconciliationMonth(2026, 11),
            seeds=_SEEDS,
            now=_AFTER_SEPT,
            tolerance=_TOL,
        )


def test_a_clean_report_raises_no_alert(tmp_path: Path) -> None:
    """Clean months stay off Telegram (ADR-008 not-for-Telegram rule)."""
    report = _run(write_september(tmp_path / "l.sqlite3"))

    assert (report.verdict, reconciliation_alert(report, now=_AFTER_SEPT)) == (Verdict.CLEAN, None)


def test_pending_eur_values_raise_an_info_alert(tmp_path: Path) -> None:
    """Two trades without a rate: INFO, naming the month and the count."""
    report = _run(write_september(tmp_path / "l.sqlite3", priced=False))

    alert = reconciliation_alert(report, now=_AFTER_SEPT)

    assert alert is not None
    assert (alert.source, alert.severity, alert.reason) == (
        "reconciler",
        AnomalySeverity.INFO,
        "2026-09: 2 trade(s) still without EUR values",
    )


def test_drift_raises_a_warning_naming_the_view_and_asset(tmp_path: Path) -> None:
    """A lot table off by -0.001 BTC: WARNING naming lots and BTC, but no amount."""
    path = write_september(tmp_path / "l.sqlite3")
    _sql(path, "UPDATE lots SET remaining_qty = '0.005'")

    alert = reconciliation_alert(_run(path), now=_AFTER_SEPT)

    assert alert is not None
    assert (alert.severity, alert.reason, alert.correlation_id) == (
        AnomalySeverity.WARNING,
        "2026-09 drift: lots BTC disagrees with the book; run `hermes reconcile 2026-09`",
        "reconcile-2026-09",
    )


def test_a_long_drift_list_is_cut_to_three_details() -> None:
    """Five findings: the alert names three and counts the rest, so it fits a phone."""
    report = ReconciliationReport(
        month=_SEPT,
        as_of=_SEPT.end,
        checks=(),
        tax=None,
        problems=tuple(f"p{i}" for i in range(5)),
    )

    alert = reconciliation_alert(report, now=_AFTER_SEPT)

    assert alert is not None
    assert alert.reason == "2026-09 drift: p0; p1; p2; 2 more; run `hermes reconcile 2026-09`"


def test_the_pushed_alert_carries_no_amount(tmp_path: Path) -> None:
    """Drift on BTC and USDT: the reason holds no digit beyond the month, so no money leaks."""
    path = write_september(tmp_path / "l.sqlite3")
    _sql(
        path,
        "DELETE FROM events WHERE event_type = 'TradeExecuted' "
        "AND json_extract(payload_json, '$.side') = 'SELL'",
    )

    alert = reconciliation_alert(_run(path), now=_AFTER_SEPT)

    assert alert is not None
    assert not any(ch.isdigit() for ch in alert.reason.replace("2026-09", ""))
