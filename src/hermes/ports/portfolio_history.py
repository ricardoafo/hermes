"""Portfolio history port — the per-bar portfolio marks the serve loop records.

The concrete implementation is :class:`hermes.adapters.ledger.sqlite_ledger.SqliteLedger`.
The console's read model reads it (#387). These rows hold equity and PnL, so
AI-facing tools must not read them before certification (ADR-012 D-06).

Reference: code-conventions.md §5 (Ports as Protocols), GitHub issue #387.
"""

from datetime import datetime
from typing import Protocol, runtime_checkable

from hermes.domain.portfolio.mark import PortfolioMark
from hermes.domain.value_objects.strategy_id import StrategyId

__all__ = ["PortfolioHistoryPort"]


@runtime_checkable
class PortfolioHistoryPort(Protocol):
    """Contract for storing and reading portfolio marks.

    Adapters MUST raise :class:`hermes.domain.errors.InfraError` (or a
    subclass) on any storage failure, and offer no update or delete.
    """

    def record_portfolio_mark(self, mark: PortfolioMark) -> bool:
        """Store ``mark``; return ``False`` when its bar is already recorded."""
        ...

    def portfolio_marks(
        self, strategy_id: StrategyId, start: datetime, end: datetime
    ) -> tuple[PortfolioMark, ...]:
        """Return marks with ``start <= bar_close_time < end``, oldest first."""
        ...

    def latest_portfolio_mark(self, strategy_id: StrategyId) -> PortfolioMark | None:
        """Return the newest mark of the strategy, or ``None`` before the first bar."""
        ...
