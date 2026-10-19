"""Currency value object — allowlisted trading currencies.

Implemented as a ``StrEnum`` so Currency values compare equal to their
string representations (``Currency.EUR == "EUR"``), which is useful when
parsing exchange responses without extra conversion steps.

Reference: code-conventions.md §1 (Value Object) and §4 (Factory methods).
"""

from enum import StrEnum

from returns.result import Failure, Result, Success

from hermes.domain.errors import DomainError, InvalidLot

__all__ = ["Currency"]


class Currency(StrEnum):
    """Allowlisted trading currencies for hermes.

    Only currencies in this enum are accepted at domain boundaries.
    Adding a new currency is a deliberate, versioned change — not an
    accident from a typo in an exchange response.

    Reference: code-conventions.md §1 (Value Object).
    """

    EUR = "EUR"
    USDT = "USDT"
    USDC = "USDC"
    BTC = "BTC"
    ETH = "ETH"
    SOL = "SOL"

    @classmethod
    def from_str(cls, s: str) -> Result[Currency, DomainError]:
        """Parse a string into a Currency, returning Result.

        Args:
            s: Uppercase currency code (e.g. ``"BTC"``).

        Returns:
            ``Success(Currency)`` if ``s`` is in the allowlist,
            ``Failure(InvalidLot)`` otherwise.

        Reference: code-conventions.md §4 — Factory methods vs constructors.
        """
        try:
            return Success(cls(s.upper()))
        except ValueError:
            return Failure(InvalidLot(reason=f"Unknown currency: {s!r}"))
