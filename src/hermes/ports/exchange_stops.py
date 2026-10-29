"""Ports for the stops a real-money position rests on the exchange (#1080).

- :class:`ExchangeStopsPort`: the venue side, implemented by
  :class:`hermes.adapters.exchanges.binance_spot.BinanceSpotAdapter`. Tests
  drive it against the fake market's order endpoints only, never a real
  exchange (ADR-003 §00 I-08).
- :class:`ExchangeStopJournalPort`: the ledger side, implemented by
  :class:`hermes.adapters.ledger.exchange_stop_rows.SqliteExchangeStopJournal`.

Separate from :mod:`hermes.ports.exchange` because hermes only keeps stops
here; it places no entry or exit order through this port.

Reference: GitHub issue #1080; openspec/changes/1080-exchange-stops/change.md.
"""

from collections.abc import Sequence
from decimal import Decimal
from typing import Protocol

from hermes.domain.events.base import DomainEvent
from hermes.domain.events.exchange_stop_changed import ExchangeStopChanged
from hermes.domain.trading.exchange_stop import RestingStop, StopFate
from hermes.domain.trading.order import Order
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.strategy_id import StrategyId

__all__ = ["ExchangeStopJournalPort", "ExchangeStopsPort"]


class ExchangeStopsPort(Protocol):
    """An exchange that holds, lists and cancels stop-loss sells.

    Every method raises :class:`hermes.domain.errors.InfraError` (or a
    subclass) on an exchange or transport failure.
    """

    def place_stop_loss(self, *, order: Order, stop_price: Decimal) -> str:
        """Rest a stop-loss SELL for ``order.qty`` triggered at ``stop_price``.

        Returns:
            The exchange order id.

        Raises:
            hermes.domain.errors.OrderNotPlacedError: The submission failed and
                the exchange confirmed it has no such order: safe to retry.
            hermes.domain.errors.OrderStatusUnknownError: The submission failed
                and the lookup did too: the stop may rest.
            hermes.domain.errors.InfraError: Any other failure or refusal.
        """
        ...

    def cancel_order(self, *, order_id: str) -> None:
        """Cancel ``order_id``; an order that is already gone is not an error."""
        ...

    def resting_stop_losses(self, *, pair: Pair) -> tuple[RestingStop, ...]:
        """Return every stop-loss sell the exchange holds for ``pair``."""
        ...

    def stop_loss_fate(self, *, order_id: str) -> StopFate:
        """Return what became of the stop order ``order_id``."""
        ...


class ExchangeStopJournalPort(Protocol):
    """The ledger's record of each exchange-held stop.

    Adapters raise :class:`hermes.domain.errors.LedgerError` on any storage
    failure, and offer no update or delete.
    """

    def latest_exchange_stop(
        self, strategy_id: StrategyId, pair: Pair
    ) -> ExchangeStopChanged | None:
        """Return the newest row of ``pair`` that is not a failure, or ``None``."""
        ...

    def record_exchange_stops(self, events: Sequence[DomainEvent]) -> None:
        """Append ``events`` (stop changes and their alerts) to the ledger, in order."""
        ...
