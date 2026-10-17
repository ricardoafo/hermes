"""Unit tests for Sharpe, moments, PSR and the Deflated Sharpe Ratio (pre-registration §8.1)."""

from __future__ import annotations

import math

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hermes.domain.statistics.sharpe import (
    deflated_sharpe_ratio,
    expected_max_sharpe,
    kurtosis,
    probabilistic_sharpe_ratio,
    sharpe_ratio,
    sharpe_t_statistic,
    skewness,
)

# Bailey and López de Prado (2014), worked example: T = 1250 daily returns,
# annualised Sharpe 2.5, skewness -3, kurtosis 10, N = 100 trials, variance of
# the trials' annualised Sharpe 0.5. Published: SR0 ≈ 0.1132 per day, DSR ≈ 0.9004.
_PAPER_SR = 2.5 / math.sqrt(250)
_PAPER_V = 0.5 / 250


class TestPublishedExample:
    """The DSR matches the paper the pre-registration cites."""

    # SEV-1: the DSR row gates real capital (ADR-014); the formula must be the published one
    def test_expected_max_sharpe_matches_paper(self) -> None:
        """SR0 for N = 100 and V = 0.5/250 is 0.1132 per day."""
        assert expected_max_sharpe(100, _PAPER_V) == pytest.approx(0.1132, abs=5e-5)

    # SEV-1: the DSR row gates real capital (ADR-014); the formula must be the published one
    def test_dsr_matches_paper(self) -> None:
        """PSR of the example's Sharpe against SR0 is 0.9004."""
        sr0 = expected_max_sharpe(100, _PAPER_V)
        dsr = probabilistic_sharpe_ratio(_PAPER_SR, sr0, 1250, -3.0, 10.0)
        assert dsr == pytest.approx(0.9004, abs=5e-4)


class TestExpectedMaxSharpe:
    """SR0 grows with the number of trials and is zero for one trial."""

    def test_one_trial_is_zero(self) -> None:
        """A single trial has nothing to deflate for."""
        assert expected_max_sharpe(1, 0.01) == 0.0

    @given(n=st.integers(min_value=2, max_value=10_000))
    def test_more_trials_never_lower_the_bar(self, n: int) -> None:
        """SR0 is non-decreasing in N: running more trials can only raise the bar."""
        assert expected_max_sharpe(n + 1, 0.01) >= expected_max_sharpe(n, 0.01)

    @pytest.mark.parametrize(("n", "v"), [(0, 0.01), (5, -0.1)])
    def test_invalid_inputs_raise(self, n: int, v: float) -> None:
        """Zero trials or a negative variance is a caller error."""
        with pytest.raises(ValueError, match="must be"):
            expected_max_sharpe(n, v)


class TestMoments:
    """Sharpe and moments on hand-checked series."""

    def test_sharpe_of_known_series(self) -> None:
        """[1, 2, 3, 4]: mean 2.5, sample std 1.29099, Sharpe 1.93649."""
        assert sharpe_ratio([1.0, 2.0, 3.0, 4.0]) == pytest.approx(1.936492, abs=1e-6)

    def test_symmetric_series_has_zero_skew(self) -> None:
        """A symmetric series has zero skewness."""
        assert skewness([-2.0, -1.0, 0.0, 1.0, 2.0]) == pytest.approx(0.0, abs=1e-12)

    def test_two_point_kurtosis_is_one(self) -> None:
        """A symmetric two-point distribution has kurtosis exactly 1 (non-excess)."""
        assert kurtosis([-1.0, 1.0, -1.0, 1.0]) == pytest.approx(1.0)

    @pytest.mark.parametrize("returns", [[], [0.01], [0.01, 0.01, 0.01]])
    def test_degenerate_series_are_not_computed(self, returns: list[float]) -> None:
        """Too few points or zero dispersion gives None for every statistic."""
        assert (sharpe_ratio(returns), skewness(returns), kurtosis(returns)) == (None, None, None)


