"""Tests for the month tax view (change 35-monthly-reconciliation D-4).

Invariants protected here:

- only SELLs filled inside the month count toward its realized EUR P&L,
  valued FIFO against lots that may predate the month;
- the lot state is read as it stood at the period end, ignoring later fills;
- a trade without EUR values makes the figures it feeds unknown, never zero;
- a book that sells more than it holds is a failure, not a report.
"""

from datetime import UTC, datetime
from decimal import Decimal

from returns.result import Failure

from hermes.domain.lots.selection import InsufficientInventory
from hermes.domain.reconciliation.holdings import LedgerTrade
from hermes.domain.reconciliation.month import ReconciliationMonth
from hermes.domain.reconciliation.tax_view import TaxView, tax_view
from hermes.domain.trading.order import OrderSide
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.pair import Pair

_BTC = Pair(base=Currency.BTC, quote=Currency.USDT)
_SEPT = ReconciliationMonth(2026, 9)


def _trade(
    trade_id: int,
    side: OrderSide,
    qty: str,
    price_eur: str | None,
    fee_eur: str | None,
    at: datetime,
) -> LedgerTrade:
    return LedgerTrade(
        trade_id=trade_id,
        pair=_BTC,
        side=side,
        qty=Decimal(qty),
        price=Decimal(1),
        fee=Money(amount=Decimal(0), currency=Currency.USDT),
        executed_at=at,
        price_eur=None if price_eur is None else Decimal(price_eur),
        fee_eur=None if fee_eur is None else Decimal(fee_eur),
    )


def _view(trades: list[LedgerTrade]) -> TaxView:
    return tax_view(trades, start=_SEPT.start, end=_SEPT.end).unwrap()


_AUG = datetime(2026, 8, 10, 10, tzinfo=UTC)
_SEP = datetime(2026, 9, 10, 10, tzinfo=UTC)
_OCT = datetime(2026, 10, 10, 10, tzinfo=UTC)


def test_a_sell_in_the_month_realizes_against_an_older_lot() -> None:
    """August BUY 1 at 100 (fee 1), September SELL 0.5 at 150 (fee 0.5): realized 24.

    proceeds 75, cost 0.5 * 101 = 50.5, fee 0.5, realized 24.
    """
    view = _view(
        [
            _trade(1, OrderSide.BUY, "1", "100", "1", _AUG),
            _trade(2, OrderSide.SELL, "0.5", "150", "0.5", _SEP),
        ]
    )

    pnl = view.pnl
    assert (pnl.buys, pnl.sells) == (0, 1)
    assert (pnl.proceeds_eur, pnl.cost_eur, pnl.fees_eur) == (
        Decimal(75),
        Decimal("50.5"),
        Decimal("0.5"),
    )
    assert pnl.realized_eur == Decimal(24)


def test_a_sell_before_the_month_is_not_in_its_pnl() -> None:
    """An August sale is August's gain; September realized nothing."""
    view = _view(
        [
            _trade(1, OrderSide.BUY, "1", "100", "0", _AUG),
            _trade(2, OrderSide.SELL, "1", "150", "0", _AUG.replace(day=20)),
        ]
    )

    assert (view.pnl.sells, view.pnl.realized_eur) == (0, Decimal(0))


def test_lots_are_read_at_the_period_end_ignoring_later_fills() -> None:
    """An October sale does not shrink the lot September closed with."""
    view = _view(
        [
            _trade(1, OrderSide.BUY, "2", "100", "2", _SEP),
            _trade(2, OrderSide.SELL, "2", "150", "0", _OCT),
        ]
    )

    (lot,) = view.open_lots
    assert (lot.buy_trade_id, lot.remaining_qty, lot.cost_basis_per_unit_eur) == (
        1,
        Decimal(2),
        Decimal(101),
    )
    assert view.open_cost_eur == Decimal(202)


def test_a_pending_trade_makes_the_pnl_unknown_and_is_counted() -> None:
    """A SELL without an ECB rate leaves proceeds and gain unknown, not zero."""
    view = _view(
        [
            _trade(1, OrderSide.BUY, "1", "100", "0", _AUG),
            _trade(2, OrderSide.SELL, "1", None, None, _SEP),
        ]
    )

    assert (view.pnl.proceeds_eur, view.pnl.realized_eur, view.pending_trades) == (None, None, 1)


def test_a_pending_buy_makes_the_open_lot_value_unknown() -> None:
    """An open lot with no EUR basis has no acquisition value yet."""
    view = _view([_trade(1, OrderSide.BUY, "1", None, None, _SEP)])

    assert (view.open_cost_eur, view.pending_trades) == (None, 1)


def test_a_book_selling_more_than_it_holds_fails() -> None:
    """A SELL beyond the open quantity means the ledger is inconsistent."""
    result = tax_view(
        [_trade(1, OrderSide.SELL, "1", "100", "0", _SEP)], start=_SEPT.start, end=_SEPT.end
    )

    assert isinstance(result, Failure)
    assert isinstance(result.failure(), InsufficientInventory)


def test_an_empty_book_is_a_quiet_month() -> None:
    """No trades: no fills, zero realized, no lots, nothing pending."""
    view = _view([])

    assert (view.pnl.buys, view.pnl.sells, view.pnl.realized_eur) == (0, 0, Decimal(0))
    assert (view.open_lots, view.pending_trades, view.open_cost_eur) == ((), 0, Decimal(0))
