"""IRPF savings-base scale of one tax year.

The bands come from ``config/tax_scales.yaml`` (ADR-016 decision 1); this module
only applies them. A band's ``up_to`` is the cumulative upper limit of the
band, and only the last band is unbounded.

Reference: ADR-016 decision 1, IRPF art. 66 and art. 76.
"""

from dataclasses import dataclass
from decimal import Decimal
from itertools import pairwise
from typing import Final

from hermes.domain.value_objects.precision import exact

__all__ = ["BandPosition", "SavingsBand", "SavingsScale"]

_ZERO: Final[Decimal] = Decimal(0)
_ONE: Final[Decimal] = Decimal(1)


@dataclass(frozen=True, slots=True)
class SavingsBand:
    """One band of the savings scale.

    Attributes:
        up_to: Cumulative upper limit in EUR; ``None`` for the open top band.
        rate: Marginal rate as a fraction (``0.19`` is 19%).
    """

    up_to: Decimal | None
    rate: Decimal

    def __post_init__(self) -> None:
        """Reject a rate outside ``[0, 1]`` and a non-positive limit.

        Raises:
            ValueError: On either violation.
        """
        if not _ZERO <= self.rate <= _ONE:
            raise ValueError(f"SavingsBand.rate must be in [0, 1], got {self.rate}")
        if self.up_to is not None and self.up_to <= _ZERO:
            raise ValueError(f"SavingsBand.up_to must be > 0, got {self.up_to}")


@dataclass(frozen=True, slots=True)
class BandPosition:
    """Where a taxable base sits on the scale.

    Attributes:
        rate: Rate of the band the next euro of gain falls in.
        next_rate: Rate of the band after it, ``None`` in the top band.
        room_to_next: EUR of further base before the next band, ``None`` in the top band.
    """

    rate: Decimal
    next_rate: Decimal | None
    room_to_next: Decimal | None


@dataclass(frozen=True, slots=True)
class SavingsScale:
    """Savings bands of one tax year, in ascending order.

    Attributes:
        bands: Non-empty; limits strictly ascending; only the last band is unbounded.
    """

    bands: tuple[SavingsBand, ...]

    def __post_init__(self) -> None:
        """Reject an empty scale, unordered limits and a bounded top band.

        Raises:
            ValueError: On any violated invariant.
        """
        if not self.bands:
            raise ValueError("SavingsScale needs at least one band")
        *bounded, last = self.bands
        if last.up_to is not None:
            raise ValueError("the last savings band must be unbounded")
        limits = [band.up_to for band in bounded]
        if any(limit is None for limit in limits):
            raise ValueError("only the last savings band may be unbounded")
        finite = [limit for limit in limits if limit is not None]
        if any(a >= b for a, b in pairwise(finite)):
            raise ValueError("savings band limits must be strictly ascending")

    def tax(self, base: Decimal) -> Decimal:
        """Return the tax on ``base`` at full precision.

        Args:
            base: Taxable savings base in EUR; a non-positive base owes nothing.

        Returns:
            The sum over bands of ``rate * (part of base inside the band)``.
        """
        if base <= _ZERO:
            return _ZERO
        with exact():
            total = _ZERO
            lower = _ZERO
            for band in self.bands:
                if base > lower:
                    upper = base if band.up_to is None else min(base, band.up_to)
                    total += (upper - lower) * band.rate
                lower = band.up_to if band.up_to is not None else lower
            return total

    def band_at(self, base: Decimal) -> BandPosition:
        """Return the band the next euro above ``base`` is taxed in.

        Args:
            base: Taxable savings base in EUR; below zero counts as zero.

        Returns:
            The band's rate, the next band's rate and the room left before it.
        """
        position = max(base, _ZERO)
        for band, following in pairwise(self.bands):
            limit = band.up_to
            if limit is not None and position < limit:
                with exact():
                    room = limit - position
                return BandPosition(rate=band.rate, next_rate=following.rate, room_to_next=room)
        return BandPosition(rate=self.bands[-1].rate, next_rate=None, room_to_next=None)
