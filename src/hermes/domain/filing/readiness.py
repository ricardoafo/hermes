"""Whether a tax year's filing pack is ready to hand to the gestor.

A pack is ``open`` until the Madrid tax year ends (ADR-016 item 2), because
every trade still changes it. After that it is ``incomplete`` while a sale
waits for EUR values, a coin held on 31 December has no price, the 721
obligation is undetermined, or the year's own Renta Web boxes are not yet
configured, and ``ready`` otherwise. Advisory items (the
estimate, the gestor defaults, the shadow label) never change the status.

Reference: openspec/changes/568-tax-pack/change.md D-2, R-3.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from hermes.domain.filing.modelo721 import Obligation
from hermes.domain.value_objects.currency import Currency

__all__ = ["CheckItem", "PackStatus", "Readiness", "assess_readiness"]


class PackStatus(StrEnum):
    """Where a tax year's pack stands."""

    OPEN = "open"
    INCOMPLETE = "incomplete"
    READY = "ready"


@dataclass(frozen=True, slots=True)
class CheckItem:
    """One line of the readiness checklist.

    Attributes:
        done: Whether the item is satisfied.
        text: What the operator reads.
        blocking: Whether an unsatisfied item keeps a closed year from ``ready``.
    """

    done: bool
    text: str
    blocking: bool


@dataclass(frozen=True, slots=True)
class Readiness:
    """The checklist and the status it gives.

    Attributes:
        status: Where the pack stands.
        items: Checklist, blocking items first.
    """

    status: PackStatus
    items: tuple[CheckItem, ...]


def assess_readiness(
    year: int,
    today: date,
    *,
    pending_sales: int,
    missing_prices: Sequence[Currency],
    obligation: Obligation,
    estimate_included: bool,
    shadow: bool,
    campaign_confirmed: bool,
) -> Readiness:
    """Return the readiness of ``year``'s pack on ``today``.

    Args:
        year: Tax year.
        today: Madrid date of the run.
        pending_sales: Sales left out of Modelo 100 for pending EUR values.
        missing_prices: Coins held on 31 December with no EUR price.
        obligation: The 721 obligation as computed.
        estimate_included: Whether the pack holds a tax estimate from a local profile.
        shadow: Whether the ledger holds simulated fills.
        campaign_confirmed: Whether the year's own Renta Web boxes are configured.

    Returns:
        The checklist and status.
    """
    closed = today > date(year, 12, 31)
    blocking = (
        _year_item(year, closed),
        _pending_item(pending_sales),
        _price_item(missing_prices, closed),
        CheckItem(
            done=obligation is not Obligation.UNDETERMINED,
            text=f"Modelo 721: {obligation.value}.",
            blocking=True,
        ),
        _campaign_item(year, campaign_confirmed),
    )
    advisory = [
        CheckItem(
            done=estimate_included,
            text="Tax estimate included from your local taxpayer profile."
            if estimate_included
            else "No tax estimate: run `hermes tax-pack` where your taxpayer profile lives.",
            blocking=False,
        ),
        CheckItem(
            done=False,
            text="Confirm the defaults listed in report.md with your gestor.",
            blocking=False,
        ),
    ]
    if shadow:
        advisory.append(
            CheckItem(
                done=False,
                text="Shadow ledger (simulated fills): no real tax is due on these figures.",
                blocking=False,
            )
        )
    if not closed:
        status = PackStatus.OPEN
    elif all(item.done for item in blocking):
        status = PackStatus.READY
    else:
        status = PackStatus.INCOMPLETE
    return Readiness(status=status, items=(*blocking, *advisory))


def _year_item(year: int, closed: bool) -> CheckItem:
    if closed:
        return CheckItem(done=True, text=f"Tax year {year} has ended.", blocking=True)
    return CheckItem(
        done=False,
        text=f"Tax year {year} is still open: figures change with every trade until 31 December.",
        blocking=True,
    )


def _campaign_item(year: int, confirmed: bool) -> CheckItem:
    if confirmed:
        return CheckItem(
            done=True, text=f"Renta Web boxes for {year} are confirmed.", blocking=True
        )
    return CheckItem(
        done=False,
        text=f"Renta Web boxes for {year} are not confirmed yet: add them to "
        "config/renta_campaigns.yaml once AEAT publishes the campaign.",
        blocking=True,
    )


def _pending_item(pending_sales: int) -> CheckItem:
    if pending_sales == 0:
        return CheckItem(done=True, text="Every sale has its EUR values.", blocking=True)
    return CheckItem(
        done=False,
        text=f"{pending_sales} sale(s) wait for EUR values: run `hermes fx backfill`.",
        blocking=True,
    )


def _price_item(missing: Sequence[Currency], closed: bool) -> CheckItem:
    if not missing:
        return CheckItem(
            done=True, text="Every coin held on 31 December has a price.", blocking=True
        )
    coins = ", ".join(asset.value for asset in missing)
    if not closed:
        return CheckItem(
            done=False,
            text=f"Year-end prices for {coins} are set after 31 December.",
            blocking=True,
        )
    return CheckItem(
        done=False,
        text=f"No year-end price for {coins}: rerun with `--price COIN=EUR`.",
        blocking=True,
    )
