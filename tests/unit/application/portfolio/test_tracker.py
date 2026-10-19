"""Tests for PortfolioTracker.

The accounting rules are tested on PortfolioSnapshot; here the tracker's own
job is pinned: hold the current snapshot, swap it only when a fill is
accepted, and hand out the risk state for it.

Reference: 308-signal-event-portfolio-state change.md D-4, D-7, Test plan.
"""

from __future__ import annotations

from decimal import Decimal

from hypothesis import given, settings
from returns.result import Failure, Success

from hermes.application.portfolio.tracker import PortfolioTracker, ReplayError
from hermes.domain.errors import InvalidPortfolioTransition
from hermes.domain.events.trade_executed import TradeExecuted, TradeSide
from hermes.domain.portfolio.snapshot import PortfolioSnapshot
from hermes.domain.trading.fill import Fill
from hermes.domain.trading.order import OrderSide
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.price import Price
from hermes.domain.value_objects.strategy_id import StrategyId
from tests.strategies.portfolio import (
    BTC_USDT,
    ETH_USDT,
    BarOp,
    FillOp,
    Operation,
    make_bar,
    make_fill,
    operations,
    starting_cash,
)

_STRATEGY = StrategyId("swing-donchian-4h")


def _seed(cash: Decimal = Decimal("5000")) -> PortfolioSnapshot:
    """Return a USDT snapshot seeded with ``cash``."""
    return PortfolioSnapshot.seed(_STRATEGY, Money(cash, Currency.USDT))


class TestSeedState:
    """Before any fill the tracker sits at its seed."""

    def test_state_equals_the_seed_before_any_fill(self) -> None:
        """A fresh tracker reports peak and current value equal to the seed cash, zero drawdown."""
        state = PortfolioTracker(_seed()).state()
        assert (state.peak_value, state.current_value, state.drawdown) == (
            Decimal("5000"),
            Decimal("5000"),
            Decimal("0"),
        )

    def test_no_position_is_open_before_any_fill(self) -> None:
        """A fresh tracker holds no position."""
        assert PortfolioTracker(_seed()).open_position_count() == 0

    def test_marking_a_bar_with_no_position_leaves_the_state_at_the_seed(self) -> None:
        """Phase 1 has no fills: marking bars never moves the state off the seed."""
        tracker = PortfolioTracker(_seed())
        tracker.mark(make_bar(BTC_USDT, "52000", minutes=240))
        assert tracker.state() == _seed().to_risk_state()


class TestAcceptedFill:
    """An accepted fill shows in the next state."""

    def test_accepted_fill_moves_the_state(self) -> None:
        """BUY 0.005 BTC at 50000 with fee 0.25 gives current value 4999.75 under a peak of 5000."""
        tracker = PortfolioTracker(_seed())
        tracker.apply_fill(make_fill(OrderSide.BUY, "0.005", "50000", fee="0.25"))
        state = tracker.state()
        assert (state.peak_value, state.current_value) == (Decimal("5000"), Decimal("4999.75"))

    def test_accepted_fill_opens_a_position(self) -> None:
        """After an accepted BUY the tracker counts one open position."""
        tracker = PortfolioTracker(_seed())
        tracker.apply_fill(make_fill(OrderSide.BUY, "0.005", "50000"))
        assert tracker.open_position_count() == 1

    def test_snapshot_is_the_state_after_the_last_accepted_fill(self) -> None:
        """BUY 0.005 at 50000 with fee 0.25: the snapshot holds 4749.75 cash and 0.005 BTC."""
        tracker = PortfolioTracker(_seed())
        tracker.apply_fill(make_fill(OrderSide.BUY, "0.005", "50000", fee="0.25"))
        snapshot = tracker.snapshot
        assert (snapshot.cash, snapshot.positions[BTC_USDT].quantity) == (
            Decimal("4749.75"),
            Decimal("0.005"),
        )

    def test_accepted_fill_returns_the_new_snapshot(self) -> None:
        """apply_fill returns Success holding the snapshot that is now current."""
        tracker = PortfolioTracker(_seed())
        result = tracker.apply_fill(make_fill(OrderSide.BUY, "0.005", "50000", fee="0.25"))
        assert isinstance(result, Success)
        assert result.unwrap().cash == Decimal("4749.75")

    def test_closing_the_position_brings_the_count_back_to_zero(self) -> None:
        """BUY then SELL of the same quantity leaves no open position."""
        tracker = PortfolioTracker(_seed())
        tracker.apply_fill(make_fill(OrderSide.BUY, "0.005", "50000"))
        tracker.apply_fill(make_fill(OrderSide.SELL, "0.005", "50000", minutes=1))
        assert tracker.open_position_count() == 0

    def test_mark_after_a_fill_updates_equity_and_peak(self) -> None:
        """A later bar at 52000 lifts current value and peak value to 5010."""
        tracker = PortfolioTracker(_seed())
        tracker.apply_fill(make_fill(OrderSide.BUY, "0.005", "50000"))
        tracker.mark(make_bar(BTC_USDT, "52000", minutes=240))
        state = tracker.state()
        assert (state.peak_value, state.current_value) == (Decimal("5010"), Decimal("5010"))

    def test_mark_for_another_pair_leaves_the_state_unchanged(self) -> None:
        """A bar for a pair that is not held does not move the state."""
        tracker = PortfolioTracker(_seed())
        tracker.apply_fill(make_fill(OrderSide.BUY, "0.005", "50000"))
        before = tracker.state()
        tracker.mark(make_bar(ETH_USDT, "3000", minutes=240))
        assert tracker.state() == before


