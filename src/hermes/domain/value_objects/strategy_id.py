"""StrategyId value object — strategy bucket identifier.

``StrategyId`` names a trading strategy bucket. Every domain aggregate
that participates in the paper-trading engine (``Lot``, ``Order``,
``Fill``, ``Position``, ``Balance``) carries a ``StrategyId`` from day 1
so that PnL attribution, capital budgeting, and multi-strategy routing
are accurate from the first trade.

For MVP1 the only strategy is ``StrategyId("swing")``. The value-object
design (rather than a StrEnum) allows MVP2's Portfolio Balancer to
register as a second strategy without any breaking change to domain code.

Reference: SYNTHESIS §1 (bucket-aware aggregates), SA-6 (strategy_id as
first-class domain field), spec-1-domain-aggregates D-1.
"""

from __future__ import annotations

import string
from dataclasses import dataclass

__all__ = ["StrategyId"]

_MAX_LENGTH = 64
_ALLOWED_CHARS: frozenset[str] = frozenset(
    c for c in string.printable if c not in string.whitespace
)


@dataclass(frozen=True, slots=True)
class StrategyId:
    """Immutable strategy bucket identifier.

    A non-empty, whitespace-free, printable-ASCII string of at most 64
    characters. Follows the value-object idiom established by ``LotId``
    and ``Money``: wraps a validated primitive so strategy identity cannot
    be accidentally confused with arbitrary strings at the type level.

    Examples: ``StrategyId("swing")``, ``StrategyId("balancer")``,
    ``StrategyId("swing-v2")``.

    Invariants:
        - ``value`` is non-empty.
        - Every character in ``value`` is printable ASCII and not whitespace.
        - ``len(value) <= 64``.

    Reference: spec-1-domain-aggregates D-1.
    """

    value: str

    def __post_init__(self) -> None:
        """Validate the strategy identifier at construction time.

        Raises:
            ValueError: If ``value`` is empty, contains whitespace or
                non-printable characters, or exceeds 64 characters.
        """
        if not self.value:
            raise ValueError("StrategyId.value must not be empty")
        if len(self.value) > _MAX_LENGTH:
            raise ValueError(
                f"StrategyId.value must be at most {_MAX_LENGTH} characters, "
                f"got {len(self.value)}: {self.value!r}"
            )
        invalid = [c for c in self.value if c not in _ALLOWED_CHARS]
        if invalid:
            raise ValueError(
                f"StrategyId.value contains non-printable or whitespace characters: "
                f"{invalid!r} in {self.value!r}"
            )

    def __str__(self) -> str:
        """Return the raw strategy identifier string."""
        return self.value
