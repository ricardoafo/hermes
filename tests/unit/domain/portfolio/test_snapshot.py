"""Example-based tests for PortfolioSnapshot.

Each test pins one accounting rule with numbers worked out by hand. The
invariants that must hold for every sequence of operations live in
``test_snapshot_properties.py``.

Reference: 308-signal-event-portfolio-state change.md D-5, Design (transition rules).
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest
from returns.result import Failure, Success

from hermes.domain.errors import CurrencyMismatch, InvalidPortfolioTransition
from hermes.domain.portfolio.position import Position
from hermes.domain.portfolio.snapshot import PortfolioSnapshot
from hermes.domain.trading.fill import Fill
from hermes.domain.trading.order import OrderSide
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.strategy_id import StrategyId
from tests.strategies.portfolio import (
    BTC_USDC,
    BTC_USDT,
    ETH_USDT,
    at,
    make_bar,
    make_fill,
)

_STRATEGY = StrategyId("swing-donchian-4h")
_BUY = OrderSide.BUY
_SELL = OrderSide.SELL


def _seed(cash: str = "5000") -> PortfolioSnapshot:
    """Return a snapshot seeded with ``cash`` USDT."""
    return PortfolioSnapshot.seed(_STRATEGY, Money(Decimal(cash), Currency.USDT))


def _applied(snapshot: PortfolioSnapshot, *fills: Fill) -> PortfolioSnapshot:
    """Apply ``fills`` in order, failing the test if any is refused."""
    for fill in fills:
        result = snapshot.after_fill(fill)
        assert isinstance(result, Success), f"fill refused: {result}"
        snapshot = result.unwrap()
    return snapshot


class TestSeed:
    """seed builds an all-cash snapshot and refuses non-positive capital."""

    def test_seed_holds_all_cash_and_no_positions(self) -> None:
        """A seed of 5000 USDT has 5000 cash, equity and peak, no positions and no PnL."""
        snap = _seed("5000")
        assert (snap.cash, snap.equity, snap.peak_equity, snap.starting_cash) == (
            Decimal("5000"),
            Decimal("5000"),
            Decimal("5000"),
            Decimal("5000"),
        )
        assert (dict(snap.positions), snap.realized_pnl, snap.quote) == (
            {},
            Decimal("0"),
            Currency.USDT,
        )

    @pytest.mark.parametrize("amount", ["0", "-1"])
    def test_seed_rejects_non_positive_cash(self, amount: str) -> None:
        """A seed needs positive capital: zero or negative raises ValueError."""
        with pytest.raises(ValueError, match="positive cash"):
            _seed(amount)


class TestConstruction:
    """Direct construction refuses states a transition could never produce."""

    def test_negative_cash_is_rejected(self) -> None:
        """A snapshot cannot be built with cash below zero."""
        with pytest.raises(ValueError, match="cash must be >= 0"):
            replace(_seed(), cash=Decimal("-0.01"))

    def test_non_positive_starting_cash_is_rejected(self) -> None:
        """A snapshot cannot be built with zero starting cash."""
        with pytest.raises(ValueError, match="starting_cash must be > 0"):
            replace(_seed(), starting_cash=Decimal("0"))

    def test_position_keyed_under_another_pair_is_rejected(self) -> None:
        """A position filed under the wrong pair key raises ValueError."""
        position = Position(BTC_USDT, Decimal("1"), Decimal("10"), Decimal("10"), at(0))
        with pytest.raises(ValueError, match="keyed under"):
            replace(_seed(), positions={ETH_USDT: position})

    def test_position_in_another_quote_is_rejected(self) -> None:
        """A position quoted in USDC cannot sit in a USDT snapshot."""
        position = Position(BTC_USDC, Decimal("1"), Decimal("10"), Decimal("10"), at(0))
        with pytest.raises(ValueError, match="not quoted in the snapshot currency"):
            replace(_seed(), positions={BTC_USDC: position})

    def test_positions_mapping_is_read_only(self) -> None:
        """The exposed positions mapping cannot be mutated in place."""
        snap = _applied(_seed(), make_fill(_BUY, "0.005", "50000"))
        with pytest.raises(TypeError):
            snap.positions[ETH_USDT] = snap.positions[BTC_USDT]  # type: ignore[index]


class TestBuyThenSellWorkedExample:
    """The spec's worked example: BUY 0.005 BTC at 50000, then SELL it at 52000."""

    def test_buy_moves_cash_into_a_position_and_pays_the_fee(self) -> None:
        """BUY 0.005 at 50000 with fee 0.25 leaves cash 4749.75 and equity 4999.75."""
        snap = _applied(_seed(), make_fill(_BUY, "0.005", "50000", fee="0.25"))
        assert (snap.cash, snap.equity) == (Decimal("4749.75"), Decimal("4999.75"))

    def test_buy_books_the_fee_as_realized_loss(self) -> None:
        """The BUY fee is realized immediately: realized PnL is minus 0.25."""
        snap = _applied(_seed(), make_fill(_BUY, "0.005", "50000", fee="0.25"))
        assert snap.realized_pnl == Decimal("-0.25")

    def test_buy_records_quantity_cost_and_mark(self) -> None:
        """The position holds 0.005 BTC at cost 250, marked at the fill price and time."""
        snap = _applied(_seed(), make_fill(_BUY, "0.005", "50000", fee="0.25", minutes=7))
        assert snap.positions[BTC_USDT] == Position(
            BTC_USDT, Decimal("0.005"), Decimal("250"), Decimal("50000"), at(7)
        )

    def test_full_sell_realizes_profit_net_of_both_fees(self) -> None:
        """SELL at 52000 with fee 0.26 gives cash 5009.49 and realized PnL 9.49."""
        snap = _applied(
            _seed(),
            make_fill(_BUY, "0.005", "50000", fee="0.25"),
            make_fill(_SELL, "0.005", "52000", fee="0.26", minutes=1),
        )
        assert (snap.cash, snap.realized_pnl) == (Decimal("5009.49"), Decimal("9.49"))

    def test_full_sell_drops_the_position(self) -> None:
        """A SELL of the whole quantity removes the position and leaves equity equal to cash."""
        snap = _applied(
            _seed(),
            make_fill(_BUY, "0.005", "50000", fee="0.25"),
            make_fill(_SELL, "0.005", "52000", fee="0.26", minutes=1),
        )
        assert (dict(snap.positions), snap.equity) == ({}, Decimal("5009.49"))


