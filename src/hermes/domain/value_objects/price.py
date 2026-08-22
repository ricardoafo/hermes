"""Price value object — amount per unit of a trading pair.

``Price`` represents the exchange rate of a ``Pair`` (e.g. ``50000.00 BTC/USDT``
means 1 BTC costs 50000 USDT). ``apply_to`` converts a ``Quantity`` to the
equivalent ``Money`` in the quote currency.

Reference: code-conventions.md §1 (Value Object) and §4 (Factory methods).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from returns.result import Failure, Result, Success

from hermes.domain.errors import DomainError, InvalidLot
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.quantity import Quantity

__all__ = ["Price"]


@dataclass(frozen=True, slots=True)
class Price:
    """Immutable price for a trading pair: Decimal amount per base unit.

    Reference: code-conventions.md §1.
    """

    amount: Decimal
    pair: Pair

    @classmethod
    def from_str(cls, s: str) -> Result[Price, DomainError]:
        """Parse ``"50000.00 BTC/USDT"`` into ``Price``.

        Args:
            s: Space-separated amount and pair string (slash form only).

        Returns:
            ``Success(Price)`` on valid input, ``Failure(InvalidLot)`` otherwise.

        Reference: code-conventions.md §4.
        """
        parts = s.strip().split()
        if len(parts) != 2:  # noqa: PLR2004
            return Failure(
                InvalidLot(reason=f"Cannot parse Price from {s!r}: expected 'AMOUNT PAIR'")
            )
        amount_str, pair_str = parts
        try:
            amount = Decimal(amount_str)
        except InvalidOperation:
            return Failure(InvalidLot(reason=f"Cannot parse amount {amount_str!r} as Decimal"))
        pair_result = Pair.from_str(pair_str)
        if isinstance(pair_result, Failure):
            return Failure(pair_result.failure())
        return Success(cls(amount=amount, pair=pair_result.unwrap()))

    def apply_to(self, qty: Quantity) -> Result[Money, DomainError]:
        """Multiply this price by a quantity to yield Money in the quote currency.

        Args:
            qty: The quantity of the base asset. Its ``asset`` must match
                ``self.pair.base``.

        Returns:
            ``Success(Money)`` with the quote-currency value, or
            ``Failure(InvalidLot)`` when the quantity asset does not match
            the pair base.

        Reference: code-conventions.md §4.
        """
        if qty.asset != self.pair.base:
            return Failure(
                InvalidLot(
                    reason=(
                        f"Price.apply_to: quantity asset {qty.asset!r} does not match "
                        f"pair base {self.pair.base!r}"
                    )
                )
            )
        quote_currency: Currency = self.pair.quote
        return Success(Money(amount=self.amount * qty.amount, currency=quote_currency))

    def __str__(self) -> str:
        """Return ``"AMOUNT BASE/QUOTE"`` suitable for ``from_str`` roundtrip."""
        return f"{self.amount} {self.pair}"
