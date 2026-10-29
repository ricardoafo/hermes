"""Quantity value object — non-negative asset amount.

``Quantity`` represents an amount of a specific asset (e.g. ``0.5 BTC``).
The amount is constrained to be ≥ 0 — negative quantities are an invalid
domain state.

Reference: code-conventions.md §1 (Value Object) and §4 (Factory methods).
"""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from returns.result import Failure, Result, Success

from hermes.domain.errors import DomainError, InvalidLot
from hermes.domain.value_objects.currency import Currency

__all__ = ["Quantity"]


@dataclass(frozen=True, slots=True)
class Quantity:
    """Immutable asset quantity: a non-negative Decimal amount of a currency.

    Reference: code-conventions.md §1.
    """

    amount: Decimal
    asset: Currency

    def __post_init__(self) -> None:
        """Validate that amount is non-negative."""
        if self.amount < Decimal("0"):
            raise ValueError(f"Quantity amount must be ≥ 0, got {self.amount!r}")

    @classmethod
    def from_str(cls, s: str) -> Result[Quantity, DomainError]:
        """Parse ``"0.5 BTC"`` into ``Quantity``.

        Args:
            s: Space-separated amount and currency code.

        Returns:
            ``Success(Quantity)`` on valid input, ``Failure(InvalidLot)``
            for malformed strings, unknown currencies, or negative amounts.

        Reference: code-conventions.md §4.
        """
        parts = s.strip().split()
        if len(parts) != 2:  # noqa: PLR2004
            return Failure(
                InvalidLot(reason=f"Cannot parse Quantity from {s!r}: expected 'AMOUNT ASSET'")
            )
        amount_str, asset_str = parts
        try:
            amount = Decimal(amount_str)
        except InvalidOperation:
            return Failure(InvalidLot(reason=f"Cannot parse amount {amount_str!r} as Decimal"))
        if amount < Decimal("0"):
            return Failure(InvalidLot(reason=f"Quantity amount must be ≥ 0, got {amount!r}"))
        asset_result = Currency.from_str(asset_str)
        if isinstance(asset_result, Failure):
            return Failure(asset_result.failure())
        return Success(cls(amount=amount, asset=asset_result.unwrap()))

    @classmethod
    def zero(cls, asset: Currency) -> Quantity:
        """Return the zero quantity for ``asset``.

        Args:
            asset: The asset currency.

        Returns:
            ``Quantity(Decimal("0"), asset)``.

        Reference: code-conventions.md §4.
        """
        return cls(amount=Decimal("0"), asset=asset)

    def __str__(self) -> str:
        """Return ``"AMOUNT ASSET"`` suitable for ``from_str`` roundtrip."""
        return f"{self.amount} {self.asset}"
