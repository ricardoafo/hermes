"""Tests for the reconciliation verdict (change 35-monthly-reconciliation D-5).

Invariants protected here:

- any drift, any problem or an unreplayable book is DRIFT;
- with views in agreement, pending EUR values are REVIEW;
- only a CLEAN verdict on an ended month is ready for the month-end cut.
"""

from datetime import datetime, timedelta
from decimal import Decimal

from hermes.domain.reconciliation.holdings import HoldingCheck
from hermes.domain.reconciliation.month import ReconciliationMonth
from hermes.domain.reconciliation.report import ReconciliationReport, Verdict
from hermes.domain.reconciliation.tax_view import MonthPnl, TaxView
from hermes.domain.value_objects.currency import Currency

_MONTH = ReconciliationMonth(2026, 9)
_ZERO = Decimal(0)


def _tax(pending: int = 0) -> TaxView:
    pnl = MonthPnl(
        buys=0,
        sells=0,
        proceeds_eur=_ZERO,
        cost_eur=_ZERO,
        fees_eur=_ZERO,
        realized_eur=_ZERO,
    )
    return TaxView(pnl=pnl, open_lots=(), pending_trades=pending)


def _check(observed: str) -> HoldingCheck:
    return HoldingCheck(
        source="events",
        asset=Currency.BTC,
        book=Decimal(1),
        observed=Decimal(observed),
        tolerance=Decimal("1e-8"),
    )


_DEFAULT_TAX = _tax()


def _report(
    *,
    as_of: datetime = _MONTH.end,
    checks: tuple[HoldingCheck, ...] = (_check("1"),),
    tax: TaxView | None = _DEFAULT_TAX,
    problems: tuple[str, ...] = (),
    skipped: tuple[str, ...] = (),
) -> ReconciliationReport:
    return ReconciliationReport(
        month=_MONTH, as_of=as_of, checks=checks, tax=tax, problems=problems, skipped=skipped
    )


def test_agreeing_views_with_eur_values_are_clean_and_ready() -> None:
    """Everything matches on an ended month: CLEAN and ready for the cut."""
    report = _report()

    assert (report.verdict, report.ready_for_cut) == (Verdict.CLEAN, True)


def test_one_drift_is_drift() -> None:
    """A single asset off beyond the tolerance blocks the cut."""
    report = _report(checks=(_check("1"), _check("0.9")))

    assert (report.verdict, report.drifts, report.ready_for_cut) == (
        Verdict.DRIFT,
        (_check("0.9"),),
        False,
    )


def test_a_problem_without_any_drift_is_drift() -> None:
    """A finding that is not a per-asset number still means drift."""
    assert _report(problems=("events: unseeded strategy",)).verdict is Verdict.DRIFT


def test_an_unreplayable_book_is_drift() -> None:
    """No tax view means the book itself is inconsistent."""
    assert _report(tax=None).verdict is Verdict.DRIFT


def test_pending_eur_values_are_review() -> None:
    """Agreeing views with a trade awaiting its rate: REVIEW, not ready."""
    report = _report(tax=_tax(pending=2))

    assert (report.verdict, report.ready_for_cut) == (Verdict.REVIEW, False)


def test_a_clean_month_in_progress_is_not_ready_for_the_cut() -> None:
    """The current month can be CLEAN but is never cut before it ends."""
    report = _report(as_of=_MONTH.end - timedelta(days=3))

    assert (report.verdict, report.month_closed, report.ready_for_cut) == (
        Verdict.CLEAN,
        False,
        False,
    )


def test_skipped_views_do_not_change_the_verdict() -> None:
    """A view not compared is reported, not counted as drift."""
    assert _report(skipped=("exchange: no account",)).verdict is Verdict.CLEAN
