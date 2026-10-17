"""Synthetic ledger rows for the Tax screen under ``--demo`` (issue #719).

Every demo scenario shows the same synthetic year: three coins bought and
sold over the months before the read time, one SOL loss with a SOL buy three
weeks later (so the two-month rule flags it) and three lots still open. The
rows are dated back from the read time, so the year on screen is always the
current one. The demo taxpayer earns a synthetic general income of
:data:`DEMO_GENERAL_INCOME`, so the savings base is taxed from its first euro.
Nothing here reads the ledger or the real taxpayer profile.

Reference: openspec/changes/719-tax-screen/change.md.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Final

from hermes.application.console.views import TaxPending
from hermes.application.tax.screen import TaxpayerProfileView, TaxScreen, TaxScreenService
from hermes.domain.tax.netting import TaxpayerYear
from hermes.domain.tax.scale import SavingsScale
from hermes.domain.tax.year_position import tax_year_of
from hermes.domain.trading.order import OrderSide
from hermes.domain.value_objects.currency import Currency
from hermes.ports.ledger import EurTradeRow

__all__ = [
    "DEMO_GENERAL_INCOME",
    "DEMO_PRICES",
    "DemoTaxSource",
    "demo_profile",
    "demo_trades",
]

DEMO_GENERAL_INCOME: Final[Decimal] = Decimal(30000)
"""The synthetic taxpayer's general income, EUR."""

DEMO_PRICES: Final[Mapping[str, Decimal]] = {
    "BTC": Decimal(62000),
    "ETH": Decimal(2700),
    "SOL": Decimal(150),
}
"""Synthetic EUR prices the demo values its open lots at."""

# (days before the read time, ref, coin, side, qty, EUR price, EUR fee)
_ROWS: Final = (
    (200, "demo-1", "BTC", OrderSide.BUY, "0.010", "55000", "0.55"),
    (170, "demo-2", "ETH", OrderSide.BUY, "0.20", "2400", "0.48"),
    (120, "demo-3", "BTC", OrderSide.SELL, "0.010", "61000", "0.61"),
    (110, "demo-4", "SOL", OrderSide.BUY, "2", "160", "0.32"),
    (80, "demo-5", "SOL", OrderSide.SELL, "2", "140", "0.28"),
    (59, "demo-6", "SOL", OrderSide.BUY, "1.5", "135", "0.20"),
    (50, "demo-7", "ETH", OrderSide.SELL, "0.20", "2600", "0.52"),
    (10, "demo-8", "BTC", OrderSide.BUY, "0.008", "60100", "0.48"),
    (3, "demo-9", "ETH", OrderSide.BUY, "0.10", "2650", "0.27"),
)


def demo_trades(as_of: datetime) -> tuple[EurTradeRow, ...]:
    """Return the synthetic rows, dated back from ``as_of``.

    Args:
        as_of: The read time.

    Returns:
        The rows, oldest first.
    """
    return tuple(
        EurTradeRow(
            ref=ref,
            asset=Currency(coin),
            side=side,
            qty=Decimal(qty),
            executed_at=as_of - timedelta(days=days),
            price_eur=Decimal(price),
            fee_eur=Decimal(fee),
        )
        for days, ref, coin, side, qty, price, fee in _ROWS
    )


def demo_profile() -> TaxpayerProfileView:
    """Return the synthetic taxpayer profile the demo's Your profile shows (masked)."""
    return TaxpayerProfileView(exists=True, figures=(("general income", DEMO_GENERAL_INCOME),))


@dataclass(frozen=True, slots=True)
class _Rows:
    rows: tuple[EurTradeRow, ...]

    def list_eur_trades(self) -> tuple[EurTradeRow, ...]:
        return self.rows


class DemoTaxSource:
    """The Tax screen over the synthetic rows, through the real tax service.

    Args:
        scales: Savings scale per tax year.
        default_personal_minimum: Personal minimum of the synthetic taxpayer.
    """

    def __init__(
        self, *, scales: Mapping[int, SavingsScale], default_personal_minimum: Decimal
    ) -> None:
        """Remember the scales."""
        self._scales = scales
        self._minimum = default_personal_minimum

    def tax_screen(
        self, *, as_of: datetime, prices: Mapping[str, Decimal]
    ) -> TaxScreen | TaxPending:
        """Return the synthetic year's screen, valued at :data:`DEMO_PRICES`.

        Args:
            as_of: The read time.
            prices: Ignored: the demo lots are not the scenario's positions.

        Returns:
            The screen, or why there is none (a scale missing for the year).
        """
        del prices
        return TaxScreenService(
            _Rows(demo_trades(as_of)),
            scales=self._scales,
            taxpayer={
                tax_year_of(as_of): TaxpayerYear(
                    general_income=DEMO_GENERAL_INCOME, personal_minimum=self._minimum
                )
            },
            default_personal_minimum=self._minimum,
        ).tax_screen(as_of=as_of, prices=DEMO_PRICES)
