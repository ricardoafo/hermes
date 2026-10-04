"""Position views computed from ledger facts, with no I/O.

The ledger read model gathers plain facts about one open position (the
snapshot's size, cost and mark, the entry signal's stop, the open lots and
their EUR values) and this module turns them into the view objects. Keeping the
arithmetic here keeps it testable without SQLite.

EUR rule (change ``ledger-read-model`` D-4): the entry and the fees paid are the
EUR values the fills were priced at (#435); the current price, the stop and the
high are converted at the newest rate the ledger holds. When any of that is
missing the row is EUR pending and carries no EUR figure at all.

Reference: openspec/changes/ledger-read-model/change.md (D-3 to D-7).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Final

from hermes.application.console.format import fmt_eur_price, fmt_qty
from hermes.application.console.views import (
    PositionDetail,
    PositionRow,
    TaxPending,
    TimelineEvent,
)

__all__ = ["LotFacts", "PositionFacts", "build_detail"]

_ZERO: Final = Decimal(0)
_HUNDRED: Final = Decimal(100)
_SIDE: Final = "long"


@dataclass(frozen=True, slots=True)
class LotFacts:
    """One open lot of a position, as its BUY trade recorded it.

    Attributes:
        remaining_qty: Quantity still held from this lot.
        original_qty: Quantity the BUY filled.
        opened_at: Fill time of the BUY.
        price_eur: EUR price per unit at the fill-date rate; ``None`` while pending.
        fee_eur: EUR value of the whole BUY fee; ``None`` while pending.
    """

    remaining_qty: Decimal
    original_qty: Decimal
    opened_at: datetime
    price_eur: Decimal | None
    fee_eur: Decimal | None

    @property
    def priced(self) -> bool:
        """Return whether the BUY has its EUR values."""
        return self.price_eur is not None and self.fee_eur is not None


@dataclass(frozen=True, slots=True)
class PositionFacts:
    """Everything the ledger knows about one open position.

    Attributes:
        strategy: Strategy id that holds the position.
        coin: Base asset.
        timeframe: Bar interval of the strategy's stream for the coin.
        size: Quantity held, from the newest portfolio mark.
        cost_native: Notional paid for ``size`` in the quote currency, fees excluded.
        current_price: Newest mark price in the quote currency.
        stop_price: Stop level of the entry signal, in the quote currency.
        atr: ATR of the entry signal, in the quote currency.
        signal_at: Time of the entry signal.
        lots: The strategy's open lots of the coin.
        price_path: Mark prices since entry, oldest first.
        eur_per_usdt: EUR for one quote unit at the newest recorded rate, if any.

    Raises:
        ValueError: On a non-positive size, cost, price or ATR.
    """

    strategy: str
    coin: str
    timeframe: str
    size: Decimal
    cost_native: Decimal
    current_price: Decimal
    stop_price: Decimal
    atr: Decimal
    signal_at: datetime
    lots: tuple[LotFacts, ...]
    price_path: tuple[Decimal, ...]
    eur_per_usdt: Decimal | None

    def __post_init__(self) -> None:
        """Reject values no open position can have."""
        for name in ("size", "cost_native", "current_price", "atr"):
            if getattr(self, name) <= _ZERO:
                msg = f"PositionFacts.{name} must be > 0, got {getattr(self, name)}"
                raise ValueError(msg)

    @property
    def eur_known(self) -> bool:
        """Return whether every EUR input exists and the lots cover the size."""
        return (
            self.eur_per_usdt is not None
            and bool(self.lots)
            and all(lot.priced for lot in self.lots)
            and sum((lot.remaining_qty for lot in self.lots), _ZERO) == self.size
        )


@dataclass(frozen=True, slots=True)
class _Eur:
    rate: Decimal
    entry_total: Decimal
    fees_paid: Decimal

    @classmethod
    def of(cls, facts: PositionFacts) -> _Eur | None:
        if not facts.eur_known or facts.eur_per_usdt is None:
            return None
        entry_total = _ZERO
        fees_paid = _ZERO
        for lot in facts.lots:
            if lot.price_eur is None or lot.fee_eur is None:
                return None
            entry_total += lot.price_eur * lot.remaining_qty
            fees_paid += lot.fee_eur * lot.remaining_qty / lot.original_qty
        return cls(rate=facts.eur_per_usdt, entry_total=entry_total, fees_paid=fees_paid)


def build_detail(facts: PositionFacts) -> PositionDetail:
    """Return the detail view (and with it the list row) of one open position.

    Args:
        facts: What the ledger recorded about the position.

    Returns:
        The detail; its ``row`` is the list row. When EUR values are missing
        the row is ``eur_pending`` and every EUR money field is zero and unused.
    """
    eur = _Eur.of(facts)
    if eur is None:
        row = _row(facts, net=_ZERO, eur_per_usdt=Decimal(1), entry_price_eur=None)
        return PositionDetail(
            row=row,
            strategy=facts.strategy,
            entry_values=(),
            stop_moves=0,
            gross=_ZERO,
            fees_paid=_ZERO,
            fees_to_close=_ZERO,
            tax=TaxPending(),
            price_path=facts.price_path,
            timeline=_timeline(facts, eur),
        )
    now_value = facts.size * facts.current_price * eur.rate
    gross = now_value - eur.entry_total
    fees_to_close = now_value * eur.fees_paid / eur.entry_total
    row = _row(
        facts,
        net=gross - eur.fees_paid - fees_to_close,
        eur_per_usdt=eur.rate,
        entry_price_eur=eur.entry_total / facts.size,
    )
    return PositionDetail(
        row=row,
        strategy=facts.strategy,
        entry_values=(("atr", facts.atr * eur.rate),),
        stop_moves=0,
        gross=gross,
        fees_paid=eur.fees_paid,
        fees_to_close=fees_to_close,
        tax=TaxPending(),
        price_path=facts.price_path,
        timeline=_timeline(facts, eur),
    )


def _row(
    facts: PositionFacts,
    *,
    net: Decimal,
    eur_per_usdt: Decimal,
    entry_price_eur: Decimal | None,
) -> PositionRow:
    """Return the list row; ``entry_price_eur is None`` makes it EUR pending."""
    entry_price = facts.cost_native / facts.size
    cushion = facts.current_price - facts.stop_price
    if entry_price_eur is None:
        change = facts.current_price / entry_price - 1
    else:
        change = facts.current_price * eur_per_usdt / entry_price_eur - 1
    return PositionRow(
        coin=facts.coin,
        timeframe=facts.timeframe,
        side=_SIDE,
        size=facts.size,
        opened_at=min((lot.opened_at for lot in facts.lots), default=facts.signal_at),
        entry_price=entry_price,
        current_price=facts.current_price,
        change_pct=change * _HUNDRED,
        net=net,
        stop_price=facts.stop_price,
        high_since_entry=max((entry_price, *facts.price_path)),
        stop_distance_atr=cushion / facts.atr,
        stop_distance_pct=cushion * _HUNDRED / facts.current_price,
        eur_per_usdt=eur_per_usdt,
        entry_price_eur=entry_price_eur,
        eur_pending=entry_price_eur is None,
    )


def _timeline(facts: PositionFacts, eur: _Eur | None) -> tuple[TimelineEvent, ...]:
    events = [
        TimelineEvent(
            at=facts.signal_at,
            label=f"signal: {_SIDE} {facts.coin} on {facts.timeframe} close",
        )
    ]
    for lot in sorted(facts.lots, key=lambda lot: lot.opened_at):
        if eur is not None and lot.price_eur is not None:
            price = f"at {fmt_eur_price(lot.price_eur)}"
        else:
            price = "· EUR price pending"
        events.append(
            TimelineEvent(at=lot.opened_at, label=f"filled {fmt_qty(lot.original_qty)} {price}")
        )
    return tuple(events)
