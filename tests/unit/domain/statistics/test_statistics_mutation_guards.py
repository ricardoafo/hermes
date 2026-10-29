"""Boundary and reference-value tests for the certification statistics (#505).

Each test pins an invariant that a mutation-testing run over
``hermes.domain.statistics`` showed the rest of the suite did not guard:
the smallest valid input, the exact edge of a threshold, a closed-form value,
or a keyword-only signature. The statistics decide the certification verdict
(pre-registration §8, §9; ADR-014), so a flipped comparison here would change
which strategy reaches real capital.
"""

import math
from collections.abc import Callable, Sequence
from statistics import NormalDist

import pytest

from hermes.domain.statistics.bootstrap import (
    SharpeInterval,
    block_bootstrap_sharpe_ci,
    percentile,
)
from hermes.domain.statistics.performance import (
    TradeResult,
    daily_returns,
    fees_to_gross_pnl,
    max_drawdown,
    profit_factor,
    recovery_days,
    win_rate,
)
from hermes.domain.statistics.rng import SplitMix64
from hermes.domain.statistics.sharpe import (
    kurtosis,
    probabilistic_sharpe_ratio,
    sharpe_ratio,
    skewness,
)
from hermes.domain.statistics.splits import Split, purged_cpcv_splits, walk_forward_splits

_SKEWED = [0.0, 0.0, 0.0, 1.0]
"""Closed form: skewness 2/sqrt(3), non-excess kurtosis 7/3."""


class TestMomentsAtTwoPoints:
    """Two returns are the fewest a sample moment can use, and they are enough."""

    def test_sharpe_of_two_returns(self) -> None:
        """[0.01, 0.03]: mean 0.02 over sample std 0.01*sqrt(2) gives sqrt(2)."""
        assert sharpe_ratio([0.01, 0.03]) == pytest.approx(math.sqrt(2.0))

    def test_skewness_of_two_returns(self) -> None:
        """Two points are symmetric about their mean, so skewness is 0, not undefined."""
        assert skewness([1.0, 3.0]) == (0.0)

    def test_kurtosis_of_two_returns(self) -> None:
        """Two points sit at one standard deviation each, so kurtosis is exactly 1."""
        assert kurtosis([1.0, 3.0]) == pytest.approx(1.0)


class TestMomentClosedForms:
    """Skewness and kurtosis match their closed form on a hand-checkable series."""

    def test_skewness_closed_form(self) -> None:
        """[0, 0, 0, 1] has population skewness 2/sqrt(3)."""
        assert skewness(_SKEWED) == pytest.approx(2.0 / math.sqrt(3.0))

    def test_kurtosis_closed_form(self) -> None:
        """[0, 0, 0, 1] has non-excess kurtosis 7/3."""
        assert kurtosis(_SKEWED) == pytest.approx(7.0 / 3.0)


class TestPsrVarianceTerm:
    """The PSR is undefined exactly when 1 - skew*SR + (kurt-1)/4*SR² is not positive."""

    # SEV-1: the PSR feeds the DSR row that gates real capital (ADR-014)
    def test_small_positive_variance_term_is_defined(self) -> None:
        """SR 1, skew 1, kurt 3 gives a variance term of 0.5 and PSR Phi(sqrt(2))."""
        psr = probabilistic_sharpe_ratio(1.0, 0.0, 2, 1.0, 3.0)
        assert psr == pytest.approx(NormalDist().cdf(math.sqrt(2.0)))

    # SEV-1: the PSR feeds the DSR row that gates real capital (ADR-014)
    def test_zero_variance_term_is_undefined(self) -> None:
        """SR 1, skew 1, kurt 1 makes the variance term exactly 0, so no PSR; skew 0 makes it 1."""
        undefined = probabilistic_sharpe_ratio(1.0, 0.0, 10, 1.0, 1.0)
        defined = probabilistic_sharpe_ratio(1.0, 0.0, 10, 0.0, 1.0)
        assert (undefined, defined) == (None, pytest.approx(NormalDist().cdf(3.0)))

    # SEV-1: the PSR feeds the DSR row that gates real capital (ADR-014)
    def test_negative_variance_term_is_undefined(self) -> None:
        """SR 1, skew 2, kurt 1 makes the variance term -1, so no PSR; skew 0 makes it 1."""
        undefined = probabilistic_sharpe_ratio(1.0, 0.0, 10, 2.0, 1.0)
        defined = probabilistic_sharpe_ratio(1.0, 0.0, 10, 0.0, 1.0)
        assert (undefined, defined) == (None, pytest.approx(NormalDist().cdf(3.0)))


