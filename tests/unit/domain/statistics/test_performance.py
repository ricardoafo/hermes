"""Unit tests for the §9 secondary metrics (pre-registration §9)."""

from __future__ import annotations

import math

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hermes.domain.statistics.performance import (
    TradeResult,
    daily_returns,
    fees_to_gross_pnl,
    max_drawdown,
    profit_factor,
    recovery_days,
    ulcer_index,
    win_rate,
)

_CURVE = [100.0, 110.0, 99.0, 104.5, 110.0, 121.0]
"""Peak 110, trough 99 (10% drawdown), back at 110 three days after the peak."""


class TestDrawdown:
    """Max drawdown and ulcer index on a hand-computed curve."""

    def test_max_drawdown(self) -> None:
        """110 → 99 is a 10% drawdown."""
        assert max_drawdown(_CURVE) == pytest.approx(0.10)

    def test_ulcer_index(self) -> None:
        """Drawdowns 0, 0, 10, 5, 0, 0 points: RMS = √(125/6)."""
        assert ulcer_index(_CURVE) == pytest.approx(math.sqrt(125 / 6))

    @given(st.lists(st.floats(min_value=1.0, max_value=1e6), min_size=1, max_size=50))
    def test_drawdown_is_a_fraction(self, curve: list[float]) -> None:
        """Any positive curve has a max drawdown in [0, 1)."""
        value = max_drawdown(curve)
        assert value is not None
        assert 0.0 <= value < 1.0

    def test_empty_curve_is_not_computed(self) -> None:
        """No equity points, no drawdown statistics."""
        assert (max_drawdown([]), ulcer_index([]), recovery_days([])) == (None, None, None)


class TestRecovery:
    """Time to recover from drawdowns of 8% or more (D-4)."""

    def test_recovered_drawdown_counts_peak_to_recovery(self) -> None:
        """Peak at day 1, back at the peak on day 4: three days."""
        assert recovery_days(_CURVE) == 3.0

    def test_shallow_drawdowns_are_ignored(self) -> None:
        """A 5% dip does not count toward recovery time."""
        assert recovery_days([100.0, 95.0, 96.0, 97.0, 101.0]) == 0.0

    # SEV-1: an open deep drawdown must fail the §9 recovery row, not pass it
    def test_unrecovered_deep_drawdown_is_infinite(self) -> None:
        """A 10% drawdown still open at the end fails the row (D-4)."""
        assert recovery_days([100.0, 90.0, 95.0]) == math.inf

    def test_exactly_eight_percent_counts(self) -> None:
        """The §9 rule says 8% or more, so exactly 8% counts."""
        assert recovery_days([100.0, 92.0, 100.0]) == 2.0


class TestTrades:
    """Profit factor, fee ratio and win rate from closed trades."""

    _TRADES = (
        TradeResult(gross_pnl=50.0, fees=2.0),
        TradeResult(gross_pnl=30.0, fees=2.0),
        TradeResult(gross_pnl=-20.0, fees=2.0),
        TradeResult(gross_pnl=1.0, fees=2.0),
    )
    """Net: 48, 28, -22, -1."""

    def test_profit_factor_uses_net_pnl(self) -> None:
        """(48 + 28) / (22 + 1)."""
        assert profit_factor(self._TRADES) == pytest.approx(76 / 23)

    def test_fees_to_gross_pnl(self) -> None:
        """8 in fees over 61 gross."""
        assert fees_to_gross_pnl(self._TRADES) == pytest.approx(8 / 61)

    def test_win_rate_counts_net_winners(self) -> None:
        """The +1 gross trade loses after fees, so 2 of 4 win."""
        assert win_rate(self._TRADES) == 0.5

    def test_no_losers_is_infinite_profit_factor(self) -> None:
        """All winners: profit factor is infinite and passes (D-6)."""
        assert profit_factor([TradeResult(10.0, 1.0)]) == math.inf

    # SEV-1: a losing gross PnL must fail the fee row, not divide into a small ratio
    def test_non_positive_gross_pnl_fails_the_fee_row(self) -> None:
        """Gross PnL at or below zero gives an infinite fee ratio (D-6)."""
        assert fees_to_gross_pnl([TradeResult(-5.0, 1.0)]) == math.inf

    def test_no_trades_are_not_computed(self) -> None:
        """No trades, no trade statistics."""
        assert (profit_factor([]), fees_to_gross_pnl([]), win_rate([])) == (None, None, None)


class TestDailyReturns:
    """Returns from an equity curve."""

    def test_simple_returns(self) -> None:
        """100 → 110 → 99 gives +10% then -10%."""
        assert daily_returns([100.0, 110.0, 99.0]) == pytest.approx([0.10, -0.10])

    def test_non_positive_equity_is_rejected(self) -> None:
        """A zero equity point cannot form a return."""
        with pytest.raises(ValueError, match="positive"):
            daily_returns([100.0, 0.0])
