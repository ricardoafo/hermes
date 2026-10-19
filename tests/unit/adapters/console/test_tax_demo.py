"""Tests for the Tax screen's synthetic demo rows (issue #719).

Reference: openspec/archive/719-tax-screen/change.md (R-6).
"""

from datetime import UTC, datetime
from decimal import Decimal

from hermes.adapters.console.tax_demo import (
    DEMO_GENERAL_INCOME,
    DemoTaxSource,
    demo_profile,
    demo_trades,
)
from hermes.application.console.views import TaxEstimate, TaxPending
from hermes.application.tax.screen import TaxScreen
from tests.unit.domain.tax.scales import SCALE_2026

_NOW = datetime(2026, 10, 3, 12, 12, tzinfo=UTC)


def _screen() -> TaxScreen:
    screen = DemoTaxSource(
        scales={2026: SCALE_2026}, default_personal_minimum=Decimal(5550)
    ).tax_screen(as_of=_NOW, prices={"BTC": Decimal(1)})
    assert isinstance(screen, TaxScreen)
    return screen


def test_demo_rows_are_dated_back_from_the_read_time() -> None:
    """Invariant: every demo row is before the read time, the newest three days before it."""
    rows = demo_trades(_NOW)
    assert all(row.executed_at < _NOW for row in rows)
    assert max(row.executed_at for row in rows) == datetime(2026, 9, 30, 12, 12, tzinfo=UTC)


def test_demo_year_flags_only_the_sol_loss() -> None:
    """Invariant: the demo's one two-month flag is the SOL loss of 40.60 EUR."""
    flags = _screen().estimate.two_month
    assert [(f.asset, f.sale_ref, f.loss) for f in flags] == [("SOL", "demo-5", Decimal("40.60"))]


def test_demo_figures_come_from_the_tax_service() -> None:
    """Invariant: the demo's realised gain and fees are the replay's, to the cent."""
    screen = _screen()
    assert screen.estimate.realised_gain == Decimal("57.24")
    assert screen.fees == Decimal("3.71")
    assert isinstance(screen.estimate.hermes_tax, TaxEstimate)
    assert screen.estimate.hermes_tax.amount.quantize(Decimal("0.01")) == Decimal("10.88")


def test_demo_values_open_lots_at_its_own_prices() -> None:
    """Invariant: the scenario's prices are ignored, so "open if sold now" is never pending."""
    if_sold = _screen().estimate.tax_if_sold_now
    assert isinstance(if_sold, TaxEstimate)
    assert if_sold.amount.quantize(Decimal("0.01")) == Decimal("7.93")


def test_demo_profile_is_synthetic() -> None:
    """Invariant: the demo profile shows the synthetic income and reads no file."""
    profile = demo_profile()
    assert profile.exists is True
    assert profile.figures == (("general income", DEMO_GENERAL_INCOME),)


def test_demo_without_a_scale_is_pending() -> None:
    """Invariant: no scale for the year makes the demo screen the service's pending notice."""
    screen = DemoTaxSource(scales={}, default_personal_minimum=Decimal(0)).tax_screen(
        as_of=_NOW, prices={}
    )
    assert screen == TaxPending(label="tax pending: scale missing for 2026")