class TestPositionAveraging:
    """Average cost: a second BUY adds quantity and cost, and re-marks."""

    def test_second_buy_adds_quantity_and_cost(self) -> None:
        """BUY 1 at 100 then BUY 2 at 101 holds 3 with total cost 302."""
        snap = _applied(
            _seed("1000"),
            make_fill(_BUY, "1", "100"),
            make_fill(_BUY, "2", "101", minutes=1),
        )
        position = snap.positions[BTC_USDT]
        assert (position.quantity, position.cost_basis) == (Decimal("3"), Decimal("302"))

    def test_second_buy_marks_at_its_own_fill_price_and_time(self) -> None:
        """After BUY 1 at 100 then BUY 2 at 101 the mark is 101 at the second fill's time."""
        snap = _applied(
            _seed("1000"),
            make_fill(_BUY, "1", "100"),
            make_fill(_BUY, "2", "101", minutes=9),
        )
        position = snap.positions[BTC_USDT]
        assert (position.mark_price, position.marked_at) == (Decimal("101"), at(9))

    def test_positions_in_two_pairs_are_independent(self) -> None:
        """A BTC buy and an ETH buy produce two positions keyed by pair."""
        snap = _applied(
            _seed("1000"),
            make_fill(_BUY, "1", "100"),
            make_fill(_BUY, "2", "10", pair=ETH_USDT),
        )
        assert {pair: p.quantity for pair, p in snap.positions.items()} == {
            BTC_USDT: Decimal("1"),
            ETH_USDT: Decimal("2"),
        }


