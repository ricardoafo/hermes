"""Trial log port — append-only store of backtest trial records.

The concrete implementation is :mod:`hermes.adapters.trial_log.jsonl_file`.
The port has no update or delete operation on purpose: pre-registration §8.1
needs every trial counted.

Reference: code-conventions.md §5 (Ports as Protocols),
openspec/archive/357-trial-log/change.md.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from hermes.domain.calibration.trial_record import TrialRecord

__all__ = ["TrialLogPort"]


@runtime_checkable
class TrialLogPort(Protocol):
    """Contract every trial log store satisfies.

    Adapters MUST raise :class:`hermes.domain.errors.InfraError` (or a
    subclass) when the store cannot be written or has been tampered with.
    """

    def append(self, record: TrialRecord) -> None:
        """Append ``record`` durably before returning."""
        ...

    def records(self) -> tuple[TrialRecord, ...]:
        """Return every record, oldest first, after checking the store is intact."""
        ...
