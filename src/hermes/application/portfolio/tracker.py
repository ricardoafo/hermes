"""PortfolioTracker — holds the current portfolio snapshot for one strategy.

A thin owner of mutable state around the pure
:class:`~hermes.domain.portfolio.snapshot.PortfolioSnapshot`. The serve loop
calls :meth:`mark` before ``on_bar`` so the strategy sees marked equity, and
:meth:`apply_fill` right after the router returns a fill. The accounting
itself lives in the snapshot; this class only swaps the current snapshot on
success.

The tracker is built per strategy and lives in memory. :meth:`rebuilt` restores
it after a restart by replaying the strategy's recorded ``TradeExecuted``
events through the same fill accounting the live loop uses.

Reference: 308-signal-event-portfolio-state change.md D-4, D-7, D-8;
322-rebuild-portfolio-from-ledger change.md D-2.
"""

from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal
from typing import Self

from returns.result import Failure, Result, Success

from hermes.domain.errors import DomainError
from hermes.domain.events.trade_executed import TradeExecuted
from hermes.domain.market_data.bar import Bar
from hermes.domain.portfolio.snapshot import PortfolioSnapshot
from hermes.domain.risk.state import PortfolioState
from hermes.domain.trading.fill import Fill
from hermes.domain.trading.order import Order, OrderSide

__all__ = ["PortfolioTracker", "ReplayError"]


class ReplayError(DomainError):
    """A recorded trade could not be replayed; the message names its correlation id."""


class PortfolioTracker:
    """Current portfolio snapshot plus the calls the bar loop makes on it.

    Args:
        seed: The first snapshot, normally ``PortfolioSnapshot.seed`` built
            from the strategy's configured capital budget.
    """

    def __init__(self, seed: PortfolioSnapshot) -> None:
        """Start from ``seed``."""
        self._snapshot = seed

    @classmethod
    def rebuilt(
        cls, seed: PortfolioSnapshot, trades: Iterable[TradeExecuted]
    ) -> Result[Self, DomainError]:
        """Replay recorded trades over ``seed``.

        Args:
            seed: The snapshot the strategy started from.
            trades: The strategy's ``TradeExecuted`` events, oldest first.

        Returns:
            ``Success(tracker)`` holding the replayed state (the seed when
            ``trades`` is empty), or a ``Failure(ReplayError)`` for the
            first trade the accounting rejects.
        """
        tracker = cls(seed)
        for trade in trades:
            result = tracker.apply_fill(_fill_from_trade(trade))
            if isinstance(result, Failure):
                return Failure(
                    ReplayError(
                        f"trade {trade.correlation_id} ({trade.side.value} {trade.pair}) "
                        f"cannot be replayed: {result.failure()}"
                    )
                )
        return Success(tracker)

    @property
    def current(self) -> PortfolioSnapshot:
        """Return the current snapshot (read-only value object)."""
        return self._snapshot

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

    @property
    def snapshot(self) -> PortfolioSnapshot:
        """The current snapshot (immutable, so safe to hand out)."""
        return self._snapshot

    def state(self) -> PortfolioState:
        """Return the risk-facing state for the current snapshot."""
        return self._snapshot.to_risk_state()

    def open_position_count(self) -> int:
        """Return how many pairs currently hold a position."""
        return len(self._snapshot.positions)

    def open_notional(self) -> Decimal:
        """Return the open positions' value at their latest marks, in the quote currency.

        The pre-reg §5.5 total exposure cap is checked against this value when
        a new entry is sized.
        """
        return self._snapshot.market_value


def _fill_from_trade(trade: TradeExecuted) -> Fill:
    """Rebuild the ``Fill`` the live loop applied for a recorded trade."""
    return Fill(
        order=Order(
            side=OrderSide(trade.side.value),
            pair=trade.pair,
            qty=trade.qty,
            strategy_id=trade.strategy_id,
        ),
        executed_qty=trade.qty.amount,
        avg_price=trade.price.amount,
        fee=trade.fee,
        timestamp=trade.occurred_at,
    )
