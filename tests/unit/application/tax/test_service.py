"""Tests for TaxService over a real in-memory SqliteLedger (change tax-estimates).

Reference: ADR-016 decisions 3 and 7; change trade-eur-values (EUR trade rows).
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from decimal import Decimal

from hermes.adapters.ledger.sqlite_ledger import SqliteLedger
from hermes.application.console.views import TaxEstimate, TaxPending
from hermes.application.tax.estimate import TaxService, TaxYearEstimate
from hermes.domain.tax.netting import TaxpayerYear
from hermes.domain.trading.order import OrderSide
from tests.unit.adapters.ledger.test_sqlite_trade_ledger import _record, _trade
from tests.unit.domain.tax.scales import SCALE_2026

_AS_OF = datetime(2026, 1, 2, 18, tzinfo=UTC)


def _service() -> tuple[TaxService, SqliteLedger]:
    ledger = SqliteLedger(sqlite3.connect(":memory:"))
    return TaxService(ledger), ledger


def _run(service: TaxService) -> TaxYearEstimate | TaxPending:
    return service.estimate(
        year=2026,
        scales={2026: SCALE_2026},
        taxpayer=TaxpayerYear(),
        default_personal_minimum=Decimal(0),
        prices={},
        as_of=_AS_OF,
        is_shadow=True,
    )


def test_service_estimates_from_the_ledger_with_fees_in_the_values() -> None:
    """Buy at 125 USDT + fee 1.25, sell at 150 - 1.25 at 1.25 USD/EUR: gain 18 EUR, tax 3.42."""
    service, ledger = _service()
    _record(ledger, _trade(OrderSide.BUY, "1", "125", "1.25", minutes=0))
    _record(ledger, _trade(OrderSide.SELL, "1", "150", "1.25", minutes=10))
    estimate = _run(service)
    assert isinstance(estimate, TaxYearEstimate)
    assert (estimate.realised_gain, estimate.hermes_tax) == (
        Decimal("18"),
        TaxEstimate(Decimal("3.42")),
    )


def test_service_reports_pending_ledger_rows() -> None:
    """A fill stored while FX was down keeps the year pending until backfilled."""
    service, ledger = _service()
    _record(ledger, _trade(OrderSide.BUY, "1", "125", "1.25", minutes=0, priced=False))
    assert _run(service) == TaxPending(
        label="tax pending: EUR values pending for 1 trade(s) in 2026"
    )
