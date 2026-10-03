"""Tests for FillKernel, the signal-to-fill path backtest and serve share.

Invariants (backtest-driver-mvp1-phase2 change.md D-2, R-2, R-4):

- An entry fills on the book's touch through the production router, reaches
  the portfolio, and returns a ``TradeExecuted`` v2 matching the fill.
- Entry sizing uses the starting capital, not current equity (pre-reg §2).
- An exit sells exactly the held quantity and leaves the pair flat.
- An exit without a position, a missing book, a fill-model rejection, a
  portfolio refusal and a kill trigger each decline without touching state.
- Replaying the returned trades rebuilds the same portfolio.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from hermes.application.execution.kernel import Declined, Executed, Halted
from hermes.application.portfolio.tracker import PortfolioTracker
from hermes.domain.events.trade_executed import TradeExecuted, TradeSide
from hermes.domain.fees.fee_schedule import FeeSchedule
from hermes.domain.market_data.orderbook import Orderbook
from hermes.domain.market_data.synthetic_orderbook import synthesize_orderbook
from hermes.domain.strategy.signal import SignalKind, SignalSide, StrategySignal
from hermes.domain.trading.fill import Fill, Rejection
from hermes.domain.trading.order import Order
from hermes.domain.value_objects.money import Money
from tests.support.fill_kernel import (
    BTC_USDT,
    STARTING_CASH,
    STRATEGY,
    calm_state,
    kernel,
    market,
    seed,
)

_T0 = datetime(2024, 1, 2, 4, 0, tzinfo=UTC)
_BPS = Decimal("10000")
_SIZED_NOTIONAL = STARTING_CASH * Decimal("0.01") / Decimal("1.5")
"""Router sizing at the pre-registered inputs: the ATR-baseline cap binds (R6 in the spec)."""


def _entry(price: Decimal, at: datetime = _T0) -> StrategySignal:
    return StrategySignal(
        kind=SignalKind.ENTRY,
        side=SignalSide.LONG,
        entry_price=price,
        stop_price=price * Decimal("0.9"),
        atr_context=Decimal("2"),
        signal_time=at,
    )


def _exit(stop: Decimal, at: datetime) -> StrategySignal:
    return StrategySignal(
        kind=SignalKind.EXIT,
        side=SignalSide.LONG,
        entry_price=None,
        stop_price=stop,
        atr_context=Decimal("2"),
        signal_time=at,
        exit_reason="chandelier",
    )


def _book(ref: Decimal, half_bps: str, at: datetime, depth: str = "1000") -> Orderbook:
    return synthesize_orderbook(BTC_USDT, ref, Decimal(half_bps), Decimal(depth), at)


def _executed(outcome: object) -> Executed:
    assert isinstance(outcome, Executed), f"expected Executed, got {outcome!r}"
    return outcome


def test_entry_fills_on_the_ask_touch_and_reaches_the_portfolio() -> None:
    """A 5 bps book fills a BUY at ref * 1.0005 and the position holds the sized quantity."""
    k = kernel()
    out = _executed(
        k.execute(
            _entry(Decimal("100")),
            BTC_USDT,
            market(_book(Decimal("100"), "5", _T0), _T0),
            correlation_id="c1",
        )
    )
    qty = _SIZED_NOTIONAL / Decimal("100")
    assert out.fill.avg_price == Decimal("100.05")
    assert out.fill.executed_qty == qty
    assert k.tracker.snapshot().positions[BTC_USDT].quantity == qty


def test_entry_trade_event_mirrors_the_fill_and_carries_the_strategy() -> None:
    """The TradeExecuted v2 copies side, qty, price, fee, time and strategy_id from the fill."""
    out = _executed(
        kernel().execute(
            _entry(Decimal("100")),
            BTC_USDT,
            market(_book(Decimal("100"), "5", _T0), _T0),
            correlation_id="c1",
        )
    )
    trade = out.trade
    assert TradeExecuted.__event_schema_version__ == 2
    assert (trade.side, trade.strategy_id, trade.correlation_id) == (TradeSide.BUY, STRATEGY, "c1")
    assert (trade.qty.amount, trade.price.amount, trade.fee, trade.occurred_at) == (
        out.fill.executed_qty,
        out.fill.avg_price,
        out.fill.fee,
        _T0,
    )


def test_entry_sizing_uses_starting_capital_after_a_loss() -> None:
    """After a losing round trip the next entry has the same size: §2 fixes the 5000 denominator."""
    k = kernel()
    t1, t2 = _T0 + timedelta(hours=4), _T0 + timedelta(hours=8)
    _executed(
        k.execute(
            _entry(Decimal("100")),
            BTC_USDT,
            market(_book(Decimal("100"), "5", _T0), _T0),
            correlation_id="a",
        )
    )
    _executed(
        k.execute(
            _exit(Decimal("50"), t1),
            BTC_USDT,
            market(_book(Decimal("50"), "15", t1), t1),
            correlation_id="b",
        )
    )
    assert k.tracker.snapshot().equity < STARTING_CASH
    second = _executed(
        k.execute(
            _entry(Decimal("100"), t2),
            BTC_USDT,
            market(_book(Decimal("100"), "5", t2), t2),
            correlation_id="c",
        )
    )
    assert second.fill.executed_qty == _SIZED_NOTIONAL / Decimal("100")


def test_exit_sells_the_whole_position_on_the_bid_touch() -> None:
    """A chandelier exit on a 15 bps book sells the held qty at stop * 0.9985 and goes flat."""
    k = kernel()
    entry = _executed(
        k.execute(
            _entry(Decimal("100")),
            BTC_USDT,
            market(_book(Decimal("100"), "5", _T0), _T0),
            correlation_id="a",
        )
    )
    t1 = _T0 + timedelta(hours=4)
    out = _executed(
        k.execute(
            _exit(Decimal("98"), t1),
            BTC_USDT,
            market(_book(Decimal("98"), "15", t1), t1),
            correlation_id="b",
        )
    )
    assert out.fill.executed_qty == entry.fill.executed_qty
    expected = Decimal("98") * (1 - Decimal("15") / _BPS)
    assert abs(out.fill.avg_price - expected) < Decimal("1e-20"), "VWAP rounds at 28 digits"
    assert out.trade.side is TradeSide.SELL
    assert dict(k.tracker.snapshot().positions) == {}


def test_exit_without_a_position_is_declined() -> None:
    """An exit for a flat pair declines as no-position and leaves the seed untouched."""
    k = kernel()
    out = k.execute(
        _exit(Decimal("98"), _T0),
        BTC_USDT,
        market(_book(Decimal("98"), "15", _T0), _T0),
        correlation_id="x",
    )
    assert out == Declined(
        reason=f"exit signal for {BTC_USDT} without a position", rule="no-position"
    )
    assert k.tracker.snapshot() == seed()


def test_missing_book_is_declined_before_routing() -> None:
    """With no book (zero-volume bar) the entry declines as no-book and nothing changes."""
    k = kernel()
    out = k.execute(_entry(Decimal("100")), BTC_USDT, market(None, _T0), correlation_id="x")
    assert isinstance(out, Declined)
    assert out.rule == "no-book"
    assert k.tracker.snapshot() == seed()


def test_entry_larger_than_the_book_is_rejected_with_the_fill_model_rejection() -> None:
    """A BUY bigger than the whole ask side declines as fill_model, carrying the M-04 Rejection."""
    k = kernel()
    out = k.execute(
        _entry(Decimal("100")),
        BTC_USDT,
        market(_book(Decimal("100"), "5", _T0, depth="0.01"), _T0),
        correlation_id="x",
    )
    assert isinstance(out, Declined)
    assert out.rule == "fill_model"
    assert out.rejection is not None
    assert out.rejection.reason.startswith("INSUFFICIENT_DEPTH")
    assert out.rejection.order.qty.amount == _SIZED_NOTIONAL / Decimal("100")
    assert k.tracker.snapshot() == seed()


def test_exit_larger_than_the_book_is_rejected_and_keeps_the_position() -> None:
    """A SELL bigger than the bid side declines as fill_model; the position stays open."""
    k = kernel()
    entry = _executed(
        k.execute(
            _entry(Decimal("100")),
            BTC_USDT,
            market(_book(Decimal("100"), "5", _T0), _T0),
            correlation_id="a",
        )
    )
    t1 = _T0 + timedelta(hours=4)
    out = k.execute(
        _exit(Decimal("98"), t1),
        BTC_USDT,
        market(_book(Decimal("98"), "15", t1, depth="0.01"), t1),
        correlation_id="b",
    )
    assert isinstance(out, Declined)
    assert out.rule == "fill_model"
    assert out.rejection is not None
    assert k.tracker.snapshot().positions[BTC_USDT].quantity == entry.fill.executed_qty


def test_kill_trigger_halts_the_entry() -> None:
    """A 20% drawdown in the experiment state trips §10: Halted, no fill, no state change."""
    k = kernel()
    out = k.execute(
        _entry(Decimal("100")),
        BTC_USDT,
        market(_book(Decimal("100"), "5", _T0), _T0, state=calm_state(max_drawdown=0.20)),
        correlation_id="x",
    )
    assert isinstance(out, Halted)
    assert out.breaker.decision.triggered is True
    assert k.tracker.snapshot() == seed()


@dataclass
class _OverpricedFillModel:
    """Fills every order at a price no 5000 USDT portfolio can pay."""

    def fill_order(
        self, order: Order, orderbook: Orderbook, clock: datetime, fees: FeeSchedule
    ) -> Fill | Rejection:
        return Fill(
            order=order,
            executed_qty=order.qty.amount,
            avg_price=Decimal("1e9"),
            fee=Money(Decimal("0"), order.pair.quote),
            timestamp=clock,
        )


def test_fill_the_portfolio_refuses_is_declined_and_not_recorded() -> None:
    """A BUY costing more than cash declines as portfolio; no trade is returned, state unchanged."""
    k = kernel(fill_model=_OverpricedFillModel())
    out = k.execute(
        _entry(Decimal("100")),
        BTC_USDT,
        market(_book(Decimal("100"), "5", _T0), _T0),
        correlation_id="x",
    )
    assert isinstance(out, Declined)
    assert out.rule == "portfolio"
    assert out.rejection is None
    assert k.tracker.snapshot() == seed()


_prices = st.decimals(min_value=Decimal("1"), max_value=Decimal("100000"), places=2)


@settings(max_examples=40, deadline=None)
@given(steps=st.lists(st.tuples(st.booleans(), _prices), min_size=1, max_size=12))
def test_replaying_the_returned_trades_rebuilds_the_same_portfolio(
    steps: list[tuple[bool, Decimal]],
) -> None:
    """For any entry/exit sequence, rebuilding from the trades equals the live tracker."""
    k = kernel()
    trades: list[TradeExecuted] = []
    for i, (is_entry, price) in enumerate(steps):
        at = _T0 + timedelta(hours=4 * i)
        signal = _entry(price, at) if is_entry else _exit(price, at)
        half = "5" if is_entry else "15"
        out = k.execute(
            signal, BTC_USDT, market(_book(price, half, at), at), correlation_id=f"c{i}"
        )
        if isinstance(out, Executed):
            trades.append(out.trade)
    rebuilt = PortfolioTracker.rebuilt(seed(), trades).unwrap()
    assert rebuilt.snapshot() == k.tracker.snapshot()


def test_kernel_exposes_its_tracker() -> None:
    """The kernel folds into the tracker it was given, so serve can keep using that tracker."""
    tracker = PortfolioTracker(seed())
    assert kernel(tracker=tracker).tracker is tracker
