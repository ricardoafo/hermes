"""Backfill of pending EUR values (change ``trade-eur-values`` R-3).

Re-prices every trade stored with ``eur_status = 'pending'`` from the ECB rate
of its fill date. Once the fill date is in the past, the closest prior business
day counts (weekends, holidays, same-day fills made before the ECB published).
Each priced trade is written with its lots and consumptions re-projected, so
gains that depended on it gain their EUR values. A pending capital seed is
priced the same way, by the ECB rate of its seed date (change ``405-usdt-fifo``
D-1), so the quote-currency leg gains its EUR values too.

Reference: ADR-016 item 5.
"""

from dataclasses import dataclass
from datetime import date

import structlog

from hermes.application.ledger.eur_pricer import EurPricer
from hermes.domain.fx.eur_pricing import fill_date_madrid
from hermes.domain.trading.trade_record import TradeEur
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.money import Money
from hermes.ports.ledger import TradeLedgerPort

__all__ = ["BackfillReport", "backfill_pending_eur", "pending_rate_keys"]

_log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class BackfillReport:
    """Outcome of one backfill run.

    Attributes:
        filled: Trade ids that now carry EUR values.
        pending: Trade ids still pending (rate missing or not final).
        seeds_filled: Capital seed ids that now carry their EUR value.
        seeds_pending: Capital seed ids still pending.
    """

    filled: tuple[int, ...]
    pending: tuple[int, ...]
    seeds_filled: tuple[int, ...] = ()
    seeds_pending: tuple[int, ...] = ()


def pending_rate_keys(ledger: TradeLedgerPort) -> list[tuple[Currency, date]]:
    """Return the distinct ``(quote, Madrid date)`` rates the pending rows need.

    Raises:
        LedgerError: When reading the ledger fails.
    """
    keys = [(s.asset, fill_date_madrid(s.seeded_at)) for s in ledger.list_pending_seeds()]
    keys.extend(
        (t.pair.quote, fill_date_madrid(t.executed_at)) for t in ledger.list_pending_trades()
    )
    return list(dict.fromkeys(keys))


def backfill_pending_eur(
    ledger: TradeLedgerPort, pricer: EurPricer, *, today: date
) -> BackfillReport:
    """Price every pending seed and trade whose rate is final and store it.

    Args:
        ledger: Trade-row persistence.
        pricer: EUR rate lookups (its per-day cache spans the run).
        today: Madrid calendar date of the run.

    Returns:
        Which seeds and trades were filled and which stay pending. Provider
        failures leave them pending; they never raise.

    Raises:
        LedgerError: When reading or writing the ledger fails.
    """
    seeds_filled: list[int] = []
    seeds_pending: list[int] = []
    for seed in ledger.list_pending_seeds():
        seed_pricing = pricer.price(seed.asset, fill_date_madrid(seed.seeded_at), today=today)
        if seed_pricing is None:
            seeds_pending.append(seed.seed_id)
            continue
        ledger.apply_seed_eur(seed.seed_id, seed_pricing)
        seeds_filled.append(seed.seed_id)
    filled: list[int] = []
    pending: list[int] = []
    for trade in ledger.list_pending_trades():
        quote = trade.pair.quote
        pricing = pricer.price(quote, fill_date_madrid(trade.executed_at), today=today)
        eur = None
        if pricing is not None:
            eur = TradeEur.price(
                pricing,
                price=Money(amount=trade.price, currency=quote),
                fee=trade.fee,
                base=trade.pair.base,
            )
        if eur is None:
            pending.append(trade.trade_id)
            continue
        ledger.apply_trade_eur(trade.trade_id, eur)
        filled.append(trade.trade_id)
    _log.info(
        "eur_backfill_done",
        filled=len(filled),
        pending=len(pending),
        seeds_filled=len(seeds_filled),
        seeds_pending=len(seeds_pending),
    )
    return BackfillReport(
        filled=tuple(filled),
        pending=tuple(pending),
        seeds_filled=tuple(seeds_filled),
        seeds_pending=tuple(seeds_pending),
    )