class TestDeflatedSharpeRatio:
    """DSR on a return series."""

    _RETURNS = (0.004, -0.001, 0.003, 0.0, 0.002, -0.002, 0.005, 0.001) * 20

    def test_one_trial_equals_psr_against_zero(self) -> None:
        """With N = 1 the DSR is the PSR against a zero benchmark."""
        returns = list(self._RETURNS)
        sr = sharpe_ratio(returns)
        sk = skewness(returns)
        ku = kurtosis(returns)
        assert sr is not None
        assert sk is not None
        assert ku is not None
        expected = probabilistic_sharpe_ratio(sr, 0.0, len(returns), sk, ku)
        assert deflated_sharpe_ratio(returns, 1, 0.0) == expected

    @given(n=st.integers(min_value=1, max_value=1_000))
    def test_more_trials_never_raise_the_dsr(self, n: int) -> None:
        """Holding the returns fixed, more trials can only lower the DSR (§8.1)."""
        returns = list(self._RETURNS)
        low = deflated_sharpe_ratio(returns, n + 1, 1e-4)
        high = deflated_sharpe_ratio(returns, n, 1e-4)
        assert low is not None
        assert high is not None
        assert low <= high

    def test_degenerate_returns_are_not_computed(self) -> None:
        """Flat returns give no DSR, so the gate fails closed."""
        flat = deflated_sharpe_ratio([0.0] * 10, 5, 1e-4)
        two_point = deflated_sharpe_ratio([0.01, -0.01] * 5, 1, 0.0)
        assert (flat, two_point) == (None, pytest.approx(0.5))

    def test_psr_needs_two_observations(self) -> None:
        """T = 1 cannot form sqrt(T - 1)."""
        one = probabilistic_sharpe_ratio(0.1, 0.0, 1, 0.0, 3.0)
        two = probabilistic_sharpe_ratio(0.1, 0.0, 2, 0.0, 3.0)
        assert (one, two) == (None, pytest.approx(0.53973, abs=1e-5))


_RETURNS = st.lists(
    st.integers(min_value=-500, max_value=500).map(lambda k: k / 10_000), min_size=2, max_size=60
)


class TestSharpeTStatistic:
    """The t-statistic of the mean return is SR * sqrt(T) (ADR-021 §5; v2-backtest-funnel D-5)."""

    def test_two_returns(self) -> None:
        """[0.01, 0.03]: mean 0.02, s = 0.01 * sqrt(2), SR = sqrt(2), t = 2."""
        assert sharpe_t_statistic([0.01, 0.03]) == pytest.approx(2.0, abs=1e-12)

    def test_three_returns(self) -> None:
        """[0.01, 0.02, 0.03]: mean 0.02, s = 0.01, SR = 2, t = 2 * sqrt(3)."""
        assert sharpe_t_statistic([0.01, 0.02, 0.03]) == pytest.approx(3.4641016, abs=1e-7)

    def test_degenerate_series_are_not_computed(self) -> None:
        """No returns, one return, or no dispersion has no t-statistic; one more return has one."""
        cases = ([], [0.01], [0.02, 0.02], [0.01, 0.03])
        assert [sharpe_t_statistic(r) for r in cases] == [None, None, None, pytest.approx(2.0)]

    @given(_RETURNS)
    def test_doubling_every_return_changes_nothing(self, returns: list[float]) -> None:
        """Doubling every return is exact and leaves the scale-free t identical."""
        assert sharpe_t_statistic([2.0 * r for r in returns]) == sharpe_t_statistic(returns)

    @given(_RETURNS)
    def test_negating_the_returns_negates_t(self, returns: list[float]) -> None:
        """A strategy and its mirror have opposite t-statistics."""
        t = sharpe_t_statistic(returns)
        mirrored = sharpe_t_statistic([-r for r in returns])
        if t is None:
            assert mirrored is None
        else:
            assert mirrored == pytest.approx(-t, abs=1e-9)
