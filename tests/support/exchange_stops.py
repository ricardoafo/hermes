"""In-memory stand-ins for the exchange-stop ports (#1080), for the keeper's unit tests.

:class:`FakeStopVenue` holds resting stop-loss sells the way an exchange does
and can be told to fail the next call of a kind; :class:`MemoryStopJournal`
keeps the rows the keeper records. The seam with the real venue is
``tests/integration/scenarios/test_exchange_stops.py``, which drives the real
Binance adapter against the fake market's order endpoints.
"""

import itertools
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from hermes.domain.errors import (
    ExchangeError,
    InfraError,
    OrderNotPlacedError,
    OrderStatusUnknownError,
)
from hermes.domain.events.base import DomainEvent
from hermes.domain.events.exchange_stop_changed import ExchangeStopChanged, StopAction
from hermes.domain.trading.exchange_stop import RestingStop, StopFate
from hermes.domain.trading.order import Order
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.strategy_id import StrategyId


@dataclass
class FakeStopVenue:
    """An exchange that rests, lists, cancels and fills stop-loss sells in memory.

    Attributes:
        resting: The stops on the book, by order id.
        fates: What became of each stop that left the book, by order id.
        calls: Every call, as ``(method, order id or pair)``, in order.
        failures: Errors the next calls of a method raise, oldest first.

    """

    resting: dict[str, RestingStop] = field(default_factory=dict)
    fates: dict[str, StopFate] = field(default_factory=dict)
    calls: list[tuple[str, str]] = field(default_factory=list)
    failures: dict[str, list[InfraError]] = field(default_factory=dict)
    _ids: itertools.count[int] = field(default_factory=lambda: itertools.count(1))

    def fail_next(self, method: str, error: InfraError) -> None:
        """Make the next call of ``method`` raise ``error``."""
        self.failures.setdefault(method, []).append(error)

    def rest(self, pair: Pair, qty: str, stop: str) -> str:
        """Put a stop on the book that hermes did not place; return its id."""
        order_id = f"X:{next(self._ids)}"
        self.resting[order_id] = RestingStop(order_id, pair, Decimal(qty), Decimal(stop))
        return order_id

    def trigger(self, order_id: str) -> None:
        """Fill a resting stop, as the market reaching it would."""
        del self.resting[order_id]
        self.fates[order_id] = StopFate.FILLED

    def drop(self, order_id: str) -> None:
        """Remove a resting stop without a fill, as a cancel outside hermes would."""
        del self.resting[order_id]
        self.fates[order_id] = StopFate.GONE

    def _maybe_fail(self, method: str) -> None:
        queued = self.failures.get(method)
        if queued:
            raise queued.pop(0)

    def place_stop_loss(self, *, order: Order, stop_price: Decimal) -> str:
        """Rest a stop for ``order.qty`` at ``stop_price``, unless told to fail."""
        self.calls.append(("place", str(order.pair)))
        queued = self.failures.get("place")
        if queued and isinstance(queued[0], OrderStatusUnknownError):
            # The stop reached the book; only the answer was lost.
            order_id = f"X:{next(self._ids)}"
            self.resting[order_id] = RestingStop(order_id, order.pair, order.qty.amount, stop_price)
        self._maybe_fail("place")
        order_id = f"X:{next(self._ids)}"
        self.resting[order_id] = RestingStop(order_id, order.pair, order.qty.amount, stop_price)
        return order_id

    def cancel_order(self, *, order_id: str) -> None:
        """Cancel ``order_id``; an order already gone is not an error."""
        self.calls.append(("cancel", order_id))
        self._maybe_fail("cancel")
        if self.resting.pop(order_id, None) is not None:
            self.fates[order_id] = StopFate.GONE

    def resting_stop_losses(self, *, pair: Pair) -> tuple[RestingStop, ...]:
        """Return the stops on the book for ``pair``."""
        self.calls.append(("list", str(pair)))
        self._maybe_fail("list")
        return tuple(s for s in self.resting.values() if s.pair == pair)

    def stop_loss_fate(self, *, order_id: str) -> StopFate:
        """Return what became of ``order_id``."""
        self.calls.append(("fate", order_id))
        self._maybe_fail("fate")
        if order_id in self.resting:
            return StopFate.RESTING
        return self.fates.get(order_id, StopFate.GONE)


@dataclass
class MemoryStopJournal:
    """The ledger's exchange-stop rows, in memory.

    Attributes:
        events: Every event recorded, in order.

    """

    events: list[DomainEvent] = field(default_factory=list)

    def latest_exchange_stop(
        self, strategy_id: StrategyId, pair: Pair
    ) -> ExchangeStopChanged | None:
        """Return the newest row of ``pair`` that is not a failure."""
        for event in reversed(self.events):
            if (
                isinstance(event, ExchangeStopChanged)
                and event.strategy_id == strategy_id
                and event.pair == pair
                and event.action is not StopAction.FAILED
            ):
                return event
        return None

    def record_exchange_stops(self, events: Sequence[DomainEvent]) -> None:
        """Append ``events``."""
        self.events.extend(events)


@dataclass
class FixedClock:
    """A clock that always reads ``at``."""

    at: datetime = datetime(2024, 10, 29, 4, 0, 5, tzinfo=UTC)

    def now(self) -> datetime:
        """Return ``at``."""
        return self.at


def refusal(msg: str = "Account has insufficient balance") -> ExchangeError:
    """Return the refusal an exchange answers a bad order with."""
    return ExchangeError(msg=msg)


def not_placed() -> OrderNotPlacedError:
    """Return the error for a submission the exchange confirms it never saw."""
    return OrderNotPlacedError(msg="lost", client_order_id="hermes-aaaa")


def status_unknown() -> OrderStatusUnknownError:
    """Return the error for a submission whose fate nobody knows."""
    return OrderStatusUnknownError(msg="lost twice", client_order_id="hermes-bbbb")
