"""ReconciliationMonth — the calendar month a reconciliation covers.

Months are calendar months in Europe/Madrid, the same clock ADR-016 item 2
uses for the tax year, so a fill at 23:30 UTC on 31 March belongs to April.
:attr:`ReconciliationMonth.start` and :attr:`ReconciliationMonth.end` give the
bounds as UTC instants, half-open: ``start <= t < end``.

Reference: openspec/changes/35-monthly-reconciliation/change.md D-3, ADR-016.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from hermes.domain.value_objects.time import MADRID

__all__ = ["ReconciliationMonth"]

_PATTERN: Final = re.compile(r"(\d{4})-(\d{2})")
_DECEMBER: Final = 12
# datetime ends at year 9999, and a month needs its following month for its end.
_MAX_YEAR: Final = 9998


@dataclass(frozen=True, slots=True, order=True)
class ReconciliationMonth:
    """One calendar month in Europe/Madrid.

    Attributes:
        year: Four-digit year.
        month: 1 to 12.

    Raises:
        ValueError: On a month outside 1 to 12 or a year outside 1 to 9998.
    """

    year: int
    month: int

    def __post_init__(self) -> None:
        """Reject an impossible month or a year with no following month.

        Raises:
            ValueError: See the class docstring.
        """
        if not 1 <= self.month <= _DECEMBER:
            raise ValueError(f"month must be 1 to 12, got {self.month}")
        if not 1 <= self.year <= _MAX_YEAR:
            raise ValueError(f"year must be 1 to {_MAX_YEAR}, got {self.year}")

    @classmethod
    def parse(cls, text: str) -> ReconciliationMonth:
        """Parse ``YYYY-MM``.

        Args:
            text: The month, e.g. ``"2026-10"``.

        Returns:
            The month.

        Raises:
            ValueError: ``text`` is not ``YYYY-MM`` or names no real month.
        """
        match = _PATTERN.fullmatch(text.strip())
        if match is None:
            raise ValueError(f"expected YYYY-MM, got {text!r}")
        return cls(year=int(match.group(1)), month=int(match.group(2)))

    @classmethod
    def containing(cls, instant: datetime) -> ReconciliationMonth:
        """Return the Madrid month ``instant`` falls in.

        Args:
            instant: A timezone-aware instant.

        Returns:
            The month holding ``instant`` on the Madrid wall clock.

        Raises:
            ValueError: ``instant`` is naive.
        """
        if instant.tzinfo is None:
            raise ValueError("ReconciliationMonth.containing needs an aware datetime")
        local = instant.astimezone(MADRID)
        return cls(year=local.year, month=local.month)

    def previous(self) -> ReconciliationMonth:
        """Return the month before this one."""
        if self.month == 1:
            return ReconciliationMonth(year=self.year - 1, month=_DECEMBER)
        return ReconciliationMonth(year=self.year, month=self.month - 1)

    def following(self) -> ReconciliationMonth:
        """Return the month after this one."""
        if self.month == _DECEMBER:
            return ReconciliationMonth(year=self.year + 1, month=1)
        return ReconciliationMonth(year=self.year, month=self.month + 1)

    @property
    def start(self) -> datetime:
        """Madrid midnight on the first day, as a UTC instant (inclusive)."""
        return datetime(self.year, self.month, 1, tzinfo=MADRID).astimezone(UTC)

    @property
    def end(self) -> datetime:
        """Madrid midnight on the first day of the next month, as UTC (exclusive)."""
        return self.following().start

    def __str__(self) -> str:
        """Return ``YYYY-MM``."""
        return f"{self.year:04d}-{self.month:02d}"
