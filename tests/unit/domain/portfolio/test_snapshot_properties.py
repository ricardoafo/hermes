"""Property tests for PortfolioSnapshot: invariants over any sequence of fills and bars.

Each property folds a Hypothesis-drawn operation list into a snapshot seeded
with drawn cash and checks one invariant after every step. Amounts are bounded
(see ``tests/strategies/portfolio.py``) so every sum is exact and the checks
use ``==``, not a tolerance.

Reference: 308-signal-event-portfolio-state change.md D-5, Test plan.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterator
from decimal import Decimal
from fractions import Fraction

from hypothesis import assume, given, settings
from hypothesis import strategies as st
from returns.result import Failure, Success

from hermes.domain.errors import CurrencyMismatch, InvalidPortfolioTransition
from hermes.domain.portfolio.snapshot import PortfolioSnapshot
from hermes.domain.trading.order import OrderSide
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.strategy_id import StrategyId
from tests.strategies.portfolio import (
    BTC_USDC,
    BTC_USDT,
    BarOp,
    FillOp,
    Operation,
    fee_for,
    make_bar_at,
    make_fill,
    operations,
    starting_cash,
)

_SETTINGS = settings(max_examples=100, deadline=None, derandomize=True)
_STRATEGY = StrategyId("swing-donchian-4h")
_QTY = st.decimals(min_value=Decimal("0.0001"), max_value=Decimal("5"), places=4)
_PRICE = st.decimals(min_value=Decimal("1"), max_value=Decimal("100000"), places=2)


def _seed(cash: Decimal) -> PortfolioSnapshot:
    """Return a USDT snapshot seeded with ``cash``."""
    return PortfolioSnapshot.seed(_STRATEGY, Money(cash, Currency.USDT))


def _steps(
    seed: PortfolioSnapshot, ops: list[Operation]
) -> Iterator[tuple[Operation, PortfolioSnapshot, PortfolioSnapshot, bool]]:
    """Fold ``ops`` into ``seed``, yielding (op, before, after, accepted) per step.

    A refused fill leaves the snapshot as it was, which is what the tracker does.
    """
    snapshot = seed
    for op in ops:
        before = snapshot
        accepted = True
        if isinstance(op, FillOp):
            result = before.after_fill(op.fill)
            if isinstance(result, Success):
                snapshot = result.unwrap()
            else:
                accepted = False
        else:
            snapshot = before.after_bar(op.bar)
        yield op, before, snapshot, accepted


def _last(seed: PortfolioSnapshot, ops: list[Operation]) -> PortfolioSnapshot:
    """Return the snapshot after folding every operation of ``ops`` into ``seed``."""
    snapshot = seed
    for _op, _before, after, _accepted in _steps(seed, ops):
        snapshot = after
    return snapshot


@_SETTINGS
@given(cash=starting_cash, ops=operations())
def test_equity_equals_start_plus_realized_plus_unrealized(
    cash: Decimal, ops: list[Operation]
) -> None:
    """After every operation, equity == starting_cash + realized_pnl + unrealized_pnl, exactly."""
    for _op, _before, after, _accepted in _steps(_seed(cash), ops):
        assert after.equity == after.starting_cash + after.realized_pnl + after.unrealized_pnl


@_SETTINGS
@given(cash=starting_cash, ops=operations())
def test_position_quantity_is_the_signed_sum_of_accepted_fills(
    cash: Decimal, ops: list[Operation]
) -> None:
    """Held quantity per pair equals accepted BUY quantity minus accepted SELL quantity."""
    expected: defaultdict[Pair, Decimal] = defaultdict(Decimal)
    last = _seed(cash)
    for op, _before, after, accepted in _steps(_seed(cash), ops):
        if isinstance(op, FillOp) and accepted:
            sign = 1 if op.fill.order.side == OrderSide.BUY else -1
            expected[op.fill.order.pair] += sign * op.fill.executed_qty
        last = after
    held = {pair: position.quantity for pair, position in last.positions.items()}
    assert held == {pair: qty for pair, qty in expected.items() if qty != 0}


@_SETTINGS
@given(cash=starting_cash, ops=operations())
def test_cash_is_never_negative(cash: Decimal, ops: list[Operation]) -> None:
    """No sequence of fills, accepted or refused, drives cash below zero."""
    for _op, _before, after, _accepted in _steps(_seed(cash), ops):
        assert after.cash >= Decimal("0")


@_SETTINGS
@given(cash=starting_cash, ops=operations())
def test_bars_never_change_cash_realized_pnl_or_quantities(
    cash: Decimal, ops: list[Operation]
) -> None:
    """Marking with a bar moves only marks: cash, realized PnL and quantities are untouched."""
    for op, before, after, _accepted in _steps(_seed(cash), ops):
        if isinstance(op, BarOp):
            assert after.cash == before.cash
            assert after.realized_pnl == before.realized_pnl
            assert {p: x.quantity for p, x in after.positions.items()} == {
                p: x.quantity for p, x in before.positions.items()
            }


@_SETTINGS
@given(cash=starting_cash, ops=operations())
def test_buy_is_accepted_exactly_when_it_fits_in_cash(cash: Decimal, ops: list[Operation]) -> None:
    """A BUY is refused with InvalidPortfolioTransition iff qty*price + fee exceeds cash."""
    for op, before, _after, accepted in _steps(_seed(cash), ops):
        if isinstance(op, FillOp) and op.fill.order.side == OrderSide.BUY:
            cost = op.fill.executed_qty * op.fill.avg_price + op.fill.fee.amount
            assert accepted == (cost <= before.cash)
            if not accepted:
                result = before.after_fill(op.fill)
                assert isinstance(result, Failure)
                assert isinstance(result.failure(), InvalidPortfolioTransition)


@_SETTINGS
@given(cash=starting_cash, ops=operations())
def test_sell_is_accepted_exactly_when_the_position_covers_it(
    cash: Decimal, ops: list[Operation]
) -> None:
    """A SELL is refused with InvalidPortfolioTransition iff the pair is unheld or too small."""
    for op, before, _after, accepted in _steps(_seed(cash), ops):
        if isinstance(op, FillOp) and op.fill.order.side == OrderSide.SELL:
            pair = op.fill.order.pair
            held = before.positions.get(pair)
            covered = held is not None and op.fill.executed_qty <= held.quantity
            refusal: Failure[InvalidPortfolioTransition] | None = None
            if not covered:
                reason = (
                    f"SELL of {pair} without a position"
                    if held is None
                    else f"SELL of {op.fill.executed_qty} {pair} exceeds the position of "
                    f"{held.quantity}"
                )
                refusal = Failure(InvalidPortfolioTransition(reason=reason))
            outcome = None if accepted else before.after_fill(op.fill)
            assert (accepted, outcome) == (covered, refusal)


@_SETTINGS
@given(cash=starting_cash, ops=operations())
def test_peak_equity_never_decreases_and_covers_equity(cash: Decimal, ops: list[Operation]) -> None:
    """Peak equity never falls between steps and is always at least the current equity."""
    for _op, before, after, _accepted in _steps(_seed(cash), ops):
        assert after.peak_equity >= before.peak_equity
        assert after.peak_equity >= after.equity


@_SETTINGS
@given(cash=starting_cash, ops=operations())
def test_drawdown_stays_between_zero_and_one(cash: Decimal, ops: list[Operation]) -> None:
    """The drawdown the risk rules read is a fraction in [0, 1] after every operation."""
    for _op, _before, after, _accepted in _steps(_seed(cash), ops):
        assert 0 <= after.to_risk_state().drawdown <= 1


@_SETTINGS
@given(cash=starting_cash, ops=operations())
def test_marking_twice_with_one_bar_equals_marking_once(
    cash: Decimal, ops: list[Operation]
) -> None:
    """Applying the same bar a second time is a no-op."""
    for op, _before, after, _accepted in _steps(_seed(cash), ops):
        if isinstance(op, BarOp):
            assert after.after_bar(op.bar) == after


@_SETTINGS
@given(
    cash=starting_cash,
    ops=operations(),
    close=st.decimals(min_value=Decimal("1"), max_value=Decimal("100000"), places=2),
)
def test_a_bar_not_newer_than_the_mark_changes_nothing(
    cash: Decimal, ops: list[Operation], close: Decimal
) -> None:
    """A bar closing exactly at a position's mark time never re-marks it, whatever its close."""
    final = _last(_seed(cash), ops)
    for pair, position in final.positions.items():
        assert final.after_bar(make_bar_at(pair, close, position.marked_at)) == final