def _linear_quantile(ordered: Sequence[float], q: float) -> float:
    position = q * (len(ordered) - 1)
    low, high = math.floor(position), math.ceil(position)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def _reference_interval(
    returns: Sequence[float], *, seed: int, resamples: int, block_length: int, confidence: float
) -> tuple[float, float, int]:
    rng = SplitMix64(seed)
    n = len(returns)
    sharpes: list[float] = []
    for _ in range(resamples):
        sample: list[float] = []
        for _ in range(math.ceil(n / block_length)):
            start = rng.below(n)
            sample += [returns[(start + i) % n] for i in range(block_length)]
        value = sharpe_ratio(sample[:n])
        if value is not None:
            sharpes.append(value)
    sharpes.sort()
    tail = (1.0 - confidence) / 2.0
    return _linear_quantile(sharpes, tail), _linear_quantile(sharpes, 1.0 - tail), len(sharpes)


_WAVY = [0.01, -0.004, 0.006, -0.002, 0.003] * 10
_UNEVEN = [0.01, -0.004, 0.006, -0.002, 0.003, 0.012, -0.007] * 10
"""Period 7, so blocks of 10 are not rotations of one another and resamples differ."""


class TestSplitMix64Reference:
    """The generator reproduces the published SplitMix64 test vector."""

    # SEV-1: a seed must reproduce the certification bootstrap exactly (ADR-014)
    def test_first_five_outputs_for_seed_1234567(self) -> None:
        """Seed 1234567 gives the reference implementation's first five outputs."""
        rng = SplitMix64(1234567)
        assert [rng.next64() for _ in range(5)] == [
            6457827717110365317,
            3203168211198807973,
            9817491932198370423,
            4593380528125082431,
            16408922859458223821,
        ]

    # SEV-1: uniform block starts underpin the certification bootstrap (ADR-014)
    def test_below_rejects_outputs_in_the_tail(self) -> None:
        """For n = 2^63 + 1 every output of n or more is rejected, so outputs 3 and 5 are skipped.

        Of the seed's first five outputs, the 1st, 2nd and 4th are below n and
        are returned as they are; the 3rd and 5th are at least n and must be drawn again.
        """
        rng = SplitMix64(1234567)
        assert [rng.below(2**63 + 1) for _ in range(3)] == [
            6457827717110365317,
            3203168211198807973,
            4593380528125082431,
        ]

    # SEV-1: uniform block starts underpin the certification bootstrap (ADR-014)
    def test_below_keeps_every_output_under_the_limit(self) -> None:
        """For n = 3 * 2^62 the limit is n itself, so the first three outputs all come back.

        The third output, 9817491932198370423, is above 2^63; a limit built from
        the wrong power of two would reject it.
        """
        rng = SplitMix64(1234567)
        assert [rng.below(3 * 2**62) for _ in range(3)] == [
            6457827717110365317,
            3203168211198807973,
            9817491932198370423,
        ]

    def test_negative_seed_wraps_to_64_bits(self) -> None:
        """Seed -1 is the 64-bit pattern of all ones, as in the reference."""
        assert SplitMix64(-1).next64() == SplitMix64(2**64 - 1).next64()


