"""Backfill of pending EUR values (change ``trade-eur-values`` R-3).

Re-prices every trade stored with ``eur_status = 'pending'`` from the ECB rate
of its fill date. Once the fill date is in the past, the closest prior business
day counts (weekends, holidays, same-day fills made before the ECB published).
Each priced trade is written with its lots and consumptions re-projected, so
gains that depended on it gain their EUR values.

Reference: ADR-016 item 5.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import structlog

from hermes.application.ledger.eur_pricer import EurPricer
from hermes.domain.fx.eur_pricing import fill_date_madrid
from hermes.domain.trading.trade_record import TradeEur
from hermes.domain.value_objects.money import Money
from hermes.ports.ledger import TradeLedgerPort

__all__ = ["BackfillReport", "backfill_pending_eur"]

_log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class BackfillReport:
    """Outcome of one backfill run.

    Attributes:
        filled: Trade ids that now carry EUR values.
        pending: Trade ids still pending (rate missing or not final).
    """

    filled: tuple[int, ...]
    pending: tuple[int, ...]


def backfill_pending_eur(
    ledger: TradeLedgerPort, pricer: EurPricer, *, today: date
) -> BackfillReport:
    """Price every pending trade whose rate is final and store it.

    Args:
        ledger: Trade-row persistence.
        pricer: EUR rate lookups (its per-day cache spans the run).
        today: Madrid calendar date of the run.

    Returns:
        Which trades were filled and which stay pending. Provider failures
        leave trades pending; they never raise.

    Raises:
        LedgerError: When reading or writing the ledger fails.
    """
    filled: list[int] = []
    pending: list[int] = []
    for trade in ledger.list_pending_trades():
        quote = trade.pair.quote
        pricing = pricer.price(quote, fill_date_madrid(trade.executed_at), today=today)
        eur = None
        if pricing is not None:
            eur = TradeEur.price(
                pricing, price=Money(amount=trade.price, currency=quote), fee=trade.fee
            )
        if eur is None:
            pending.append(trade.trade_id)
            continue
        ledger.apply_trade_eur(trade.trade_id, eur)
        filled.append(trade.trade_id)
    _log.info("eur_backfill_done", filled=len(filled), pending=len(pending))
    return BackfillReport(filled=tuple(filled), pending=tuple(pending))
