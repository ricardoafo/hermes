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

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol, runtime_checkable

from hermes.domain.events.base import DomainEvent
from hermes.domain.events.fill_quality_recorded import FillQualityRecorded
from hermes.domain.events.signal_emitted import SignalEmitted
from hermes.domain.events.trade_executed import TradeExecuted
from hermes.domain.fx.eur_pricing import EurPricing
from hermes.domain.lots.lot import Lot
from hermes.domain.lots.lot_consumption import LotConsumption
from hermes.domain.lots.quote_leg import CapitalSeed
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.strategy.signal import SignalKind
from hermes.domain.trading.order import OrderSide
from hermes.domain.trading.trade_record import TradeEur, TradeRecord
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.strategy_id import StrategyId

__all__ = [
    "EurTradeRow",
    "ExecutionLedgerPort",
    "LedgerPort",
    "PendingSeed",
    "PendingTrade",
    "TaxTradeSourcePort",
    "TradeLedgerPort",
]


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
        self,
        strategy_id: StrategyId,
        pair: Pair,
        *,
        at_or_before: datetime,
        kind: SignalKind | None = None,
    ) -> SignalEmitted | None:
        """Return the newest signal of one strategy and pair, bounded in time.

        Args:
            strategy_id: Strategy bucket that emitted the signal.
            pair: Trading pair.
            at_or_before: Inclusive upper bound on ``occurred_at``. A signal
                newer than this (written before a crash, ahead of its cursor
                row) is ignored.
            kind: Only signals of this kind, or any kind when ``None``. An
                entry is looked up past the exits declined after it (#706).

        Returns:
            The newest matching event, or ``None`` when there is none.

        Raises:
            LedgerError: On any SQLite read failure or an unreadable payload.
        """
        ...

    def trade_events(self, strategy_id: StrategyId) -> tuple[TradeExecuted, ...]:
        """Return every recorded trade of one strategy, oldest first.

        Args:
            strategy_id: Strategy bucket the fills belong to.

        Returns:
            The rehydrated events ordered by ``occurred_at`` then insert order.
            Empty when the strategy has no recorded trade.

        Raises:
            LedgerError: On any SQLite read failure, a row that is not schema
                version 2, or an unreadable payload.
        """
        ...


@dataclass(frozen=True, slots=True)
class PendingTrade:
    """A stored trade whose EUR values are still pending.

    Attributes:
        trade_id: Ledger row id.
        pair: Traded pair; ``pair.quote`` is the currency to price.
        executed_at: Fill time; its Madrid date picks the EUR rate.
        price: Native price of one base unit.
        fee: Native fee.
    """

    trade_id: int
    pair: Pair
    executed_at: datetime
    price: Decimal
    fee: Money


@dataclass(frozen=True, slots=True)
class PendingSeed:
    """A stored capital seed whose EUR value is still pending.

    Attributes:
        seed_id: Ledger row id.
        asset: Currency the capital is held in; the currency to price.
        seeded_at: Seed date; its Madrid date picks the EUR rate.
    """

    seed_id: int
    asset: Currency
    seeded_at: datetime


@runtime_checkable
class TradeLedgerPort(Protocol):
    """Trade-row persistence with EUR values (change ``trade-eur-values``).

    Lots and consumptions written through this port are a projection of the
    ``trades`` table: every write replays the asset's trades FIFO and stores
    the EUR values, fees included, in the same transaction (I-06).

    Adapters raise :class:`hermes.domain.errors.LedgerError` on failure.
    """

    def record_trade(self, trade: TradeRecord, event: DomainEvent, *also: DomainEvent) -> int:
        """Store one trade, its lot effects and its events atomically.

        Args:
            trade: The trade; ``trade.eur is None`` stores it pending.
            event: The ``TradeExecuted`` event for the trade.
            *also: Further events about the same fill, such as its
                ``FillQualityRecorded`` row, stored in the same transaction.

        Returns:
            The new trade row id.

        Raises:
            LedgerError: On any SQLite failure, or a SELL beyond the open
                quantity of its asset. Nothing is stored.
        """
        ...

    def list_pending_trades(self) -> tuple[PendingTrade, ...]:
        """Return every trade whose EUR values are pending, oldest first.

        Returns:
            Pending trades ordered by ``executed_at`` then id.

        Raises:
            LedgerError: On any SQLite read failure.
        """
        ...

    def apply_trade_eur(self, trade_id: int, eur: TradeEur) -> None:
        """Fill a pending trade's EUR values and re-project its asset.

        Args:
            trade_id: Row id of the trade.
            eur: The EUR values to store.

        Raises:
            LedgerError: On any SQLite failure or an unknown ``trade_id``.
        """
        ...

    def record_capital_seed(self, seed: CapitalSeed, event: DomainEvent) -> int:
        """Store a strategy's start capital as one quote-asset lot, with its event.

        The seed is stored EUR-pending; :meth:`apply_seed_eur` prices it. From
        then on the seed's asset is replayed FIFO as the quote leg of every
        trade quoted in it (change ``405-usdt-fifo`` D-3).

        Args:
            seed: The seed, without an EUR rate.
            event: The ``ShadowCapitalSeeded`` event.

        Returns:
            The new seed row id.

        Raises:
            LedgerError: On any SQLite failure, a second seed for the same
                strategy, a priced seed or a quote leg that cannot be replayed.
                Nothing is stored.
        """
        ...

    def capital_seed(self, strategy_id: StrategyId) -> CapitalSeed | None:
        """Return the strategy's seed, or ``None`` when it has none.

        Raises:
            LedgerError: On any SQLite read failure.
        """
        ...

    def earliest_activity(self) -> datetime | None:
        """Return the earliest bar, signal or trade event time, or ``None`` without any.

        Raises:
            LedgerError: On any SQLite read failure.
        """
        ...

    def list_pending_seeds(self) -> tuple[PendingSeed, ...]:
        """Return every seed whose EUR value is pending, oldest first.

        Raises:
            LedgerError: On any SQLite read failure.
        """
        ...

    def apply_seed_eur(self, seed_id: int, pricing: EurPricing) -> None:
        """Price a pending seed and re-project its asset.

        Args:
            seed_id: Row id of the seed.
            pricing: The EUR rate of the seed's asset on its seed date.

        Raises:
            LedgerError: On any SQLite failure, an unknown ``seed_id`` or a
                pricing for another currency.
        """
        ...


