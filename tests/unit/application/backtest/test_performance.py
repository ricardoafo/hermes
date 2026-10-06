"""Unit tests for the certify performance file of a backtest run (#822).

Invariants (822-portfolio-backtest change.md D-4):

- A round trip's gross PnL is sale proceeds minus the cost of what was sold,
  and its fees are every fee paid on the entry and the exit.
- A position still open at the end is not a trade.
- Round trips are kept per pair, so interleaved pairs do not mix.
- The file carries exactly the keys ``hermes certify`` reads, and one trial
  with no Sharpe variance deflates by nothing (variance 0); two or more
  without a variance are refused.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hermes.application.backtest.performance import performance_file, round_trips
from hermes.application.backtest.replay import BacktestRun
from hermes.domain.calibration.trial_record import TrialSummary
from hermes.domain.statistics.performance import TradeResult
from hermes.domain.trading.fill import Fill
from hermes.domain.trading.order import Order, OrderSide
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.quantity import Quantity
from tests.support.fill_kernel import BTC_USDT, STRATEGY

_ETH = Pair(base=Currency.ETH, quote=Currency.USDT)
_T0 = datetime(2024, 1, 1, tzinfo=UTC)


def _fill(side: OrderSide, qty: str, price: str, fee: str, pair: Pair = BTC_USDT) -> Fill:
    order = Order(
        side=side,
        pair=pair,
        qty=Quantity(amount=Decimal(qty), asset=pair.base),
        strategy_id=STRATEGY,
    )
    return Fill(
        order=order,
        executed_qty=Decimal(qty),
        avg_price=Decimal(price),
        fee=Money(Decimal(fee), Currency.USDT),
        timestamp=_T0,
    )


class TestRoundTrips:
    """Fills become closed trades with gross PnL and fees."""

    def test_one_round_trip(self) -> None:
        """Buy 2 at 100 (fee 0.2), sell 2 at 110 (fee 0.22): gross 20, fees 0.42."""
        trades = round_trips(
            [_fill(OrderSide.BUY, "2", "100", "0.2"), _fill(OrderSide.SELL, "2", "110", "0.22")]
        )
        assert trades == [TradeResult(gross_pnl=20.0, fees=pytest.approx(0.42))]

    def test_open_position_is_not_a_trade(self) -> None:
        """An entry with no exit yet adds no trade."""
        assert round_trips([_fill(OrderSide.BUY, "1", "100", "0.1")]) == []

    def test_partial_exits_close_one_trade(self) -> None:
        """Selling 1 then 1 of 2 bought at 100 closes one trade when flat."""
        trades = round_trips(
            [
                _fill(OrderSide.BUY, "2", "100", "0.2"),
                _fill(OrderSide.SELL, "1", "90", "0.09"),
                _fill(OrderSide.SELL, "1", "120", "0.12"),
            ]
        )
        assert trades == [TradeResult(gross_pnl=10.0, fees=pytest.approx(0.41))]

    def test_pairs_do_not_mix(self) -> None:
        """BTC and ETH trades interleave; each keeps its own cost."""
        trades = round_trips(
            [
                _fill(OrderSide.BUY, "1", "100", "0", BTC_USDT),
                _fill(OrderSide.BUY, "1", "10", "0", _ETH),
                _fill(OrderSide.SELL, "1", "9", "0", _ETH),
                _fill(OrderSide.SELL, "1", "130", "0", BTC_USDT),
            ]
        )
        assert [t.gross_pnl for t in trades] == [-1.0, 30.0]

    def test_overselling_is_refused(self) -> None:
        """A sell larger than the holding is a broken fill stream, not a short."""
        with pytest.raises(ValueError, match="exceeds"):
            round_trips(
                [_fill(OrderSide.BUY, "1", "100", "0"), _fill(OrderSide.SELL, "2", "100", "0")]
            )

    @given(
        buy=st.decimals(min_value=1, max_value=1000, places=2),
        sell=st.decimals(min_value=1, max_value=1000, places=2),
        qty=st.decimals(min_value=Decimal("0.01"), max_value=10, places=2),
    )
    def test_gross_pnl_is_price_change_times_quantity(
        self, buy: Decimal, sell: Decimal, qty: Decimal
    ) -> None:
        """For any one-lot round trip, gross PnL = (sell - buy) * qty."""
        (trade,) = round_trips(
            [
                _fill(OrderSide.BUY, str(qty), str(buy), "0"),
                _fill(OrderSide.SELL, str(qty), str(sell), "0"),
            ]
        )
        assert trade.gross_pnl == pytest.approx(float((sell - buy) * qty))


class TestPerformanceFile:
    """The file holds exactly what certify reads."""

    @staticmethod
    def _run() -> BacktestRun:
        run = BacktestRun()
        run.daily_equity = [(_T0 + timedelta(days=i), Decimal(5000 + i)) for i in range(3)]
        run.fills = [
            _fill(OrderSide.BUY, "1", "100", "0.1"),
            _fill(OrderSide.SELL, "1", "105", "0.1"),
        ]
        return run

    def test_file_shape(self) -> None:
        """Window train_val, equity floats, one trade, the trial stats and the seed."""
        payload = performance_file(
            self._run(),
            trials=TrialSummary(count=4, validation_count=1, sharpe_variance=0.01),
            bootstrap_seed=7,
        )
        assert payload == {
            "window": "train_val",
            "daily_equity": [5000.0, 5001.0, 5002.0],
            "trades": [{"gross_pnl": 5.0, "fees": pytest.approx(0.2)}],
            "trials": {"count": 4, "sharpe_variance": 0.01},
            "bootstrap_seed": 7,
        }

    def test_single_trial_deflates_by_nothing(self) -> None:
        """One trial has no Sharpe variance; it is written as 0, which deflates by nothing."""
        payload = performance_file(
            self._run(),
            trials=TrialSummary(count=1, validation_count=1, sharpe_variance=None),
            bootstrap_seed=0,
        )
        assert payload["trials"] == {"count": 1, "sharpe_variance": 0.0}

    def test_many_trials_without_variance_are_refused(self) -> None:
        """Several trials but no variance would understate the deflation; refuse."""
        with pytest.raises(ValueError, match="variance"):
            performance_file(
                self._run(),
                trials=TrialSummary(count=3, validation_count=1, sharpe_variance=None),
                bootstrap_seed=0,
            )
