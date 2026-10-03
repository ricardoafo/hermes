"""Ledger port — Protocol for the FIFO tax-lot persistence layer.

Implementations live under ``hermes.adapters.ledger``. The Protocol shape is
intentionally minimal for this PR — additional query methods (reconciliation
reports, month-end summaries) land in follow-up issues so this foundation
stays reviewable.

Reference: ADR-003 §00 invariant I-06 (every trade write happens in one
SQLite transaction with its domain event), §03 (persistence schema),
code-conventions.md §1 (Ports & Adapters).
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from hermes.domain.events.base import DomainEvent
from hermes.domain.events.signal_emitted import SignalEmitted
from hermes.domain.lots.lot import Lot
from hermes.domain.lots.lot_consumption import LotConsumption
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.strategy_id import StrategyId

__all__ = ["LedgerPort"]


@runtime_checkable
class LedgerPort(Protocol):
    """Minimum contract every ledger adapter satisfies.

    Every write method groups its row insert AND the accompanying event
    append into ONE SQLite transaction. Callers must not attempt to open
    a transaction externally — the adapter owns the boundary so invariant
    I-06 is a machine-checkable property (see integration tests).

    Adapters raise :class:`hermes.domain.errors.LedgerError` on I/O
    failure; the application layer catches and translates to ``Result``.
    """

    def open_lot(self, lot: Lot, event: DomainEvent) -> None:
        """Persist ``lot`` and ``event`` in the same SQLite transaction.

        Args:
            lot: A freshly-opened :class:`Lot` with
                ``remaining_qty == original_qty``.
            event: The :class:`TradeExecuted` (BUY side) that caused this
                lot to open. Its ``correlation_id`` becomes the tracing link
                between the lot row and the event row.

        Raises:
            LedgerError: On any SQLite failure. The transaction is rolled
                back — neither the lot nor the event lands in the ledger
                (invariant I-06).
        """
        ...

    def consume_lot(
        self,
        consumptions: tuple[LotConsumption, ...],
        updated_lots: tuple[Lot, ...],
        event: DomainEvent,
    ) -> None:
        """Persist a batch of consumptions and updated lot remainders atomically.

        A single SELL may touch N lots (see FIFO worked example in ADR-003
        §03). The tuple length of ``consumptions`` matches the number of
        lots consumed; ``updated_lots`` carries the lots with reduced
        ``remaining_qty`` so this method can persist the new remainders
        without an additional round-trip.

        Args:
            consumptions: One :class:`LotConsumption` per lot touched, in
                FIFO order.
            updated_lots: The same lots after ``consume`` reduced their
                ``remaining_qty``. One entry per consumption, same order.
            event: The :class:`TradeExecuted` (SELL side) that caused the
                consumption.

        Raises:
            LedgerError: On any SQLite failure. The transaction is rolled
                back — neither the consumptions, nor the updated remainders,
                nor the event land in the ledger (invariant I-06).
        """
        ...

    def list_open_lots(self, asset: Currency) -> tuple[Lot, ...]:
        """Return every lot with ``remaining_qty > 0`` for the given asset.

        The returned tuple is sorted the way :func:`select_lots_fifo`
        expects to receive input — primary sort ``opened_at`` ascending,
        secondary sort ``lot_id.value`` ascending. Callers that need
        different orderings must resort in memory.

        Args:
            asset: Filter to lots holding this base currency only.

        Returns:
            Immutable tuple of open :class:`Lot` instances. Empty when no
            open lots exist for ``asset``.

        Raises:
            LedgerError: On any SQLite read failure.
        """
        ...

    def append_event(self, event: DomainEvent) -> None:
        """Append a standalone domain event to the ``events`` table.

        Used for events that are NOT part of a lot open/consume operation
        (e.g. :class:`AnomalyDetected`, :class:`RebalanceTriggered`). Runs
        in its own single-statement transaction.

        Args:
            event: Any concrete :class:`DomainEvent` subclass.

        Raises:
            LedgerError: On any SQLite write failure.
        """
        ...

    def has_signal_event(self, event: SignalEmitted) -> bool:
        """Return whether a ledger row already records ``event``.

        A row matches on strategy, pair, kind and ``occurred_at`` (the signal
        bar's close time), so a bar re-evaluated after a restart finds the
        signal it already wrote.

        Args:
            event: The signal event to look up.

        Returns:
            ``True`` when a matching ``SignalEmitted`` row exists.

        Raises:
            LedgerError: On any SQLite read failure.
        """
        ...

    def last_processed_close(self, pair: Pair, interval: KlineInterval) -> datetime | None:
        """Return the close time of the newest bar handled for one stream.

        Reads the ``BarProcessed`` rows the serve loop appends after every
        handled bar.

        Args:
            pair: Trading pair.
            interval: Candle-duration bucket.

        Returns:
            The newest ``occurred_at`` for the stream, or ``None`` when no
            bar of it was ever handled.

        Raises:
            LedgerError: On any SQLite read failure.
        """
        ...

    def latest_signal_event(
        self, strategy_id: StrategyId, pair: Pair, *, at_or_before: datetime
    ) -> SignalEmitted | None:
        """Return the newest signal of one strategy and pair, bounded in time.

        Args:
            strategy_id: Strategy bucket that emitted the signal.
            pair: Trading pair.
            at_or_before: Inclusive upper bound on ``occurred_at``. A signal
                newer than this (written before a crash, ahead of its cursor
                row) is ignored.

        Returns:
            The newest matching event, or ``None`` when there is none.

        Raises:
            LedgerError: On any SQLite read failure or an unreadable payload.
        """
        ...
