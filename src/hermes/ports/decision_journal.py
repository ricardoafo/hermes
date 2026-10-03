"""Decision journal store port — append-only persistence.

There is deliberately no update or delete: the journal is append-only, and a
correction is a new entry (issue #363).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from hermes.domain.journal.entries import Decision, Review

__all__ = ["DecisionJournalPort", "JournalIntegrityError"]


class JournalIntegrityError(Exception):
    """The stored journal was edited, reordered or truncated."""


@runtime_checkable
class DecisionJournalPort(Protocol):
    """Append and read journal entries."""

    def append(self, entry: Decision | Review) -> None:
        """Append one entry at the end of the journal."""
        ...

    def entries(self) -> Sequence[Decision | Review]:
        """Return every entry in append order.

        Raises:
            JournalIntegrityError: The stored history fails verification.
        """
        ...
