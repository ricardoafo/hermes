"""Money value object — amount + currency with Result-based arithmetic.

``Money`` is the core financial primitive. All arithmetic is via explicit
named methods that return ``Result`` — arithmetic dunders are intentionally
omitted to prevent accidental ``Money() + Money()`` that silently passes
only for same-currency pairs and raises for cross-currency.

Reference: code-conventions.md §1 (Value Object), §2 (hybrid Result/exceptions),
§4 (Factory methods), §6 (Decimal for ledger values).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from returns.result import Failure, Result, Success

from hermes.domain.errors import CurrencyMismatch, DomainError, InvalidLot
from hermes.domain.value_objects.currency import Currency

__all__ = ["Money"]


@dataclass(frozen=True, slots=True)
class Money:
    """Immutable monetary value: a Decimal amount in a specific currency.

    Use named arithmetic methods (``add``, ``sub``, ``multiply``, ``divide``)
    — they all return ``Result`` so callers are forced to handle the
    currency-mismatch case via mypy. Arithmetic dunders are NOT defined;
    attempting ``money_a + money_b`` is a type error.

    Reference: code-conventions.md §1 and §2.
    """

    amount: Decimal
    currency: Currency

    # ------------------------------------------------------------------
    # Factories
    # ------------------------------------------------------------------

    @classmethod
    def from_str(cls, s: str) -> Result[Money, DomainError]:
        """Parse ``"100.50 EUR"`` into ``Money``.

        Args:
            s: Space-separated amount and currency code (e.g. ``"0.5 BTC"``).

        Returns:
            ``Success(Money)`` on valid input, ``Failure(InvalidLot)`` for
            malformed strings or unknown currencies.

        Reference: code-conventions.md §4.
        """
        parts = s.strip().split()
        if len(parts) != 2:  # noqa: PLR2004
            return Failure(
                InvalidLot(reason=f"Cannot parse Money from {s!r}: expected 'AMOUNT CURRENCY'")
            )
        amount_str, currency_str = parts
        try:
            amount = Decimal(amount_str)
        except InvalidOperation:
            return Failure(InvalidLot(reason=f"Cannot parse amount {amount_str!r} as Decimal"))
        currency_result = Currency.from_str(currency_str)
        if isinstance(currency_result, Failure):
            return Failure(currency_result.failure())
        return Success(cls(amount=amount, currency=currency_result.unwrap()))

    @classmethod
    def zero(cls, currency: Currency) -> Money:
        """Return the additive neutral element for ``currency``.

        Args:
            currency: The currency of the zero value.

        Returns:
            ``Money(Decimal("0"), currency)``.

        Reference: code-conventions.md §4.
        """
        return cls(amount=Decimal("0"), currency=currency)

    # ------------------------------------------------------------------
    # Arithmetic — all return Result
    # ------------------------------------------------------------------

    def add(self, other: Money) -> Result[Money, CurrencyMismatch]:
        """Add two Money values.

        Args:
            other: The Money to add. Must share the same currency.

        Returns:
            ``Success(Money)`` with the summed amount, or
            ``Failure(CurrencyMismatch)`` when currencies differ.

        Reference: code-conventions.md §2.
        """
        if self.currency != other.currency:
            return Failure(CurrencyMismatch(expected=self.currency, actual=other.currency))
        return Success(Money(amount=self.amount + other.amount, currency=self.currency))

    def sub(self, other: Money) -> Result[Money, CurrencyMismatch]:
        """Subtract ``other`` from ``self``.

        Args:
            other: The Money to subtract. Must share the same currency.

        Returns:
            ``Success(Money)`` with the difference, or
            ``Failure(CurrencyMismatch)`` when currencies differ.

        Reference: code-conventions.md §2.
        """
        if self.currency != other.currency:
            return Failure(CurrencyMismatch(expected=self.currency, actual=other.currency))
        return Success(Money(amount=self.amount - other.amount, currency=self.currency))

    def multiply(self, factor: Decimal | int) -> Money:
        """Scalar multiplication. Always succeeds — no currency involved.

        Args:
            factor: Decimal or int scalar.

        Returns:
            New ``Money`` with amount scaled by ``factor``.

        Reference: code-conventions.md §4.
        """
        return Money(amount=self.amount * Decimal(factor), currency=self.currency)

    def divide(self, divisor: Decimal | int) -> Result[Money, DomainError]:
        """Scalar division.

        Args:
            divisor: Decimal or int divisor. Must not be zero.

        Returns:
            ``Success(Money)`` with divided amount, or
            ``Failure(InvalidLot)`` when ``divisor`` is zero.

        Reference: code-conventions.md §4.
        """
        d = Decimal(divisor)
        if d == Decimal("0"):
            return Failure(InvalidLot(reason="Division by zero on Money"))
        return Success(Money(amount=self.amount / d, currency=self.currency))

    # ------------------------------------------------------------------
    # String representation
    # ------------------------------------------------------------------

    def __str__(self) -> str:
        """Return ``"AMOUNT CURRENCY"`` suitable for ``from_str`` roundtrip."""
        return f"{self.amount} {self.currency}"