class TestBlockBootstrapReference:
    """The bootstrap equals a plain re-statement of §8.2's circular block bootstrap."""

    # SEV-1: the bootstrap lower bound is the H1 row that gates real capital (ADR-014)
    def test_interval_matches_reference_when_blocks_do_not_divide_the_series(self) -> None:
        """50 returns in blocks of 7: eight wrapped blocks per resample, cut back to 50."""
        got = block_bootstrap_sharpe_ci(
            _WAVY, seed=42, resamples=200, block_length=7, confidence=0.9
        )
        want = _reference_interval(_WAVY, seed=42, resamples=200, block_length=7, confidence=0.9)
        assert got == SharpeInterval(lower=want[0], upper=want[1], resamples=want[2])

    # SEV-1: the bootstrap lower bound is the H1 row that gates real capital (ADR-014)
    def test_interval_matches_reference_when_blocks_divide_the_series(self) -> None:
        """70 returns in blocks of 10: exactly seven blocks per resample, at a 40% level."""
        got = block_bootstrap_sharpe_ci(
            _UNEVEN, seed=9, resamples=200, block_length=10, confidence=0.4
        )
        want = _reference_interval(_UNEVEN, seed=9, resamples=200, block_length=10, confidence=0.4)
        assert got == SharpeInterval(lower=want[0], upper=want[1], resamples=want[2])
        assert got.lower < got.upper

    def test_default_resample_count_is_ten_thousand(self) -> None:
        """§8.2 fixes 10,000 resamples; a drifting series keeps every one defined."""
        interval = block_bootstrap_sharpe_ci(_WAVY * 3, seed=7)
        assert interval is not None
        assert interval.resamples == 10_000

    def test_one_resample_of_one_period_blocks_is_valid(self) -> None:
        """One resample of one-period blocks is the smallest valid call."""
        interval = block_bootstrap_sharpe_ci(_WAVY, seed=3, resamples=1, block_length=1)
        assert interval is not None
        assert interval.resamples == 1

    def test_confidence_above_one_is_rejected(self) -> None:
        """A confidence of 1.5 is outside (0, 1)."""
        with pytest.raises(ValueError, match="confidence"):
            block_bootstrap_sharpe_ci(_WAVY, seed=1, confidence=1.5)

    def test_negative_confidence_is_rejected(self) -> None:
        """A confidence of -0.5 is outside (0, 1)."""
        with pytest.raises(ValueError, match="confidence"):
            block_bootstrap_sharpe_ci(_WAVY, seed=1, confidence=-0.5)

    def test_options_are_keyword_only(self) -> None:
        """A positional seed is refused, so seed and resamples cannot be swapped."""
        call: Callable[..., object] = block_bootstrap_sharpe_ci
        with pytest.raises(TypeError):
            call(_WAVY, 1, 100)

    def test_interval_is_hashable_by_value(self) -> None:
        """Equal intervals are interchangeable dict keys, so a computed one is a value."""
        interval = SharpeInterval(lower=0.0, upper=1.0, resamples=1)
        same = SharpeInterval(lower=0.0, upper=1.0, resamples=1)
        assert {interval: "kept"}[same] == "kept"


class TestPercentile:
    """Linear interpolation between order statistics."""

    @pytest.mark.parametrize(
        ("q", "expected"),
        [(0.0, 0.0), (0.25, 7.5), (0.5, 15.0), (0.9, 27.0), (1.0, 30.0)],
    )
    def test_interpolates_between_neighbours(self, q: float, expected: float) -> None:
        """On [0, 10, 20, 30] the q-quantile sits at position 3q, read linearly."""
        assert percentile([0.0, 10.0, 20.0, 30.0], q) == pytest.approx(expected)