class TestRefusedFill:
    """A refused fill returns Failure and the tracker keeps its state."""

    def test_refused_fill_returns_the_failure(self) -> None:
        """A BUY over cash returns Failure(InvalidPortfolioTransition) to the caller."""
        result = PortfolioTracker(_seed()).apply_fill(make_fill(OrderSide.BUY, "1", "6000"))
        assert isinstance(result, Failure)
        assert isinstance(result.failure(), InvalidPortfolioTransition)

    def test_refused_fill_leaves_the_state_unchanged(self) -> None:
        """After a refused BUY the risk state equals what it was before the call."""
        tracker = PortfolioTracker(_seed())
        tracker.apply_fill(make_fill(OrderSide.BUY, "0.005", "50000"))
        before = tracker.state()
        tracker.apply_fill(make_fill(OrderSide.BUY, "1", "6000", minutes=1))
        assert tracker.state() == before

    def test_refused_fill_leaves_the_position_count_unchanged(self) -> None:
        """A refused SELL of an unheld pair does not change the count of open positions."""
        tracker = PortfolioTracker(_seed())
        tracker.apply_fill(make_fill(OrderSide.BUY, "0.005", "50000"))
        tracker.apply_fill(make_fill(OrderSide.SELL, "1", "3000", pair=ETH_USDT, minutes=1))
        assert tracker.open_position_count() == 1


@settings(max_examples=60)
@given(cash=starting_cash, ops=operations())
def test_tracker_state_matches_the_pure_fold_of_the_same_operations(
    cash: Decimal, ops: list[Operation]
) -> None:
    """Driving the tracker equals folding the snapshot by hand: refused fills are skipped."""
    tracker = PortfolioTracker(_seed(cash))
    folded = _seed(cash)
    for op in ops:
        if isinstance(op, FillOp):
            tracker.apply_fill(op.fill)
            result = folded.after_fill(op.fill)
            if isinstance(result, Success):
                folded = result.unwrap()
        elif isinstance(op, BarOp):
            tracker.mark(op.bar)
            folded = folded.after_bar(op.bar)
    assert tracker.state() == folded.to_risk_state()
    assert tracker.open_position_count() == len(folded.positions)


def _trade_of(fill: Fill, correlation_id: str = "01J9Z8T3M5BQR2FVKN4XWY6PH1") -> TradeExecuted:
    """Record ``fill`` the way the ledger would: as the TradeExecuted that carries it."""
    pair = fill.order.pair
    return TradeExecuted(
        occurred_at=fill.timestamp,
        correlation_id=correlation_id,
        pair=pair,
        side=TradeSide(fill.order.side.value),
        qty=fill.order.qty,
        price=Price(amount=fill.avg_price, pair=pair),
        fee=fill.fee,
        strategy_id=fill.strategy_id,
    )