@_SETTINGS
@given(
    cash=starting_cash,
    qty=_QTY,
    buy_price=_PRICE,
    sell_price=_PRICE,
    buy_bps=st.integers(min_value=0, max_value=100),
    sell_bps=st.integers(min_value=0, max_value=100),
)
def test_full_close_leaves_no_position_and_equity_equals_cash(
    cash: Decimal,
    qty: Decimal,
    buy_price: Decimal,
    sell_price: Decimal,
    buy_bps: int,
    sell_bps: int,
) -> None:
    """BUY then SELL of the same quantity empties the book and books exactly the round-trip PnL."""
    buy_fee = fee_for(qty * buy_price, buy_bps)
    sell_fee = fee_for(qty * sell_price, sell_bps)
    assume(qty * buy_price + buy_fee <= cash)
    bought = _seed(cash).after_fill(make_fill(OrderSide.BUY, qty, buy_price, fee=buy_fee))
    assert isinstance(bought, Success)
    sold = bought.unwrap().after_fill(
        make_fill(OrderSide.SELL, qty, sell_price, fee=sell_fee, minutes=1)
    )
    assert isinstance(sold, Success)
    closed = sold.unwrap()
    assert dict(closed.positions) == {}
    assert closed.equity == closed.cash
    assert closed.realized_pnl == qty * (sell_price - buy_price) - buy_fee - sell_fee
    assert closed.cash == cash + closed.realized_pnl