class TestPartialSell:
    """A partial SELL releases a proportional share of the cost."""

    def test_partial_sell_releases_a_proportional_cost(self) -> None:
        """Selling 0.4 of 1 BTC bought at 50000 realizes 400 and leaves cost 30000 on 0.6."""
        snap = _applied(
            _seed("100000"),
            make_fill(_BUY, "1", "50000"),
            make_fill(_SELL, "0.4", "51000", minutes=1),
        )
        position = snap.positions[BTC_USDT]
        assert (snap.realized_pnl, position.quantity, position.cost_basis) == (
            Decimal("400"),
            Decimal("0.6"),
            Decimal("30000"),
        )

    def test_partial_sell_rounds_the_released_cost_to_eight_places(self) -> None:
        """Cost 302 over 3 BTC, selling 1 releases 100.66666667 and leaves 201.33333333."""
        snap = _applied(
            _seed("1000"),
            make_fill(_BUY, "1", "100"),
            make_fill(_BUY, "2", "101", minutes=1),
            make_fill(_SELL, "1", "102", minutes=2),
        )
        assert (snap.realized_pnl, snap.positions[BTC_USDT].cost_basis) == (
            Decimal("1.33333333"),
            Decimal("201.33333333"),
        )

    def test_partial_sell_keeps_the_identity_exact_after_rounding(self) -> None:
        """After a rounded release, equity still equals start plus realized plus unrealized."""
        snap = _applied(
            _seed("1000"),
            make_fill(_BUY, "1", "100"),
            make_fill(_BUY, "2", "101", minutes=1),
            make_fill(_SELL, "1", "102", minutes=2),
        )
        assert snap.equity == snap.starting_cash + snap.realized_pnl + snap.unrealized_pnl

    def test_partial_sell_never_leaves_negative_cost_on_an_off_grid_cost(self) -> None:
        """A cost smaller than the rounding grid is released without going negative."""
        held = Position(BTC_USDT, Decimal("3"), Decimal("1.6e-8"), Decimal("1"), at(0))
        snap = replace(_seed("1000"), positions={BTC_USDT: held})
        sold = _applied(snap, make_fill(_SELL, "1.5", "1", minutes=1))
        assert sold.positions[BTC_USDT].cost_basis >= Decimal("0")


class TestRefusedFills:
    """A refused fill returns Failure with a specific error and nothing moves."""

    def test_buy_over_cash_is_refused(self) -> None:
        """BUY costing 5001 against 5000 cash returns InvalidPortfolioTransition."""
        result = _seed("5000").after_fill(make_fill(_BUY, "1", "5001"))
        assert isinstance(result, Failure)
        assert isinstance(result.failure(), InvalidPortfolioTransition)

    def test_buy_whose_fee_tips_it_over_cash_is_refused(self) -> None:
        """The fee counts toward the cost: 5000 notional plus 0.01 fee exceeds 5000 cash."""
        result = _seed("5000").after_fill(make_fill(_BUY, "1", "5000", fee="0.01"))
        assert isinstance(result, Failure)

    def test_buy_costing_exactly_the_cash_is_accepted(self) -> None:
        """A BUY that spends all the cash is the boundary and is accepted, leaving zero cash."""
        snap = _applied(_seed("5000"), make_fill(_BUY, "1", "4999.99", fee="0.01"))
        assert snap.cash == Decimal("0")

    def test_sell_without_a_position_is_refused(self) -> None:
        """Selling a pair that is not held returns InvalidPortfolioTransition."""
        result = _seed().after_fill(make_fill(_SELL, "0.001", "50000"))
        assert isinstance(result, Failure)
        assert isinstance(result.failure(), InvalidPortfolioTransition)

    def test_sell_above_the_position_is_refused(self) -> None:
        """Selling 0.006 of a 0.005 position returns InvalidPortfolioTransition."""
        snap = _applied(_seed(), make_fill(_BUY, "0.005", "50000"))
        result = snap.after_fill(make_fill(_SELL, "0.006", "50000", minutes=1))
        assert isinstance(result, Failure)
        assert isinstance(result.failure(), InvalidPortfolioTransition)

    def test_sell_whose_fee_would_overdraw_cash_is_refused(self) -> None:
        """A SELL whose fee exceeds proceeds plus cash would go below zero and is refused."""
        snap = _applied(_seed("100"), make_fill(_BUY, "1", "99", fee="0.5"))
        result = snap.after_fill(make_fill(_SELL, "1", "1", fee="5", minutes=1))
        assert isinstance(result, Failure)
        assert isinstance(result.failure(), InvalidPortfolioTransition)

    def test_fill_in_another_quote_returns_currency_mismatch(self) -> None:
        """A BTC/USDC fill against a USDT snapshot returns CurrencyMismatch(USDT, USDC)."""
        result = _seed().after_fill(make_fill(_BUY, "0.001", "50000", pair=BTC_USDC))
        assert isinstance(result, Failure)
        assert result.failure() == CurrencyMismatch(expected="USDT", actual="USDC")

    def test_fee_in_another_currency_returns_currency_mismatch(self) -> None:
        """A fee charged in USDC on a BTC/USDT fill returns CurrencyMismatch(USDT, USDC)."""
        fill = make_fill(_BUY, "0.001", "50000", fee="0.05", fee_currency=Currency.USDC)
        result = _seed().after_fill(fill)
        assert isinstance(result, Failure)
        assert result.failure() == CurrencyMismatch(expected="USDT", actual="USDC")