class TestRebuilt:
    """rebuilt() replays recorded trades through the same accounting as live fills."""

    def test_buy_then_partial_sell_matches_applying_the_fills_live(self) -> None:
        """Replaying BUY 0.005 then SELL 0.002 gives the live tracker's state and positions."""
        buy = make_fill(OrderSide.BUY, "0.005", "50000", fee="0.25")
        sell = make_fill(OrderSide.SELL, "0.002", "52000", fee="0.1", minutes=60)
        live = PortfolioTracker(_seed())
        live.apply_fill(buy)
        live.apply_fill(sell)
        result = PortfolioTracker.rebuilt(_seed(), [_trade_of(buy), _trade_of(sell)])
        assert isinstance(result, Success)
        rebuilt = result.unwrap()
        assert (rebuilt.state(), rebuilt.open_position_count()) == (
            live.state(),
            live.open_position_count(),
        )

    def test_buy_then_partial_sell_leaves_one_position_and_the_expected_value(self) -> None:
        """BUY 0.005 at 50000 then SELL 0.002 at 52000 keeps one position, value 5009.65."""
        buy = make_fill(OrderSide.BUY, "0.005", "50000", fee="0.25")
        sell = make_fill(OrderSide.SELL, "0.002", "52000", fee="0.1", minutes=60)
        result = PortfolioTracker.rebuilt(_seed(), [_trade_of(buy), _trade_of(sell)])
        assert isinstance(result, Success)
        tracker = result.unwrap()
        assert (tracker.open_position_count(), tracker.state().current_value.normalize()) == (
            1,
            Decimal("5009.65"),
        )

    def test_no_trades_returns_the_seed_unchanged(self) -> None:
        """An empty ledger rebuilds to the seed: same risk state, no position."""
        result = PortfolioTracker.rebuilt(_seed(), [])
        assert isinstance(result, Success)
        tracker = result.unwrap()
        assert (tracker.state(), tracker.open_position_count()) == (_seed().to_risk_state(), 0)

    def test_sell_without_a_position_fails_naming_the_trade(self) -> None:
        """A SELL with no prior BUY returns Failure(ReplayError) carrying its correlation id."""
        sell = _trade_of(make_fill(OrderSide.SELL, "0.005", "50000"), "01J9Z8T3M5BQR2FVKN4XWY6PH9")
        result = PortfolioTracker.rebuilt(_seed(), [sell])
        assert isinstance(result, Failure)
        assert isinstance(result.failure(), ReplayError)
        assert "01J9Z8T3M5BQR2FVKN4XWY6PH9" in str(result.failure())

    def test_replay_stops_at_the_first_failing_trade(self) -> None:
        """The Failure names the first bad trade, not a later one."""
        first = _trade_of(make_fill(OrderSide.SELL, "1", "3000", pair=ETH_USDT), "FIRSTBAD")
        second = _trade_of(
            make_fill(OrderSide.SELL, "1", "3000", pair=ETH_USDT, minutes=1), "LATERBAD"
        )
        result = PortfolioTracker.rebuilt(_seed(), [first, second])
        assert isinstance(result, Failure)
        assert "FIRSTBAD" in str(result.failure())
        assert "LATERBAD" not in str(result.failure())


@settings(max_examples=60)
@given(cash=starting_cash, ops=operations())
def test_rebuilt_equals_the_live_tracker_for_any_accepted_fill_sequence(
    cash: Decimal, ops: list[Operation]
) -> None:
    """Replaying the fills the live tracker accepted rebuilds the same state and positions."""
    live = PortfolioTracker(_seed(cash))
    recorded: list[TradeExecuted] = []
    for op in ops:
        if not isinstance(op, FillOp):
            continue
        if isinstance(live.apply_fill(op.fill), Success):
            recorded.append(_trade_of(op.fill))
    result = PortfolioTracker.rebuilt(_seed(cash), recorded)
    assert isinstance(result, Success)
    rebuilt = result.unwrap()
    assert rebuilt.state() == live.state()
    assert rebuilt.open_position_count() == live.open_position_count()


class TestOpenNotional:
    """open_notional is the marked value of open positions the §5.5 exposure cap reads."""

    def test_open_notional_is_zero_before_any_fill(self) -> None:
        """With no position open, nothing counts toward the total exposure cap."""
        assert PortfolioTracker(_seed()).open_notional() == Decimal("0")

    def test_open_notional_follows_the_latest_mark(self) -> None:
        """0.005 BTC bought at 50000 and marked at 52000 counts as 260 USDT, not its 250 cost."""
        tracker = PortfolioTracker(_seed())
        tracker.apply_fill(make_fill(OrderSide.BUY, "0.005", "50000"))
        tracker.mark(make_bar(BTC_USDT, "52000", minutes=240))
        assert tracker.open_notional() == Decimal("260")

    def test_open_notional_returns_to_zero_after_the_position_closes(self) -> None:
        """A closed position no longer uses exposure headroom."""
        tracker = PortfolioTracker(_seed())
        tracker.apply_fill(make_fill(OrderSide.BUY, "0.005", "50000"))
        tracker.apply_fill(make_fill(OrderSide.SELL, "0.005", "50000", minutes=1))
        assert tracker.open_notional() == Decimal("0")


class TestRestorePeak:
    """restore_peak seeds a rebuilt tracker with the peak a stored mark recorded."""

    def test_a_higher_stored_peak_is_taken(self) -> None:
        """Rebuilt from no trades (peak 5000), a stored peak of 5400 becomes the state's peak."""
        tracker = PortfolioTracker(_seed())
        tracker.restore_peak(Decimal("5400"))
        assert tracker.state().peak_value == Decimal("5400")

    def test_a_lower_stored_peak_is_ignored(self) -> None:
        """A stored peak under the rebuilt one leaves it as it was."""
        tracker = PortfolioTracker(_seed())
        tracker.restore_peak(Decimal("100"))
        assert tracker.state().peak_value == Decimal("5000")
