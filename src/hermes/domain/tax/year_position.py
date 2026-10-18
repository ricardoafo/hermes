"""Realised gains and losses of hermes by tax year.

The tax year is the calendar year in Europe/Madrid time (ADR-016 decision 2):
a sale at 23:30 UTC on 31 December falls in the next year. Each sale is one
transmission; its gain is the sum of the FIFO consumptions it drew, fees
included (ADR-016 decision 3, computed upstream by the EUR FIFO replay).

Reference: ADR-016 decisions 2, 3 and 6.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Final

from hermes.domain.tax.precision import exact
from hermes.domain.tax.tax_calendar import tax_year_of

__all__ = ["Disposal", "YearPosition", "tax_year_of"]

_ZERO: Final[Decimal] = Decimal(0)


@dataclass(frozen=True, slots=True)
class Disposal:
    """One sale (transmission) and its EUR gain, fees included.

    Attributes:
        asset: Asset sold (``"BTC"``).
        ref: Stable id of the sale (the ledger trade id).
        at: Time of the sale, timezone-aware.
        gain: Transmission value minus the acquisition value it consumed; negative is a loss.
        consumed_refs: Refs of the BUYs whose lots this sale drew from.
    """

    asset: str
    ref: str
    at: datetime
    gain: Decimal
    consumed_refs: frozenset[str]

    def __post_init__(self) -> None:
        """Reject a naive sale time.

        Raises:
            ValueError: When ``at`` is naive.
        """
        if self.at.tzinfo is None:
            raise ValueError(f"Disposal.at must be timezone-aware, got {self.at!r}")

    @property
    def tax_year(self) -> int:
        """Return the Madrid tax year of the sale."""
        return tax_year_of(self.at)


@dataclass(frozen=True, slots=True)
class YearPosition:
    """Hermes gains and losses of one tax year.

    Attributes:
        year: Tax year.
        gains: Sum of the positive sale gains.
        losses: Sum of the sale losses, as a non-negative magnitude.
    """

    year: int
    gains: Decimal
    losses: Decimal

    def __post_init__(self) -> None:
        """Reject negative totals.

        Raises:
            ValueError: When ``gains`` or ``losses`` is negative.
        """
        if self.gains < _ZERO or self.losses < _ZERO:
            raise ValueError("YearPosition.gains and losses are non-negative magnitudes")

    @property
    def net(self) -> Decimal:
        """Return ``gains - losses``, the hermes net of the year (ADR-016 decision 6)."""
        with exact():
            return self.gains - self.losses

    @classmethod
    def of(cls, year: int, disposals: Iterable[Disposal]) -> YearPosition:
        """Total the sales that fall in ``year``; other years are ignored.

        Args:
            year: Tax year.
            disposals: Sales of any year.

        Returns:
            The year's gains and losses.
        """
        gains = _ZERO
        losses = _ZERO
        with exact():
            for disposal in disposals:
                if disposal.tax_year != year:
                    continue
                if disposal.gain >= _ZERO:
                    gains += disposal.gain
                else:
                    losses -= disposal.gain
        return cls(year=year, gains=gains, losses=losses)