@runtime_checkable
class ExecutionLedgerPort(Protocol):
    """What the shadow executor reads and appends besides the trade rows.

    Change ``353-fill-kernel-in-serve`` D-4, D-5: the executor checks the
    recorded trades before filling a re-evaluated signal, appends one
    :class:`FillQualityRecorded` per fill, and reads the newest one back for
    risk rule RO-03.

    Adapters raise :class:`hermes.domain.errors.LedgerError` on failure.
    """

    def append_event(self, event: DomainEvent) -> None:
        """Append one standalone event.

        Args:
            event: The event to store.

        Raises:
            LedgerError: On any SQLite failure.
        """
        ...

    def trade_events(self, strategy_id: StrategyId) -> tuple[TradeExecuted, ...]:
        """Return every recorded trade of one strategy, oldest first.

        Args:
            strategy_id: Strategy bucket the fills belong to.

        Returns:
            The rehydrated events ordered by ``occurred_at`` then insert order.

        Raises:
            LedgerError: On any SQLite read failure or an unreadable payload.
        """
        ...

    def fill_quality_events(self, strategy_id: StrategyId) -> tuple[FillQualityRecorded, ...]:
        """Return every fill-quality row of one strategy, oldest first.

        Args:
            strategy_id: Strategy bucket the fills belong to.

        Returns:
            The rehydrated events ordered by ``occurred_at`` then insert order.

        Raises:
            LedgerError: On any SQLite read failure or an unreadable payload.
        """
        ...


@dataclass(frozen=True, slots=True)
class EurTradeRow:
    """One step of an asset's FIFO replay with its EUR values, as the tax estimate reads it.

    A stored trade gives a base-asset row and, once its quote currency has a
    capital seed, a quote-asset row with the opposite side; the seed itself is
    a quote-asset BUY (change ``405-usdt-fifo``, ADR-016 item 4).

    Attributes:
        ref: Replay ref: the trade row id, or ``seed:<id>`` for a capital seed.
        asset: The asset this row moves (the base, or the quote for a quote leg).
        side: BUY opens a lot of ``asset``; SELL consumes its lots.
        qty: Quantity of ``asset`` moved (net of a fee taken in it).
        executed_at: Fill (or seed) time, timezone-aware.
        price_eur: EUR price of one unit, ``None`` while pending.
        fee_eur: EUR fee, ``None`` while pending.
        quote_leg: ``True`` for a quote-currency row (a seed or a trade's quote leg).
    """

    ref: str
    asset: Currency
    side: OrderSide
    qty: Decimal
    executed_at: datetime
    price_eur: Decimal | None
    fee_eur: Decimal | None
    quote_leg: bool = False


@runtime_checkable
class TaxTradeSourcePort(Protocol):
    """Read side of the trade ledger for tax estimates (change ``tax-estimates``).

    Adapters raise :class:`hermes.domain.errors.LedgerError` on failure.
    """

    def list_eur_trades(self) -> tuple[EurTradeRow, ...]:
        """Return every row the FIFO projection replays, all assets and both legs.

        Returns:
            Each asset's rows in the ledger's replay order: base-asset lot-opening
            BUYs and SELLs, and for a seeded quote currency its seed and the quote
            leg of every trade quoted in it.

        Raises:
            LedgerError: On any SQLite read failure.
        """
        ...
