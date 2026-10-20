"""Tests for the tax pack readiness checklist (change 568-tax-pack R-3).

Reference: ADR-016 item 2 (Madrid tax year).
"""

from datetime import date

from hermes.domain.filing.modelo721 import Obligation
from hermes.domain.filing.readiness import PackStatus, Readiness, assess_readiness
from hermes.domain.value_objects.currency import Currency

_CLOSED = date(2027, 1, 5)


def _assess(
    today: date = _CLOSED,
    *,
    pending: int = 0,
    missing: tuple[Currency, ...] = (),
    obligation: Obligation = Obligation.NOT_REQUIRED,
    estimate: bool = False,
    shadow: bool = False,
    campaign: bool = True,
) -> Readiness:
    return assess_readiness(
        2026,
        today,
        pending_sales=pending,
        missing_prices=missing,
        obligation=obligation,
        estimate_included=estimate,
        shadow=shadow,
        campaign_confirmed=campaign,
    )


def test_year_is_open_through_31_december() -> None:
    """Invariant: on 31 December the year can still change, so the pack is open."""
    assert _assess(date(2026, 12, 31)).status is PackStatus.OPEN


def test_closed_year_with_nothing_blocking_is_ready() -> None:
    """Invariant: after 31 December, no pending sale, every price, a known 721: ready."""
    assert _assess().status is PackStatus.READY


def test_a_pending_sale_keeps_a_closed_year_incomplete() -> None:
    """Invariant: a sale waiting for EUR values blocks ready."""
    assert _assess(pending=1).status is PackStatus.INCOMPLETE


def test_a_missing_year_end_price_keeps_a_closed_year_incomplete() -> None:
    """Invariant: a coin held on 31 December with no price blocks ready."""
    assert _assess(missing=(Currency.BTC,)).status is PackStatus.INCOMPLETE


def test_an_undetermined_721_keeps_a_closed_year_incomplete() -> None:
    """Invariant: an undetermined 721 obligation blocks ready."""
    assert _assess(obligation=Obligation.UNDETERMINED).status is PackStatus.INCOMPLETE


def test_advisory_items_never_block_ready() -> None:
    """Invariant: no estimate and a shadow ledger still give ready."""
    assert _assess(estimate=False, shadow=True).status is PackStatus.READY


def test_missing_price_in_an_open_year_waits_for_year_end() -> None:
    """Invariant: during the year the missing price is explained, not an error to fix."""
    items = _assess(date(2026, 10, 4), missing=(Currency.BTC,)).items
    assert "Year-end prices for BTC are set after 31 December." in [i.text for i in items]


def test_pending_sales_point_at_the_backfill() -> None:
    """Invariant: the checklist names the command that clears pending EUR values."""
    items = _assess(pending=2).items
    assert "2 sale(s) wait for EUR values: run `hermes fx backfill`." in [i.text for i in items]


def test_shadow_adds_the_no_real_tax_note() -> None:
    """Invariant: a shadow pack says no real tax is due."""
    texts = [i.text for i in _assess(shadow=True).items]
    assert "Shadow ledger (simulated fills): no real tax is due on these figures." in texts


def test_unconfirmed_renta_boxes_keep_a_closed_year_incomplete() -> None:
    """Invariant (D-6): a closed year never freezes on another campaign's box numbers."""
    assert _assess(campaign=False).status is PackStatus.INCOMPLETE