@_SETTINGS
@given(
    first_qty=_QTY,
    second_qty=_QTY,
    first_price=_PRICE,
    second_price=_PRICE,
    percent=st.integers(min_value=1, max_value=99),
)
def test_partial_sell_releases_cost_pro_rata_within_half_a_quantum(
    first_qty: Decimal,
    second_qty: Decimal,
    first_price: Decimal,
    second_price: Decimal,
    percent: int,
) -> None:
    """A partial SELL releases cost * sold / held, off by at most half the 8-place rounding grid."""
    seeded = _seed(Decimal("100000000"))
    held = seeded.after_fill(make_fill(OrderSide.BUY, first_qty, first_price))
    assert isinstance(held, Success)
    both = held.unwrap().after_fill(make_fill(OrderSide.BUY, second_qty, second_price, minutes=1))
    assert isinstance(both, Success)
    before = both.unwrap().positions[BTC_USDT]
    sold_qty = before.quantity * Decimal(percent) / Decimal(100)
    sold = both.unwrap().after_fill(make_fill(OrderSide.SELL, sold_qty, "1", minutes=2))
    assert isinstance(sold, Success)
    after = sold.unwrap().positions[BTC_USDT]
    released = before.cost_basis - after.cost_basis
    exact = Fraction(before.cost_basis) * Fraction(sold_qty) / Fraction(before.quantity)
    assert abs(Fraction(released) - exact) <= Fraction(5, 10**9)
    assert after.quantity == before.quantity - sold_qty


@_SETTINGS
@given(cash=starting_cash, qty=_QTY)
def test_fill_in_another_quote_is_refused_whatever_the_amounts(cash: Decimal, qty: Decimal) -> None:
    """A fill quoted in USDC never changes a USDT snapshot: it returns CurrencyMismatch."""
    result = _seed(cash).after_fill(make_fill(OrderSide.BUY, qty, "1", pair=BTC_USDC))
    assert isinstance(result, Failure)
    assert result.failure() == CurrencyMismatch(expected="USDT", actual="USDC")
