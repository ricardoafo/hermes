"""Which Renta Web layout a tax year is filed with.

AEAT can renumber boxes between campaigns, so the box numbers live in config,
one entry per tax year (ejercicio), and are checked when each campaign is
published. Until a year's campaign is published, the latest earlier layout is
used and marked provisional.

Reference: openspec/changes/568-tax-pack/change.md D-6.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

__all__ = ["CampaignLayout", "RentaCampaign", "campaign_for"]


@dataclass(frozen=True, slots=True)
class CampaignLayout:
    """The Renta Web boxes of one tax year's campaign.

    Attributes:
        tax_year: Tax year (ejercicio) the campaign files.
        transmissions_box: Box holding one entry per virtual currency transmitted.
        source: Where the box numbers were checked.
        verified_on: When they were checked.
    """

    tax_year: int
    transmissions_box: str
    source: str
    verified_on: date


@dataclass(frozen=True, slots=True)
class RentaCampaign:
    """The layout a tax year's sheet uses.

    Attributes:
        tax_year: Tax year the sheet is for.
        layout: The layout applied.
        provisional: True when ``layout`` belongs to an earlier campaign.
    """

    tax_year: int
    layout: CampaignLayout
    provisional: bool


def campaign_for(year: int, layouts: Iterable[CampaignLayout]) -> RentaCampaign | None:
    """Return the layout to use for ``year``.

    Args:
        year: Tax year.
        layouts: Known campaign layouts.

    Returns:
        That year's layout; else the latest earlier one, provisional; else
        ``None`` when no layout is known for ``year`` or before.
    """
    usable = [layout for layout in layouts if layout.tax_year <= year]
    if not usable:
        return None
    latest = max(usable, key=lambda layout: layout.tax_year)
    return RentaCampaign(tax_year=year, layout=latest, provisional=latest.tax_year != year)
