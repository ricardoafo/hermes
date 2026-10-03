"""TradeRecorder — writes a fill to the ledger with its EUR values or as pending.

Runs after the exchange has filled the order, so nothing it does can block or
delay the fill. It prices the fill through :class:`EurPricer`, builds the
:class:`~hermes.domain.trading.trade_record.TradeRecord` and its
``TradeExecuted`` event, and hands both to the ledger in one transaction.
Strategy, sizing, fee and kill code never read what it writes (import-linter
contract ``eur-values-are-measurement-only``).

Reference: ADR-002 §3, ADR-016 items 3 and 5, change ``trade-eur-values`` R-2.
"""

from __future__ import annotations

from hermes.application.ledger.eur_pricer import EurPricer
from hermes.domain.events.trade_executed import TradeExecuted, TradeSide
from hermes.domain.fx.eur_pricing import fill_date_madrid
from hermes.domain.trading.fill import Fill
from hermes.domain.trading.trade_record import TradeEur, TradeRecord
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.price import Price
from hermes.domain.value_objects.quantity import Quantity
from hermes.ports.ledger import TradeLedgerPort

__all__ = ["TradeRecorder"]


class TradeRecorder:
    """Records fills as ledger trades.

    Args:
        ledger: Trade-row persistence.
        pricer: EUR rate lookups.
    """

    def __init__(self, ledger: TradeLedgerPort, pricer: EurPricer) -> None:
        """Bind the ledger and the pricer."""
        self._ledger = ledger
        self._pricer = pricer

    def record(self, fill: Fill, *, correlation_id: str) -> int:
        """Store ``fill`` as a trade row, priced in EUR when the rate is final.

        The fill date counts as "today": a rate dated before the fill date is
        not final yet, so the row is stored pending and the backfill prices it
        on a later day.

        Args:
            fill: The executed fill.
            correlation_id: Tracing id for the row and its event.

        Returns:
            The trade row id.

        Raises:
            LedgerError: When the ledger write fails. An FX failure never
                raises; it stores the row pending.
        """
        pair = fill.order.pair
        fill_date = fill_date_madrid(fill.timestamp)
        pricing = self._pricer.price(pair.quote, fill_date, today=fill_date)
        eur = None
        if pricing is not None:
            eur = TradeEur.price(
                pricing, price=Money(amount=fill.avg_price, currency=pair.quote), fee=fill.fee
            )
        record = TradeRecord.from_fill(fill, correlation_id=correlation_id, eur=eur)
        event = TradeExecuted(
            occurred_at=fill.timestamp,
            correlation_id=correlation_id,
            pair=pair,
            side=TradeSide(fill.order.side.value),
            qty=Quantity(amount=fill.executed_qty, asset=pair.base),
            price=Price(amount=fill.avg_price, pair=pair),
            fee=fill.fee,
        )
        return self._ledger.record_trade(record, event)