class TestMarking:
    """after_bar re-marks held pairs at the bar close, newest bar wins."""

    def test_newer_bar_marks_the_position_and_raises_equity(self) -> None:
        """A bar closing at 52000 lifts 0.005 BTC bought at 50000 to equity 5010."""
        snap = _applied(_seed(), make_fill(_BUY, "0.005", "50000"))
        marked = snap.after_bar(make_bar(BTC_USDT, "52000", minutes=240))
        assert (marked.equity, marked.unrealized_pnl, marked.cash) == (
            Decimal("5010"),
            Decimal("10"),
            Decimal("4750"),
        )

    def test_peak_follows_a_new_high_and_holds_through_a_pullback(self) -> None:
        """Peak equity rises with a higher mark and stays when a later bar marks lower."""
        snap = _applied(_seed(), make_fill(_BUY, "0.005", "50000"))
        high = snap.after_bar(make_bar(BTC_USDT, "52000", minutes=240))
        low = high.after_bar(make_bar(BTC_USDT, "48000", minutes=480))
        assert (high.peak_equity, low.peak_equity, low.equity) == (
            Decimal("5010"),
            Decimal("5010"),
            Decimal("4990"),
        )

    def test_bar_not_newer_than_the_mark_changes_nothing(self) -> None:
        """A bar closing at or before the current mark time returns an equal snapshot."""
        snap = _applied(_seed(), make_fill(_BUY, "0.005", "50000", minutes=300))
        assert snap.after_bar(make_bar(BTC_USDT, "40000", minutes=300)) == snap
        assert snap.after_bar(make_bar(BTC_USDT, "40000", minutes=240)) == snap

    def test_bar_for_a_pair_not_held_changes_nothing(self) -> None:
        """A bar for an unheld pair returns the same snapshot object."""
        snap = _applied(_seed(), make_fill(_BUY, "0.005", "50000"))
        assert snap.after_bar(make_bar(ETH_USDT, "3000", minutes=240)) is snap

    def test_marking_twice_with_the_same_bar_equals_marking_once(self) -> None:
        """Applying one bar twice is idempotent."""
        snap = _applied(_seed(), make_fill(_BUY, "0.005", "50000"))
        bar = make_bar(BTC_USDT, "52000", minutes=240)
        assert snap.after_bar(bar).after_bar(bar) == snap.after_bar(bar)


class TestRiskStateMapping:
    """to_risk_state hands the existing PortfolioState what it needs, and nothing invented."""

    def test_state_maps_peak_and_equity(self) -> None:
        """peak_value is the snapshot peak and current_value is its equity."""
        snap = _applied(_seed(), make_fill(_BUY, "0.005", "50000", fee="0.25"))
        snap = snap.after_bar(make_bar(BTC_USDT, "52000", minutes=240))
        snap = snap.after_bar(make_bar(BTC_USDT, "49000", minutes=480))
        state = snap.to_risk_state()
        assert (state.peak_value, state.current_value) == (
            Decimal("5009.75"),
            Decimal("4994.75"),
        )

    def test_state_has_no_slippage_correlations_or_halt_flag(self) -> None:
        """Slippage is 0, correlations are empty and no halt is requested."""
        state = _seed().to_risk_state()
        assert (state.realized_slippage_cumulative, state.correlations, state.dd_halt_required) == (
            Decimal("0"),
            {},
            False,
        )

    def test_seed_state_has_zero_drawdown(self) -> None:
        """A fresh seed reports equal peak and current value, so drawdown is 0."""
        state = _seed("5000").to_risk_state()
        assert (state.peak_value, state.current_value, state.drawdown) == (
            Decimal("5000"),
            Decimal("5000"),
            Decimal("0"),
        )

    def test_drawdown_after_a_pullback_is_the_fraction_below_peak(self) -> None:
        """Equity 4500 against peak 5000 is a 10 percent drawdown."""
        snap = _applied(_seed("5000"), make_fill(_BUY, "1", "1000"))
        snap = snap.after_bar(make_bar(BTC_USDT, "500", minutes=240))
        assert snap.to_risk_state().drawdown == Decimal("0.1")
