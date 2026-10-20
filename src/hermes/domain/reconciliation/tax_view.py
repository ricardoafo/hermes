"""Tax view of a month — realized EUR P&L in the month and the lots open at its end.

Both come from the same FIFO replay the ledger itself runs
(:func:`~hermes.domain.lots.eur_fifo.replay_fifo_eur`), fed with the trades up
to the period end, so a past month reads exactly as it stood at month end
even after later fills. A trade still waiting for its ECB rate leaves the
figures it feeds unknown; the view counts those trades instead of guessing.

Reference: openspec/archive/35-monthly-reconciliation/change.md D-4,
ADR-016 items 3 and 5.
"""

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Final

from returns.result import Failure, Result, Success

from hermes.domain.errors import DomainError
from hermes.domain.lots.eur_fifo import EurTrade, exact_sum, replay_fifo_eur
from hermes.domain.reconciliation.holdings import LedgerTrade
from hermes.domain.trading.order import OrderSide
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.precision import exact

__all__ = ["MonthPnl", "OpenLotLine", "TaxView", "tax_view"]

_ZERO: Final = Decimal("0")
# Same width as the FIFO replay, so a lot value is never rounded.


@dataclass(frozen=True, slots=True)
class MonthPnl:
    """Realized EUR figures of the SELLs filled in the month.

    Attributes:
        buys: BUY fills in the month.
        sells: SELL fills in the month.
        proceeds_eur: Gross EUR proceeds of the month's sales; ``None`` when
            any part is pending.
        cost_eur: EUR acquisition value of the lots those sales consumed.
        fees_eur: EUR fees of those sales.
        realized_eur: ``proceeds_eur - cost_eur - fees_eur``.
    """

    buys: int
    sells: int
    proceeds_eur: Decimal | None
    cost_eur: Decimal | None
    fees_eur: Decimal | None
    realized_eur: Decimal | None


@dataclass(frozen=True, slots=True)
class OpenLotLine:
    """A tax lot still open at the period end.

    Attributes:
        asset: Base asset of the lot.
        buy_trade_id: Ledger id of the BUY that opened it.
        opened_at: Fill time of that BUY.
        remaining_qty: Quantity not yet consumed.
        cost_basis_per_unit_eur: EUR acquisition value per unit, fee included;
            ``None`` while the BUY's rate is pending.
    """

    asset: Currency
    buy_trade_id: int
    opened_at: datetime
    remaining_qty: Decimal
    cost_basis_per_unit_eur: Decimal | None


@dataclass(frozen=True, slots=True)
class TaxView:
    """The month's realized P&L and the lot state at its end.

    Attributes:
        pnl: Realized figures of the month.
        open_lots: Lots open at the period end, oldest first.
        pending_trades: Trades up to the period end still without EUR values.
    """

    pnl: MonthPnl
    open_lots: tuple[OpenLotLine, ...]
    pending_trades: int

    @property
    def open_cost_eur(self) -> Decimal | None:
        """EUR acquisition value still held in open lots; ``None`` if any is pending."""
        parts: list[Decimal] = []
        for lot in self.open_lots:
            if lot.cost_basis_per_unit_eur is None:
                return None
            with exact():
                parts.append(lot.cost_basis_per_unit_eur * lot.remaining_qty)
        return exact_sum(parts)


def tax_view(
    trades: Sequence[LedgerTrade], *, start: datetime, end: datetime
) -> Result[TaxView, DomainError]:
    """Replay the trades before ``end`` and read the window ``[start, end)``.

    Args:
        trades: Every ledger trade, in any order; those at or after ``end``
            are ignored.
        start: Inclusive window start, UTC-aware.
        end: Exclusive window end, UTC-aware.

    Returns:
        ``Success(TaxView)``, or the replay's ``Failure`` when the book sells
        more of an asset than it holds (the ledger is inconsistent).
    """
    scoped = sorted(
        (t for t in trades if t.executed_at < end), key=lambda t: (t.executed_at, t.trade_id)
    )
    by_asset: dict[Currency, list[LedgerTrade]] = defaultdict(list)
    for trade in scoped:
        by_asset[trade.pair.base].append(trade)

    opened_at = {t.trade_id: t.executed_at for t in scoped}
    open_lots: list[OpenLotLine] = []
    proceeds: list[Decimal | None] = []
    costs: list[Decimal | None] = []
    fees: list[Decimal | None] = []
    for asset in sorted(by_asset, key=lambda a: a.value):
        replayed = replay_fifo_eur(asset, [_eur_trade(t) for t in by_asset[asset]])
        if isinstance(replayed, Failure):
            return Failure(replayed.failure())
        projection = replayed.unwrap()
        open_lots.extend(
            OpenLotLine(
                asset=asset,
                buy_trade_id=int(lot.buy_ref),
                opened_at=opened_at[int(lot.buy_ref)],
                remaining_qty=lot.remaining_qty,
                cost_basis_per_unit_eur=lot.cost_basis_per_unit_eur,
            )
            for lot in projection.lots
            if lot.remaining_qty > _ZERO
        )
        for piece in projection.consumptions:
            if start <= piece.consumed_at:
                proceeds.append(piece.proceeds_eur)
                costs.append(piece.cost_eur)
                fees.append(piece.fee_eur)

    in_month = [t for t in scoped if start <= t.executed_at]
    proceeds_eur, cost_eur, fees_eur = _total(proceeds), _total(costs), _total(fees)
    realized_eur = None
    if proceeds_eur is not None and cost_eur is not None and fees_eur is not None:
        realized_eur = exact_sum((proceeds_eur, -cost_eur, -fees_eur))
    pnl = MonthPnl(
        buys=sum(1 for t in in_month if t.side == OrderSide.BUY),
        sells=sum(1 for t in in_month if t.side == OrderSide.SELL),
        proceeds_eur=proceeds_eur,
        cost_eur=cost_eur,
        fees_eur=fees_eur,
        realized_eur=realized_eur,
    )
    return Success(
        TaxView(
            pnl=pnl,
            open_lots=tuple(sorted(open_lots, key=lambda lot: (lot.opened_at, lot.buy_trade_id))),
            pending_trades=sum(1 for t in scoped if t.eur_pending),
        )
    )


def _eur_trade(trade: LedgerTrade) -> EurTrade:
    return EurTrade(
        ref=str(trade.trade_id),
        side=trade.side,
        qty=trade.qty,
        executed_at=trade.executed_at,
        price_eur=trade.price_eur,
        fee_eur=trade.fee_eur,
    )


def _total(values: Iterable[Decimal | None]) -> Decimal | None:
    parts: list[Decimal] = []
    for value in values:
        if value is None:
            return None
        parts.append(value)
    return exact_sum(parts)
