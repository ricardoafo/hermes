"""Tests for TaxService over a real in-memory SqliteLedger (change tax-estimates).

Reference: ADR-016 decisions 3 and 7; change trade-eur-values (EUR trade rows).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from hermes.adapters.ledger.sqlite_ledger import SqliteLedger
from hermes.application.console.views import TaxEstimate, TaxPending
from hermes.application.tax.estimate import TaxService, TaxYearEstimate
from hermes.domain.tax.netting import TaxpayerYear
from hermes.domain.trading.order import OrderSide
from tests.unit.adapters.ledger.test_sqlite_capital_seed import _pricing, _record_seed
from tests.unit.adapters.ledger.test_sqlite_capital_seed import _record as _record_usdt
from tests.unit.adapters.ledger.test_sqlite_capital_seed import _trade as _usdt_trade
from tests.unit.adapters.ledger.test_sqlite_trade_ledger import _record, _trade
from tests.unit.domain.tax.scales import SCALE_2026

_AS_OF = datetime(2026, 1, 2, 18, tzinfo=UTC)


@pytest.fixture
def ledger() -> Iterator[SqliteLedger]:
    """Yield a ledger on an in-memory database, closed after the test."""
    conn = sqlite3.connect(":memory:")
    yield SqliteLedger(conn)
    conn.close()


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


def test_service_estimates_from_the_ledger_with_fees_in_the_values(ledger: SqliteLedger) -> None:
    """Buy at 125 USDT + fee 1.25, sell at 150 - 1.25 at 1.25 USD/EUR: gain 18 EUR, tax 3.42."""
    service = TaxService(ledger)
    _record(ledger, _trade(OrderSide.BUY, "1", "125", "1.25", minutes=0))
    _record(ledger, _trade(OrderSide.SELL, "1", "150", "1.25", minutes=10))
    estimate = _run(service)
    assert isinstance(estimate, TaxYearEstimate)
    assert (estimate.realised_gain, estimate.hermes_tax) == (
        Decimal("18"),
        TaxEstimate(Decimal("3.42")),
    )


def test_service_reports_pending_ledger_rows(ledger: SqliteLedger) -> None:
    """A fill stored while FX was down keeps the year pending until backfilled."""
    service = TaxService(ledger)
    _record(ledger, _trade(OrderSide.BUY, "1", "125", "1.25", minutes=0, priced=False))
    assert _run(service) == TaxPending(
        label="tax pending: EUR values pending for 1 trade(s) in 2026"
    )


# ---------------------------------------------------------------------------
# USDT as its own FIFO asset (ADR-016 item 4, change 405-usdt-fifo)
# ---------------------------------------------------------------------------

_SEEDED_AS_OF = datetime(2026, 10, 4, 12, tzinfo=UTC)


def _seeded_ledger(ledger: SqliteLedger, *, seed_priced: bool = True) -> None:
    """Seed 1,000 USDT at 1.25 USD/EUR, then BUY 500, SELL 600 at 1.20, BUY 400 at 1.00."""
    seed_id = _record_seed(ledger)
    if seed_priced:
        ledger.apply_seed_eur(seed_id, _pricing("1.25"))
    _record_usdt(ledger, _usdt_trade(OrderSide.BUY, "1", "500", "0", minutes=1, rate="1.25"))
    _record_usdt(ledger, _usdt_trade(OrderSide.SELL, "1", "600", "0", minutes=2, rate="1.20"))
    _record_usdt(ledger, _usdt_trade(OrderSide.BUY, "1", "400", "0", minutes=3, rate="1.00"))


def _estimate_seeded(
    ledger: SqliteLedger, prices: dict[str, Decimal]
) -> TaxYearEstimate | TaxPending:
    return TaxService(ledger).estimate(
        year=2026,
        scales={2026: SCALE_2026},
        taxpayer=TaxpayerYear(),
        default_personal_minimum=Decimal(0),
        prices=prices,
        as_of=_SEEDED_AS_OF,
        is_shadow=True,
    )


def test_usdt_leg_gains_count_in_the_year(ledger: SqliteLedger) -> None:
    """EUR/USD drift on spent USDT is a realised gain, taxed with the BTC gain.

    Seed: 1,000 USDT at 1.25 USD/EUR, 0.80 EUR per unit (800 EUR).
    BUY 1 BTC for 500 USDT at 1.25: BTC cost 400 EUR; 500 USDT disposed at 0.80
    against seed units at 0.80: gain 0.
    SELL 1 BTC for 600 USDT at 1.20: 500 EUR against 400: BTC gain 100.
    BUY 1 BTC for 400 USDT at 1.00: 400 seed units (cost 320) disposed at 1.00
    (400 EUR): USDT gain 80.
    Realised 100 + 80 = 180; tax 180 * 0.19 = 34.20 (19.00 without the USDT leg).
    """
    _seeded_ledger(ledger)
    estimate = _estimate_seeded(ledger, {})
    assert isinstance(estimate, TaxYearEstimate)
    assert (estimate.realised_gain, estimate.hermes_tax) == (
        Decimal("180"),
        TaxEstimate(Decimal("34.20")),
    )


def test_pending_seed_makes_the_year_pending(ledger: SqliteLedger) -> None:
    """Until the seed is priced its USDT lot has no EUR cost, so no figure is a guess."""
    _seeded_ledger(ledger, seed_priced=False)
    assert _estimate_seeded(ledger, {}) == TaxPending(
        label="tax pending: EUR values pending for 1 trade(s) in 2026"
    )


def test_tax_if_sold_now_leaves_the_quote_currency_out(ledger: SqliteLedger) -> None:
    """Selling the open BTC lot receives USDT and disposes of none: only BTC counts.

    Open BTC lot cost 400 EUR, price 500 EUR: unrealised 100, tax 100 * 0.19 = 19.
    No USDT price is needed although USDT lots are open.
    """
    _seeded_ledger(ledger)
    estimate = _estimate_seeded(ledger, {"BTC": Decimal("500")})
    assert isinstance(estimate, TaxYearEstimate)
    assert estimate.tax_if_sold_now == TaxEstimate(Decimal("19"))
    assert [p.asset for p in estimate.per_position_if_sold_now] == ["BTC"]


def test_usdt_losses_are_not_two_month_flagged(ledger: SqliteLedger) -> None:
    """The two-month rule flags traded coins only, not the quote currency (decision 2026-10-04).

    Seed 1,000 USDT at 1.25 (0.80 EUR per unit). BUY 1 BTC for 500 USDT at 1.50 disposes of
    500 USDT at 0.6667 EUR against 0.80: a USDT loss. SELL 1 BTC for 500 USDT a minute later
    re-acquires USDT, which would flag USDT under a same-coin reading; it must not.
    """
    seed_id = _record_seed(ledger)
    ledger.apply_seed_eur(seed_id, _pricing("1.25"))
    _record_usdt(ledger, _usdt_trade(OrderSide.BUY, "1", "500", "0", minutes=1, rate="1.50"))
    _record_usdt(ledger, _usdt_trade(OrderSide.SELL, "1", "500", "0", minutes=2, rate="1.50"))
    estimate = _estimate_seeded(ledger, {})
    assert isinstance(estimate, TaxYearEstimate)
    assert [line for line in estimate.flags if line.startswith("USDT")] == []
