"""Weight value object — portfolio allocation weight in [0, 1].

``Weight`` represents a fractional portfolio allocation. It is constrained
to ``0 ≤ value ≤ 1`` so that a set of weights can represent valid target
allocations (they must sum to 1.0, but that invariant is on ``TargetWeights``,
not on the individual ``Weight``).

Reference: code-conventions.md §1 (Value Object) and §4 (Factory methods).
"""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from returns.result import Failure, Result, Success

from hermes.domain.errors import DomainError, InvalidLot

__all__ = ["Weight"]

_ZERO = Decimal("0")
_ONE = Decimal("1")


@dataclass(frozen=True, slots=True)
class Weight:
    """Immutable portfolio weight constrained to [0, 1].

    Reference: code-conventions.md §1.
    """

    value: Decimal

    def __post_init__(self) -> None:
        """Validate that value is in [0, 1]."""
        if not (_ZERO <= self.value <= _ONE):
            raise ValueError(f"Weight must be in [0, 1], got {self.value!r}")

    @classmethod
    def from_str(cls, s: str) -> Result[Weight, DomainError]:
        """Parse a decimal string into a Weight.

        Args:
            s: Decimal string (e.g. ``"0.25"`` or ``"1"``).

        Returns:
            ``Success(Weight)`` for valid values in [0, 1],
            ``Failure(InvalidLot)`` for malformed strings or out-of-range values.

        Reference: code-conventions.md §4.
        """
        try:
            value = Decimal(s.strip())
        except InvalidOperation:
            return Failure(
                InvalidLot(reason=f"Cannot parse Weight from {s!r}: not a valid Decimal")
            )
        if not (_ZERO <= value <= _ONE):
            return Failure(InvalidLot(reason=f"Weight must be in [0, 1], got {value!r}"))
        return Success(cls(value=value))

    @classmethod
    def from_decimal(cls, d: Decimal) -> Result[Weight, DomainError]:
        """Construct a Weight from a Decimal, validating the [0, 1] constraint.

        Args:
            d: Decimal value to wrap.

        Returns:
            ``Success(Weight)`` for values in [0, 1],
            ``Failure(InvalidLot)`` for out-of-range values.

        Reference: code-conventions.md §4.
        """
        if not (_ZERO <= d <= _ONE):
            return Failure(InvalidLot(reason=f"Weight must be in [0, 1], got {d!r}"))
        return Success(cls(value=d))

    @classmethod
    def zero(cls) -> Weight:
        """Return a weight of 0 (no allocation).

        Returns:
            ``Weight(Decimal("0"))``.
        """
        return cls(value=_ZERO)

    @classmethod
    def one(cls) -> Weight:
        """Return a weight of 1 (full allocation).

        Returns:
            ``Weight(Decimal("1"))``.
        """
        return cls(value=_ONE)

    def __str__(self) -> str:
        """Return the decimal string representation."""
        return str(self.value)
