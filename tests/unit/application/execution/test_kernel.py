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

from hermes.application.execution.kernel import Declined, Executed, FillKernel, Halted
from hermes.application.portfolio.tracker import PortfolioTracker
from hermes.domain.events.trade_executed import TradeExecuted, TradeSide
from hermes.domain.fees.fee_schedule import FeeSchedule
from hermes.domain.market_data.orderbook import Orderbook, OrderbookLevel
from hermes.domain.market_data.synthetic_orderbook import synthesize_orderbook
from hermes.domain.strategy.signal import SignalKind, SignalSide, StrategySignal
from hermes.domain.trading.fill import Fill, Rejection
from hermes.domain.trading.order import Order
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.pair import Pair
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
_ETH_USDT = Pair(base=Currency.ETH, quote=Currency.USDT)
_SOL_USDT = Pair(base=Currency.SOL, quote=Currency.USDT)
_BPS = Decimal("10000")
_SIZED_NOTIONAL = STARTING_CASH * Decimal("0.05")
"""Pre-registration §5.4 sizing for these signals: a 10% stop puts the 1% risk budget at
500 USDT of notional, so the 5% per-position cap of the fixed capital (250) binds."""


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
    assert k.tracker.snapshot.positions[BTC_USDT].quantity == qty


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
    assert k.tracker.snapshot.equity < STARTING_CASH
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
    assert dict(k.tracker.snapshot.positions) == {}


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
    assert k.tracker.snapshot == seed()


def test_entry_for_a_pair_already_held_is_declined_and_buys_nothing() -> None:
    """A second entry on a held pair declines as already-held; the one position stays (#902)."""
    k = kernel()
    book = _book(Decimal("100"), "15", _T0)
    _executed(k.execute(_entry(Decimal("100")), BTC_USDT, market(book, _T0), correlation_id="a"))
    held = k.tracker.snapshot
    later = _T0 + timedelta(hours=4)
    out = k.execute(
        _entry(Decimal("105"), later),
        BTC_USDT,
        market(_book(Decimal("105"), "15", later), later),
        correlation_id="b",
    )
    assert (out, k.tracker.snapshot) == (
        Declined(reason=f"{BTC_USDT} is already held", rule="already-held"),
        held,
    )


def test_missing_book_is_declined_before_routing() -> None:
    """With no book (zero-volume bar) the entry declines as no-book and nothing changes."""
    k = kernel()
    out = k.execute(_entry(Decimal("100")), BTC_USDT, market(None, _T0), correlation_id="x")
    assert isinstance(out, Declined)
    assert out.rule == "no-book"
    assert k.tracker.snapshot == seed()


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
    assert k.tracker.snapshot == seed()


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
    assert k.tracker.snapshot.positions[BTC_USDT].quantity == entry.fill.executed_qty


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
    assert k.tracker.snapshot == seed()


@dataclass
class _OverpricedFillModel:
    """Fills every order at twice the signal price: 500 USDT for a 250 USDT entry."""

    def fill_order(
        self, order: Order, orderbook: Orderbook, clock: datetime, fees: FeeSchedule
    ) -> Fill | Rejection:
        return Fill(
            order=order,
            executed_qty=order.qty.amount,
            avg_price=Decimal("200"),
            fee=Money(Decimal("0"), order.pair.quote),
            timestamp=clock,
        )


def test_fill_the_portfolio_refuses_is_declined_and_not_recorded() -> None:
    """A BUY costing more than cash declines as portfolio; no trade is returned, state unchanged.

    The 500 USDT fill is inside the 750 USDT exposure cap, so only the 100 USDT
    of cash refuses it.
    """
    k = kernel(tracker=PortfolioTracker(seed(Decimal("100"))), fill_model=_OverpricedFillModel())
    out = k.execute(
        _entry(Decimal("100")),
        BTC_USDT,
        market(_book(Decimal("100"), "5", _T0), _T0),
        correlation_id="x",
    )
    assert isinstance(out, Declined)
    assert out.rule == "portfolio"
    assert out.rejection is None
    assert k.tracker.snapshot == seed(Decimal("100"))


def _enter_at(k: FillKernel, pair: Pair, ask_bps: str, at: datetime) -> object:
    """Enter ``pair`` at a signal price of 100 on a deep book whose ask is ``ask_bps`` above."""
    ask = Decimal("100") * (1 + Decimal(ask_bps) / _BPS)
    book = Orderbook(
        pair=pair,
        bids=(OrderbookLevel(price=Decimal("99.99"), quantity=Decimal("1000")),),
        asks=(OrderbookLevel(price=ask, quantity=Decimal("1000")),),
        snapshot_time=at,
    )
    return k.execute(_entry(Decimal("100"), at), pair, market(book, at), correlation_id=str(pair))


# SEV-1: no rubric row matches; pre-reg §5.5 total exposure cap must hold at the price paid
def test_an_entry_that_fits_the_cap_only_at_the_signal_price_is_declined() -> None:
    """Issue #864: BTC and ETH at zero spread, then SOL on a 30 bps ask would end at 750.75.

    The third 250 USDT entry fits the 750 cap at the signal price but costs
    more at the ask, so it declines as sizing and the portfolio keeps two
    positions at 500.
    """
    k = kernel()
    _executed(_enter_at(k, BTC_USDT, "0", _T0))
    _executed(_enter_at(k, _ETH_USDT, "0", _T0))
    out = _enter_at(k, _SOL_USDT, "30", _T0)
    assert isinstance(out, Declined)
    assert (
        out.rule,
        sorted(map(str, k.tracker.snapshot.positions)),
        k.tracker.open_notional(),
    ) == (
        "sizing",
        ["BTC/USDT", "ETH/USDT"],
        Decimal("500"),
    )


def test_an_entry_that_fits_the_cap_at_the_ask_still_fills() -> None:
    """The same third entry at zero spread costs exactly 250 and fills: exposure ends at 750."""
    k = kernel()
    for pair in (BTC_USDT, _ETH_USDT, _SOL_USDT):
        _executed(_enter_at(k, pair, "0", _T0))
    assert k.tracker.open_notional() == Decimal("750")


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
    assert rebuilt.snapshot == k.tracker.snapshot


def test_kernel_exposes_its_tracker() -> None:
    """The kernel folds into the tracker it was given, so serve can keep using that tracker."""
    tracker = PortfolioTracker(seed())
    assert kernel(tracker=tracker).tracker is tracker
