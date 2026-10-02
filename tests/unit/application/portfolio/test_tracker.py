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

from hermes.application.portfolio.tracker import PortfolioTracker
from hermes.domain.errors import InvalidPortfolioTransition
from hermes.domain.portfolio.snapshot import PortfolioSnapshot
from hermes.domain.trading.order import OrderSide
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.money import Money
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


@settings(max_examples=60, deadline=None, derandomize=True)
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
