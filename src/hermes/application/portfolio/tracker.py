"""PortfolioTracker — holds the current portfolio snapshot for one strategy.

A thin owner of mutable state around the pure
:class:`~hermes.domain.portfolio.snapshot.PortfolioSnapshot`. The serve loop
calls :meth:`mark` before ``on_bar`` so the strategy sees marked equity, and
:meth:`apply_fill` right after the router returns a fill. The accounting
itself lives in the snapshot; this class only swaps the current snapshot on
success.

The tracker is built per strategy: a ``Fill`` carries no strategy id yet, so
it trusts the caller to route fills to the right tracker. It lives in memory
and restarts from its seed; rebuilding it from the ledger is a follow-up.

Reference: 308-signal-event-portfolio-state change.md D-4, D-7, D-8.
"""

from __future__ import annotations

from returns.result import Result, Success

from hermes.domain.errors import DomainError
from hermes.domain.market_data.bar import Bar
from hermes.domain.portfolio.snapshot import PortfolioSnapshot
from hermes.domain.risk.state import PortfolioState
from hermes.domain.trading.fill import Fill

__all__ = ["PortfolioTracker"]


class PortfolioTracker:
    """Current portfolio snapshot plus the calls the bar loop makes on it.

    Args:
        seed: The first snapshot, normally ``PortfolioSnapshot.seed`` built
            from the strategy's configured capital budget.
    """

    def __init__(self, seed: PortfolioSnapshot) -> None:
        """Start from ``seed``."""
        self._snapshot = seed

    def mark(self, bar: Bar) -> None:
        """Mark the bar's pair at the bar close when the bar is newer than its mark.

        Args:
            bar: The closed bar about to be passed to the strategy.
        """
        self._snapshot = self._snapshot.after_bar(bar)

    def apply_fill(self, fill: Fill) -> Result[PortfolioSnapshot, DomainError]:
        """Fold a fill into the snapshot.

        Args:
            fill: A confirmed fill the router returned.

        Returns:
            ``Success(new snapshot)`` after swapping it in, or the ``Failure``
            from the snapshot, in which case the state is unchanged.
        """
        result = self._snapshot.after_fill(fill)
        if isinstance(result, Success):
            self._snapshot = result.unwrap()
        return result

    def state(self) -> PortfolioState:
        """Return the risk-facing state for the current snapshot."""
        return self._snapshot.to_risk_state()

    def open_position_count(self) -> int:
        """Return how many pairs currently hold a position."""
        return len(self._snapshot.positions)