class TestEquityBoundaries:
    """Equity validation and drawdowns at their edges."""

    def test_negative_equity_is_rejected(self) -> None:
        """A negative equity value is not positive, so returns are undefined."""
        with pytest.raises(ValueError, match="positive"):
            daily_returns([-1.0, 2.0])

    def test_equity_below_one_is_valid(self) -> None:
        """0.5 to 1.0 is a 100% return; only non-positive values are refused."""
        assert daily_returns([0.5, 1.0]) == [1.0]

    def test_drawdown_deeper_than_half(self) -> None:
        """100 to 40 is a 60% drawdown."""
        assert max_drawdown([100.0, 40.0]) == pytest.approx(0.6)


class TestRecoveryDays:
    """§9 time-to-recovery: from the peak back to it, for drawdowns of 8% or more."""

    def test_rising_curve_has_no_recovery(self) -> None:
        """A curve that only rises never draws down, so recovery is 0."""
        assert recovery_days([100.0, 110.0]) == (0.0)

    def test_single_point_has_no_recovery(self) -> None:
        """One point has no drawdown, so recovery is 0."""
        assert recovery_days([100.0]) == (0.0)

    def test_half_drawdown_recovered_in_two_days(self) -> None:
        """100, 50, 100: a 50% fall recovered two days after the peak."""
        assert recovery_days([100.0, 50.0, 100.0]) == (2.0)

    def test_small_drawdown_after_a_recovery_is_ignored(self) -> None:
        """A 1% dip after a recovered 10% fall leaves the recovery at 2 days."""
        assert recovery_days([100.0, 90.0, 100.0, 99.0]) == (2.0)

    def test_unrecovered_drawdown_of_exactly_eight_percent_is_infinite(self) -> None:
        """Ending at exactly -8% counts as an open §9 drawdown (D-4)."""
        assert recovery_days([100.0, 92.0]) == math.inf


def _trade(net: float) -> TradeResult:
    return TradeResult(gross_pnl=net, fees=0.0)


class TestTradeRatios:
    """Profit factor, fees to gross and win rate at their zero edges (D-6)."""

    def test_profit_factor_counts_sub_unit_trades(self) -> None:
        """A 0.5 win against a 0.5 loss is a profit factor of 1."""
        assert profit_factor([_trade(0.5), _trade(-0.5)]) == pytest.approx(1.0)

    def test_profit_factor_of_a_single_small_win_is_infinite(self) -> None:
        """A 0.5 win and no loss is an infinite profit factor."""
        assert profit_factor([_trade(0.5)]) == math.inf

    def test_profit_factor_of_flat_trades_is_undefined(self) -> None:
        """Trades that all net zero have neither wins nor losses; one win makes it infinite."""
        flat = profit_factor([_trade(0.0), _trade(0.0)])
        one_win = profit_factor([_trade(0.0), _trade(1.0)])
        assert (flat, one_win) == (None, math.inf)

    def test_fees_over_zero_gross_is_infinite(self) -> None:
        """Gross PnL of exactly 0 fails the §9 row with an infinite ratio."""
        trades = [TradeResult(gross_pnl=1.0, fees=0.1), TradeResult(gross_pnl=-1.0, fees=0.1)]
        assert fees_to_gross_pnl(trades) == math.inf

    def test_fees_over_sub_unit_gross(self) -> None:
        """0.1 of fees over 0.5 of gross is 0.2."""
        assert fees_to_gross_pnl([TradeResult(gross_pnl=0.5, fees=0.1)]) == pytest.approx(0.2)

    def test_win_rate_counts_only_positive_net(self) -> None:
        """Of three +0.5 trades, one flat and one -1, the three +0.5 are wins: 3 of 5."""
        trades = [_trade(0.5), _trade(0.5), _trade(0.5), _trade(0.0), _trade(-1.0)]
        assert win_rate(trades) == pytest.approx(0.6)

    def test_trade_result_is_hashable_by_value(self) -> None:
        """Equal closed trades are interchangeable dict keys, so a trade is a value."""
        assert {_trade(1.0): "kept"}[_trade(1.0)] == "kept"


