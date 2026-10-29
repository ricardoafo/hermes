"""When serve keeps the exchange-held stops, and what each position wants (#1080).

Serve calls the :class:`~hermes.application.execution.exchange_stops.ExchangeStopKeeper`
at three points, and only with real money (``allow_mainnet``) and a venue that
holds stops (``Composition.stop_exchange``). A shadow, or a composition with no
venue, places nothing: these functions return at once.

- :func:`reconcile`: at boot, after warm-up restored the positions and before
  any bar is handled, so the catch-up never trades on a stale stop. Each held
  position wants the stop the ledger recorded for it last.
- :func:`before_exit`: before an exit signal's order, the stop is cancelled
  first, so the quantity it locks on the exchange is free to sell.
- :func:`after_bar`: after each 4h bar, the position wants the stop the
  strategy evaluated on it: placed after an entry fills, moved when the
  strategy moved it, and cancelled once the position is flat.

The journal gets the pair and the action, never a price (ADR-012 D-02).

Reference: GitHub issue #1080; openspec/changes/1080-exchange-stops/change.md.
"""

from collections.abc import Callable
from typing import TYPE_CHECKING, Protocol

import structlog

from hermes.adapters.ledger.exchange_stop_rows import SqliteExchangeStopJournal
from hermes.application.execution.exchange_stops import ExchangeStopKeeper
from hermes.domain.events.anomaly_detected import AnomalyDetected
from hermes.domain.events.exchange_stop_changed import ExchangeStopChanged
from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.strategy.signal import OpenPosition, SignalKind, StrategySignal
from hermes.domain.strategy.swing.adapter import SwingStrategyAdapter
from hermes.domain.trading.exchange_stop import Protection
from hermes.domain.value_objects.pair import Pair
from hermes.infrastructure.logging import new_correlation_id
from hermes.ports.exchange_stops import ExchangeStopsPort

if TYPE_CHECKING:
    from hermes.entrypoints.serve import Composition

__all__ = ["after_bar", "before_exit", "reconcile"]

_log = structlog.get_logger(__name__)


class Alert(Protocol):
    """Sends an alert to serve's alert channels (``_BarLoop._alert``)."""

    async def __call__(self, alert: AnomalyDetected, *, failed: str) -> None:
        """Send ``alert``; log ``failed`` when a channel fails."""
        ...


async def reconcile(
    c: Composition, held: Callable[[Pair], OpenPosition | None], alert: Alert
) -> None:
    """Make the exchange's stops match the ledger's positions before any bar is handled.

    Args:
        c: Serve's composition.
        held: The open position warm-up restored for a pair, or ``None``.
        alert: Sends each alert the keeper raises.
    """
    venue = _venue(c)
    if venue is None:
        return
    for pair in c.pairs:
        position = c.tracker.snapshot.positions.get(pair)
        opened = held(pair) if position is not None else None
        if position is not None and (opened is None or opened.last_stop is None):
            # No stop recorded for it yet: what rests stays, and the next 4h bar sets it.
            _log.warning("serve.exchange_stop.no_stop_recorded", pair=str(pair))
            continue
        wanted = (
            None
            if position is None or opened is None or opened.last_stop is None
            else Protection(pair, opened.entry_time, position.quantity, opened.last_stop)
        )
        await _keep(c, venue, pair, wanted, alert)


async def before_exit(c: Composition, signal_: StrategySignal, pair: Pair, alert: Alert) -> None:
    """Cancel the pair's stop before an exit signal's order is sent."""
    venue = _venue(c)
    if signal_.kind is SignalKind.EXIT and venue is not None:
        await _keep(c, venue, pair, None, alert)


async def after_bar(c: Composition, bar: Bar, alert: Alert) -> None:
    """Rest the stop the strategy evaluated on this 4h bar, or none when flat."""
    venue = _venue(c)
    if bar.interval is not KlineInterval.H4 or venue is None:
        return
    strategy = c.strategies[bar.pair]
    record = strategy.last_stop_record if isinstance(strategy, SwingStrategyAdapter) else None
    position = c.tracker.snapshot.positions.get(bar.pair)
    if position is not None and record is None:
        return
    wanted = (
        None
        if position is None or record is None
        else Protection(bar.pair, record.entry_time, position.quantity, record.stop_price)
    )
    await _keep(c, venue, bar.pair, wanted, alert)


def _venue(c: Composition) -> ExchangeStopsPort | None:
    """Return the venue that keeps the stops: only with real money, never in a shadow."""
    return c.stop_exchange if c.config.allow_mainnet else None


async def _keep(
    c: Composition,
    venue: ExchangeStopsPort,
    pair: Pair,
    wanted: Protection | None,
    alert: Alert,
) -> None:
    keeper = ExchangeStopKeeper(
        exchange=venue,
        journal=SqliteExchangeStopJournal(c.ledger, c.sqlite_connection),
        strategy_id=c.strategy_id,
        clock=c.clock,
    )
    for event in keeper.keep(pair, wanted, correlation_id=new_correlation_id()):
        if isinstance(event, ExchangeStopChanged):
            _log.info(
                "serve.exchange_stop.changed",
                pair=str(pair),
                action=event.action.value,
                reason=event.reason,
            )
        elif isinstance(event, AnomalyDetected):
            await alert(event, failed="serve.exchange_stop.alert_failed")
