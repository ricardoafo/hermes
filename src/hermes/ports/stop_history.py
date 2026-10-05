"""Stop history port — the per-bar trailing stop of each open position.

The concrete implementation is :class:`hermes.adapters.ledger.sqlite_ledger.SqliteLedger`.
The serve loop writes one mark per 4h bar while a position is open and reads the
latest back when it restores the position after a restart (#388). These rows
hold prices, so AI-facing tools must not read them before certification
(ADR-012 D-02, D-06).

Reference: code-conventions.md §5 (Ports as Protocols), GitHub issue #388.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from hermes.domain.strategy.stop_mark import StopMark
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.strategy_id import StrategyId

__all__ = ["StopHistoryPort"]


@runtime_checkable
class StopHistoryPort(Protocol):
    """Contract for storing and reading stop marks.

    Adapters MUST raise :class:`hermes.domain.errors.InfraError` (or a
    subclass) on any storage failure, and offer no update or delete.
    """

    def record_stop_mark(self, mark: StopMark) -> bool:
        """Store ``mark``; return ``False`` when its bar is already recorded."""
        ...

    def latest_stop_mark(
        self, strategy_id: StrategyId, pair: Pair, entry_time: datetime
    ) -> StopMark | None:
        """Return the newest mark of the position that opened at ``entry_time``, or ``None``."""
        ...