class TestWalkForwardBoundaries:
    """§8.3 walk-forward windows at their edges."""

    def test_exact_fit_gives_one_split(self) -> None:
        """365 warm-up days plus one 91-day test window fit exactly in 456 days."""
        splits = walk_forward_splits(456)
        assert [(s.test[0], s.test[-1]) for s in splits] == [(365, 455)]

    def test_one_month_arguments_are_valid(self) -> None:
        """One month (30 days) is the smallest window; 91 days fit two tests after the warm-up."""
        splits = walk_forward_splits(91, min_train_months=1, step_months=1, test_months=1)
        assert [(s.test[0], len(s.test)) for s in splits] == [(30, 30), (60, 30)]

    def test_options_are_keyword_only(self) -> None:
        """Positional month arguments are refused, so they cannot be swapped."""
        call: Callable[..., object] = walk_forward_splits
        with pytest.raises(TypeError):
            call(1096, 12)

    def test_split_is_hashable_by_value(self) -> None:
        """Equal splits are interchangeable dict keys, so a split is a value."""
        assert {Split(train=(1,), test=(2,)): "kept"}[Split(train=(1,), test=(2,))] == "kept"


class TestPurgedCpcvBoundaries:
    """§8.3 purged CPCV: argument edges and exact purge and embargo."""

    def test_default_is_five_groups_two_held_out(self) -> None:
        """C(5, 2) = 10 splits with the §8.3 defaults, in the documented order.

        100 days in 5 groups of 20: the first split holds out days 0-39 and trains from
        day 43 (3 embargo days after the test block); the last holds out days 60-99.
        """
        splits = purged_cpcv_splits(100)
        assert len(splits) == 10
        assert splits == purged_cpcv_splits(
            100, groups=5, test_groups=2, purge_days=5, embargo_days=3
        )
        assert splits[0] == Split(train=tuple(range(43, 100)), test=tuple(range(40)))
        assert splits[-1].test == tuple(range(60, 100))

    def test_more_test_groups_than_groups_is_rejected(self) -> None:
        """Holding out 3 of 2 groups is impossible."""
        with pytest.raises(ValueError, match="test_groups"):
            purged_cpcv_splits(100, groups=2, test_groups=3)

    def test_negative_purge_is_rejected(self) -> None:
        """A negative purge is not a gap."""
        with pytest.raises(ValueError, match="non-negative"):
            purged_cpcv_splits(100, purge_days=-1)

    def test_negative_embargo_is_rejected(self) -> None:
        """A negative embargo is not a gap."""
        with pytest.raises(ValueError, match="non-negative"):
            purged_cpcv_splits(100, embargo_days=-1)

    def test_zero_gaps_are_valid(self) -> None:
        """No purge and no embargo: train is exactly the complement of test."""
        split = purged_cpcv_splits(10, purge_days=0, embargo_days=0)[0]
        assert split.train == (4, 5, 6, 7, 8, 9)

    def test_as_many_days_as_groups_is_valid(self) -> None:
        """Five days in five groups is one day per group."""
        split = purged_cpcv_splits(5, purge_days=0, embargo_days=0)[0]
        assert split.test == (0, 1)

    def test_fewer_days_than_groups_is_rejected(self) -> None:
        """Three days cannot fill five groups."""
        with pytest.raises(ValueError, match="at least 5 days"):
            purged_cpcv_splits(3)

    def test_first_group_purge_and_embargo(self) -> None:
        """10 days, groups (0, 1) held out, one day of embargo: train starts at day 5."""
        split = purged_cpcv_splits(10, purge_days=1, embargo_days=1)[0]
        assert (split.test, split.train) == ((0, 1, 2, 3), (5, 6, 7, 8, 9))

    def test_options_are_keyword_only(self) -> None:
        """A positional group count is refused, so it cannot be swapped with test_groups."""
        call: Callable[..., object] = purged_cpcv_splits
        with pytest.raises(TypeError):
            call(100, 5)
