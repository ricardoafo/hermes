"""ExchangeStopKeeper: every real-money position rests a stop held by the exchange (#1080).

A hermes outage must never leave a real-money position unprotected (ADR-024
§7). After each 4h bar, before each exit and once at boot, serve tells the
keeper what should rest for a pair (a :class:`Protection`, or ``None`` when
flat), and :meth:`ExchangeStopKeeper.keep` makes the exchange match:

1. It lists the stop-loss sells the exchange holds for the pair. The ledger
   names the stop hermes last rested; when it no longer rests, the keeper
   asks what became of it and records it ``filled`` or ``gone``.
2. A position whose stop already filled on the exchange is not protected
   again: the exchange sold it.
3. A resting stop with the wanted quantity and price is kept (``adopted`` when
   the ledger did not name it, after a lost answer or a crash). Every other
   resting stop is cancelled first, so the quantity it locks is free.
4. With nothing matching, a new stop is placed. An order the exchange
   confirms it never saw is retried once; an unknown status is never retried
   blindly, and the next pass adopts the stop if it rests.

The stop is a backstop at the strategy's own level (Code Owner decision,
2026-10-19): the wanted stop is the strategy's, rounded down to the exchange's
step so it never rests above it. The keeper never sells, never moves a stop on
its own and never changes a strategy, sizing, fee or kill rule; the strategy's
exit stays the one the ledger records as a trade.

Every change is an :class:`ExchangeStopChanged` row; a failure or a stop
found filled or gone also raises an :class:`AnomalyDetected` alert. Alerts
name the pair, never a price (ADR-012 D-02). Exchange failures are recorded,
never raised: the bar loop goes on, and the next pass tries again.

Reference: GitHub issue #1080; openspec/changes/1080-exchange-stops/change.md.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Final, NoReturn

from hermes.domain.errors import InfraError, OrderNotPlacedError, OrderStatusUnknownError
from hermes.domain.events.anomaly_detected import AnomalyDetected, AnomalySeverity
from hermes.domain.events.base import DomainEvent
from hermes.domain.events.exchange_stop_changed import ExchangeStopChanged, StopAction
from hermes.domain.trading.exchange_stop import EXCHANGE_STEP, Protection, RestingStop, StopFate
from hermes.domain.trading.order import Order, OrderSide
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.quantity import Quantity
from hermes.domain.value_objects.strategy_id import StrategyId
from hermes.ports.clock import ClockPort
from hermes.ports.exchange_stops import ExchangeStopJournalPort, ExchangeStopsPort

__all__ = ["STOP_SOURCE", "ExchangeStopKeeper"]

STOP_SOURCE: Final = "serve.exchange_stop"
"""``AnomalyDetected.source`` of every exchange-stop alert."""

_PLACE_ATTEMPTS: Final = 2
"""A placement the exchange confirms it never saw is safe to send once more (#858)."""


@dataclass(frozen=True, slots=True)
class ExchangeStopKeeper:
    """Keeps the exchange's stops matching what the strategy's positions need.

    Attributes:
        exchange: The venue that holds the stops.
        journal: The ledger's record of each stop.
        strategy_id: The strategy whose positions the stops protect.
        clock: Stamps every row and alert.
        step: The exchange's price and quantity step; stops round down to it.
    """

    exchange: ExchangeStopsPort
    journal: ExchangeStopJournalPort
    strategy_id: StrategyId
    clock: ClockPort
    step: Decimal = EXCHANGE_STEP

    def keep(
        self, pair: Pair, wanted: Protection | None, *, correlation_id: str
    ) -> tuple[DomainEvent, ...]:
        """Make the exchange hold ``wanted`` for ``pair``; record and return each change.

        Args:
            pair: The pair to keep.
            wanted: The stop the held position needs, or ``None`` when flat.
            correlation_id: Ties every row and alert of this pass together.

        Returns:
            The :class:`ExchangeStopChanged` rows and :class:`AnomalyDetected`
            alerts of this pass, in order, already recorded in the ledger.

        Raises:
            hermes.domain.errors.LedgerError: The journal cannot be read or written.
        """
        recorded = self.journal.latest_exchange_stop(self.strategy_id, pair)
        changes = _Pass(self, pair, correlation_id)
        changes.run(wanted, recorded)
        self.journal.record_exchange_stops(changes.events)
        return tuple(changes.events)


class _PassEndedError(Exception):
    """The exchange could not answer; the failure is recorded and the pass ends there."""


@dataclass
class _Pass:
    """One :meth:`ExchangeStopKeeper.keep` call: the exchange calls and the rows they produce."""

    keeper: ExchangeStopKeeper
    pair: Pair
    correlation_id: str
    events: list[DomainEvent] = field(default_factory=list)

    def run(self, wanted: Protection | None, recorded: ExchangeStopChanged | None) -> None:
        """Make the exchange hold ``wanted``, given the ledger's ``recorded`` row."""
        try:
            self._run(wanted, recorded)
        except _PassEndedError:
            return

    def _run(self, wanted: Protection | None, recorded: ExchangeStopChanged | None) -> None:
        exchange = self.keeper.exchange
        try:
            target = None if wanted is None else wanted.on_exchange_steps(self.keeper.step)
        except ValueError:
            self._fail(None, "below_exchange_step")
        try:
            resting = exchange.resting_stop_losses(pair=self.pair)
        except InfraError as exc:
            self._fail(target, "exchange_unreadable", exc)
        if recorded is not None and recorded.rests:
            recorded = self._ended(recorded, resting, target) or recorded
        if target is not None and _already_sold(recorded, target):
            target = None
        matching = (
            None if target is None else next((r for r in resting if r.protects(target)), None)
        )
        for stop in resting:
            if stop is not matching:
                self._cancel(stop, recorded, target)
        if target is None:
            return
        if matching is None:
            self._place(target)
        elif recorded is None or not recorded.rests or recorded.order_id != matching.order_id:
            self._row(StopAction.ADOPTED, target, matching.order_id, "found_on_exchange")

    def _ended(
        self,
        recorded: ExchangeStopChanged,
        resting: tuple[RestingStop, ...],
        target: Protection | None,
    ) -> ExchangeStopChanged | None:
        """Record what became of the stop the ledger names, when it no longer rests.

        Returns:
            The ``filled`` or ``gone`` row, or ``None`` when the stop still rests.
        """
        order_id = recorded.order_id
        if order_id is None or any(r.order_id == order_id for r in resting):
            return None
        try:
            fate = self.keeper.exchange.stop_loss_fate(order_id=order_id)
        except InfraError as exc:
            self._fail(target, "fate_unreadable", exc)
        if fate is StopFate.RESTING:
            self._fail(target, "listing_disagrees")
        sold = fate is StopFate.FILLED
        row = ExchangeStopChanged(
            occurred_at=self.keeper.clock.now(),
            correlation_id=self.correlation_id,
            strategy_id=self.keeper.strategy_id,
            pair=self.pair,
            action=StopAction.FILLED if sold else StopAction.GONE,
            entry_time=recorded.entry_time,
            order_id=order_id,
            qty=recorded.qty,
            stop_price=recorded.stop_price,
            reason="sold_on_exchange" if sold else "no_longer_on_exchange",
        )
        self.events.append(row)
        self._alert(
            AnomalySeverity.WARNING,
            f"the exchange stop on {self.pair} filled on the exchange; the strategy's own exit "
            "still runs, and it is the trade the ledger records"
            if sold
            else f"the exchange stop on {self.pair} is no longer on the exchange and sold "
            "nothing; a held position gets a new one now",
        )
        return row

    def _cancel(
        self, stop: RestingStop, recorded: ExchangeStopChanged | None, target: Protection | None
    ) -> None:
        try:
            self.keeper.exchange.cancel_order(order_id=stop.order_id)
        except InfraError as exc:
            self._fail(target, "cancel_failed", exc)
        ours = recorded is not None and recorded.order_id == stop.order_id
        if not ours:
            reason = "not_in_ledger"
        elif target is None:
            reason = "position_closed"
        else:
            reason = "stop_moved"
        self.events.append(
            ExchangeStopChanged(
                occurred_at=self.keeper.clock.now(),
                correlation_id=self.correlation_id,
                strategy_id=self.keeper.strategy_id,
                pair=self.pair,
                action=StopAction.CANCELLED,
                entry_time=recorded.entry_time if ours and recorded is not None else None,
                order_id=stop.order_id,
                qty=stop.qty,
                stop_price=stop.stop_price,
                reason=reason,
            )
        )

    def _place(self, target: Protection) -> None:
        order = Order(
            side=OrderSide.SELL,
            pair=self.pair,
            qty=Quantity(amount=target.qty, asset=self.pair.base),
            strategy_id=self.keeper.strategy_id,
        )
        for attempt in range(1, _PLACE_ATTEMPTS + 1):
            try:
                order_id = self.keeper.exchange.place_stop_loss(
                    order=order, stop_price=target.stop_price
                )
            except OrderNotPlacedError as exc:
                if attempt == _PLACE_ATTEMPTS:
                    self._fail(target, "not_placed", exc)
                continue
            except OrderStatusUnknownError as exc:
                self._fail(target, "status_unknown", exc, client_order_id=exc.client_order_id)
            except InfraError as exc:
                self._fail(target, "refused", exc)
            self._row(StopAction.PLACED, target, order_id, "protect_position")
            return

    def _row(self, action: StopAction, target: Protection, order_id: str, why: str) -> None:
        self.events.append(
            ExchangeStopChanged(
                occurred_at=self.keeper.clock.now(),
                correlation_id=self.correlation_id,
                strategy_id=self.keeper.strategy_id,
                pair=self.pair,
                action=action,
                entry_time=target.entry_time,
                order_id=order_id,
                qty=target.qty,
                stop_price=target.stop_price,
                reason=why,
            )
        )

    def _fail(
        self,
        target: Protection | None,
        reason: str,
        exc: InfraError | None = None,
        *,
        client_order_id: str = "",
    ) -> NoReturn:
        """Record the failure and its alert, then end the pass.

        Raises:
            _PassEndedError: Always.
        """
        self.events.append(
            ExchangeStopChanged(
                occurred_at=self.keeper.clock.now(),
                correlation_id=self.correlation_id,
                strategy_id=self.keeper.strategy_id,
                pair=self.pair,
                action=StopAction.FAILED,
                entry_time=None if target is None else target.entry_time,
                order_id=None,
                qty=None if target is None else target.qty,
                stop_price=None if target is None else target.stop_price,
                reason=reason,
            )
        )
        cause = "" if exc is None else f", {type(exc).__name__}"
        lookup = f"; look up client order id {client_order_id}" if client_order_id else ""
        self._alert(
            AnomalySeverity.CRITICAL,
            f"the exchange stop on {self.pair} could not be kept ({reason}{cause}){lookup}; "
            "until the next bar retries, only the strategy's own exit protects the position",
        )
        raise _PassEndedError

    def _alert(self, severity: AnomalySeverity, reason: str) -> None:
        self.events.append(
            AnomalyDetected(
                occurred_at=self.keeper.clock.now(),
                correlation_id=self.correlation_id,
                source=STOP_SOURCE,
                severity=severity,
                reason=reason,
            )
        )


def _already_sold(recorded: ExchangeStopChanged | None, target: Protection) -> bool:
    """Return whether the exchange already sold the position ``target`` protects."""
    return (
        recorded is not None
        and recorded.action is StopAction.FILLED
        and recorded.entry_time == target.entry_time
    )
