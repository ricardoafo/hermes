"""Edge tests for the circuit-breaker rules (#622).

A mutation-testing run over ``hermes.domain.risk`` showed the edges the rule
tests left unpinned: a zero or negative spread median, a correlation entry
that follows an unrelated one, a price a hair off the peg, the drawdown at a
zero peak, the full severity order, and the immutability of the state and
decision records. Each test below pins one of them.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from hermes.domain.risk.correlation_lock import CorrelationLock
from hermes.domain.risk.depeg_halt import DepegHalt
from hermes.domain.risk.depeg_warn import DepegWarn
from hermes.domain.risk.spread_spike import SpreadSpike
from hermes.domain.risk.state import PortfolioState
from hermes.domain.risk.types import RiskAction, RiskDecision
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair
from tests.unit.domain.risk.conftest import make_market_snapshot, make_portfolio_state

_BTC = Pair(base=Currency.BTC, quote=Currency.USDT)
_ETH = Pair(base=Currency.ETH, quote=Currency.USDT)
_SOL = Pair(base=Currency.SOL, quote=Currency.USDT)


class TestSpreadSpikeMedianEdge:
    """RO-02 halts conservatively whenever the 1H median is not positive."""

    # SEV-1: a missing spread median must halt, not let orders through (ADR-003 §02)
    def test_zero_median_halts(self) -> None:
        """A median of exactly 0 means no data: HALT for that reason, not as a spike."""
        decision = SpreadSpike().evaluate(
            make_portfolio_state(), make_market_snapshot(median_spread_1h=Decimal("0"))
        )
        assert (decision.action, "data absent" in decision.reason) == (RiskAction.HALT, True)

    # SEV-1: a missing spread median must halt, not let orders through (ADR-003 §02)
    def test_negative_median_halts(self) -> None:
        """A negative median is corrupt data: HALT for that reason, not as a spike."""
        decision = SpreadSpike().evaluate(
            make_portfolio_state(), make_market_snapshot(median_spread_1h=Decimal("-0.001"))
        )
        assert (decision.action, "data absent" in decision.reason) == (RiskAction.HALT, True)


class TestCorrelationLockScan:
    """RO-01 checks every pair of open positions, not just the first."""

    # SEV-1: a third correlated entry must be blocked wherever its cluster sits (pre-reg §5.5)
    def test_cluster_after_an_unclustered_pair_halts(self) -> None:
        """Three held pairs, the first combination uncorrelated: the later cluster still blocks."""
        other = Pair(base=Currency.BTC, quote=Currency.USDC)
        state = make_portfolio_state(
            held_pairs=frozenset({other, _ETH, _SOL}),
            correlations={
                (other, _ETH): 0.1,
                (other, _SOL): 0.1,
                (_ETH, _SOL): 0.95,
                (_BTC, _ETH): 0.9,
                (_BTC, _SOL): 0.9,
            },
        )
        decision = CorrelationLock().evaluate(state, make_market_snapshot(candidate_pair=_BTC))
        assert decision.action is RiskAction.HALT

    def test_equal_but_separate_candidate_is_matched(self) -> None:
        """The candidate is an equal but separate Pair object, as it is in production."""
        state = make_portfolio_state(
            held_pairs=frozenset({_ETH, _SOL}),
            correlations={(_ETH, _SOL): 0.9, (_BTC, _ETH): 0.9, (_BTC, _SOL): 0.9},
        )
        equal_copy = Pair(base=_BTC.base, quote=_BTC.quote)
        decision = CorrelationLock().evaluate(
            state, make_market_snapshot(candidate_pair=equal_copy)
        )
        assert decision.action is RiskAction.HALT


class TestDepegNearThePeg:
    """RO-05 measures distance from the peg, not the price itself."""

    def test_price_a_tenth_of_a_percent_off_is_ok(self) -> None:
        """0.999 is 0.1% off the peg, well under the 1.5% warning."""
        decision = DepegWarn().evaluate(
            make_portfolio_state(), make_market_snapshot(stable_price=Decimal("0.999"))
        )
        assert decision.action is RiskAction.OK

    def test_halt_ignores_a_price_a_tenth_of_a_percent_off(self) -> None:
        """RO-06: 0.999 is 0.1% off the peg, far from the hard halt."""
        decision = DepegHalt().evaluate(
            make_portfolio_state(), make_market_snapshot(stable_price=Decimal("0.999"))
        )
        assert decision.action is RiskAction.OK


class TestDrawdownAtZeroPeak:
    """A portfolio with no peak yet has no drawdown."""

    def test_zero_peak_has_zero_drawdown(self) -> None:
        """Peak 0 returns 0 instead of dividing by zero."""
        state = make_portfolio_state(peak_value=Decimal("0"), current_value=Decimal("0"))
        assert state.drawdown == Decimal("0")

    def test_halt_flag_defaults_to_off(self) -> None:
        """A state built without the flag does not ask for a drawdown halt."""
        state = PortfolioState(
            peak_value=Decimal("1"),
            current_value=Decimal("1"),
            realized_slippage_cumulative=Decimal("0"),
        )
        assert state.dd_halt_required is False


class TestSeverityOrder:
    """OK < WARN < HALT, strictly, in every pairing."""

    @pytest.mark.parametrize(
        ("worse", "better"),
        [
            (RiskAction.WARN, RiskAction.OK),
            (RiskAction.HALT, RiskAction.OK),
            (RiskAction.HALT, RiskAction.WARN),
        ],
    )
    def test_more_severe_is_worse_and_not_the_reverse(
        self, worse: RiskAction, better: RiskAction
    ) -> None:
        """The more severe action is worse; the less severe one is not."""
        assert (worse.is_worse_than(better), better.is_worse_than(worse)) == (True, False)


class TestImmutability:
    """Risk inputs and decisions cannot change after they are built."""

    def test_records_are_frozen_with_slots(self) -> None:
        """Portfolio state, market snapshot and decision reject edits and carry no dict."""
        records: list[tuple[object, str]] = [
            (make_portfolio_state(), "current_value"),
            (make_market_snapshot(), "current_spread"),
            (RiskDecision(action=RiskAction.OK, reason="ok"), "action"),
        ]
        for record, field_name in records:
            with pytest.raises(FrozenInstanceError):
                setattr(record, field_name, None)
        assert not any(hasattr(record, "__dict__") for record, _ in records)
