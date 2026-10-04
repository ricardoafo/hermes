"""Monthly reconciliation — compare views of the account and read the month's tax state.

Public surface:

- :class:`~hermes.domain.reconciliation.month.ReconciliationMonth`    — a Madrid calendar month
- :class:`~hermes.domain.reconciliation.holdings.LedgerTrade`         — one trades-table row
- :func:`~hermes.domain.reconciliation.holdings.book_balances`        — what the book holds
- :func:`~hermes.domain.reconciliation.holdings.compare_holdings`     — per-asset checks
- :func:`~hermes.domain.reconciliation.tax_view.tax_view`             — month P&L and open lots
- :class:`~hermes.domain.reconciliation.report.ReconciliationReport` — checks plus verdict

Pure Python, no I/O. Read-only by nature: nothing here changes a ledger.

Reference: openspec/changes/35-monthly-reconciliation/change.md.
"""

from hermes.domain.reconciliation.holdings import (
    CheckStatus,
    HoldingCheck,
    LedgerTrade,
    book_balances,
    compare_holdings,
)
from hermes.domain.reconciliation.month import ReconciliationMonth
from hermes.domain.reconciliation.report import ReconciliationReport, Verdict
from hermes.domain.reconciliation.tax_view import MonthPnl, OpenLotLine, TaxView, tax_view

__all__ = [
    "CheckStatus",
    "HoldingCheck",
    "LedgerTrade",
    "MonthPnl",
    "OpenLotLine",
    "ReconciliationMonth",
    "ReconciliationReport",
    "TaxView",
    "Verdict",
    "book_balances",
    "compare_holdings",
    "tax_view",
]
