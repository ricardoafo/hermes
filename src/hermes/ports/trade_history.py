"""Trade history port — read-only access to executed trades by time window.

The concrete implementation is
:meth:`hermes.adapters.ledger.sqlite_ledger.SqliteLedger.trades_between`. The
recharacterization tax guard reads its rolling window through it (#135).

Reference: code-conventions.md §5 (Ports as Protocols), ADR-002 §6.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from hermes.domain.events.trade_executed import TradeExecuted

__all__ = ["TradeHistoryPort"]


@runtime_checkable
class TradeHistoryPort(Protocol):
    """Contract for reading executed trades.

    Adapters MUST raise :class:`hermes.domain.errors.InfraError` (or a
    subclass) on any read failure.
    """

    def trades_between(self, start: datetime, end: datetime) -> tuple[TradeExecuted, ...]:
        """Return every trade with ``start <= occurred_at <= end``, oldest first."""
        ...
