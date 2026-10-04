"""Tests for picking a tax year's Renta Web layout (change 568-tax-pack D-6)."""

from __future__ import annotations

from datetime import date

from hermes.domain.filing.renta_campaign import CampaignLayout, RentaCampaign, campaign_for

_L2024 = CampaignLayout(
    tax_year=2024, transmissions_box="1800", source="s", verified_on=date(2025, 4, 1)
)
_L2025 = CampaignLayout(
    tax_year=2025, transmissions_box="1804", source="s", verified_on=date(2026, 4, 1)
)


def test_a_year_with_its_own_layout_is_not_provisional() -> None:
    """Invariant: a configured year uses its own boxes."""
    campaign = campaign_for(2025, [_L2024, _L2025])
    assert campaign is not None
    assert (campaign.layout, campaign.provisional) == (_L2025, False)


def test_an_unpublished_year_borrows_the_latest_earlier_layout_provisionally() -> None:
    """Invariant: before AEAT publishes a campaign, the last known boxes are used and flagged."""
    campaign = campaign_for(2026, [_L2024, _L2025])
    assert campaign is not None
    assert (campaign.layout, campaign.provisional) == (_L2025, True)


def test_never_uses_a_later_campaign() -> None:
    """Invariant: a year is never filed with boxes from a later campaign."""
    campaign = campaign_for(2024, [_L2025, _L2024])
    assert campaign is not None
    assert campaign.layout == _L2024


def test_no_layout_at_or_before_the_year_gives_none() -> None:
    """Invariant: with nothing known, there is no box to print."""
    found = {year: campaign_for(year, [_L2024]) for year in (2023, 2024)}
    assert found == {
        2023: None,
        2024: RentaCampaign(tax_year=2024, layout=_L2024, provisional=False),
    }
