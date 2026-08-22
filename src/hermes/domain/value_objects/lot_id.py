"""LotId value object — ULID-based lot identifier.

``LotId`` wraps a ULID string (26-character Crockford base32). ULIDs sort
lexicographically by creation time, which makes FIFO lot ordering deterministic
without a separate timestamp column.

Reference: code-conventions.md §1 (Value Object) and §4 (Factory methods).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import ulid
from returns.result import Failure, Result, Success

from hermes.domain.errors import DomainError, InvalidLot

__all__ = ["LotId"]

# Crockford base32 alphabet — no I, L, O, U
_CROCKFORD_RE = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")
_ULID_LENGTH = 26


@dataclass(frozen=True, slots=True)
class LotId:
    """Immutable lot identifier wrapping a ULID string.

    ULID strings are 26 characters of Crockford base32, which sort
    lexicographically by creation time. This makes FIFO lot ordering
    deterministic without an extra timestamp column.

    Reference: code-conventions.md §1.
    """

    value: str

    @classmethod
    def new(cls) -> LotId:
        """Generate a fresh ULID and wrap it in a ``LotId``.

        Returns:
            New ``LotId`` with a monotonically increasing ULID value.
        """
        return cls(value=str(ulid.new()))

    @classmethod
    def from_str(cls, s: str) -> Result[LotId, DomainError]:
        """Validate and wrap an existing ULID string.

        Args:
            s: 26-character Crockford base32 ULID string.

        Returns:
            ``Success(LotId)`` for valid ULIDs, ``Failure(InvalidLot)``
            for wrong length or invalid characters.

        Reference: code-conventions.md §4.
        """
        s = s.strip().upper()
        if len(s) != _ULID_LENGTH:
            return Failure(
                InvalidLot(reason=f"LotId must be {_ULID_LENGTH} characters, got {len(s)}: {s!r}")
            )
        if not _CROCKFORD_RE.match(s):
            return Failure(
                InvalidLot(reason=f"LotId contains invalid Crockford base32 characters: {s!r}")
            )
        return Success(cls(value=s))

    def __str__(self) -> str:
        """Return the raw ULID string."""
        return self.value
