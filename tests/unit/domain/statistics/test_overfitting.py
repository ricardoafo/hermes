"""Unit tests for CSCV and the probability of backtest overfitting (ADR-021 §5).

Hand-computed cases use two-row blocks ``[m + d, m - d]``, whose per-period
Sharpe is ``m / (d * sqrt(2))``, so the in-sample winner and its
out-of-sample rank can be read off the means.
"""

import math

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from hermes.domain.statistics.overfitting import probability_of_backtest_overfitting


def _two_row_blocks(means: list[float], d: float = 0.01) -> list[float]:
    """Build returns whose block ``k`` is ``[means[k] + d, means[k] - d]``."""
    out: list[float] = []
    for m in means:
        out.extend((m + d, m - d))
    return out


class TestHandComputed:
    """PBO matches cases worked out by hand from the CSCV definition."""

    # SEV-1: PBO gates promotion to shadow for every v2 strategy (ADR-021 §5)
    def test_winner_that_loses_out_of_sample_in_every_half_is_pbo_one(self) -> None:
        """A wins block 0 and loses block 1, B the reverse: both halves overfit, PBO = 1."""
        a = _two_row_blocks([0.01, -0.01])
        b = [0.01, -0.01, 0.01, -0.01]
        assert probability_of_backtest_overfitting([a, b], blocks=2) == 1.0

    # SEV-1: PBO gates promotion to shadow for every v2 strategy (ADR-021 §5)
    def test_dominant_trial_is_pbo_zero(self) -> None:
        """A beats B in every block, so the in-sample winner is also best out of sample."""
        a = [0.03, 0.01, 0.02, 0.0]
        b = [0.01, -0.01, 0.0, -0.02]
        assert probability_of_backtest_overfitting([a, b], blocks=2) == 0.0

    def test_one_overfit_half_of_two_is_pbo_half(self) -> None:
        """Half {0} picks A (worst in block 1); half {1} picks B, ranked 3 of 4 in block 0."""
        trials = [
            _two_row_blocks([0.04, -0.04]),
            _two_row_blocks([0.03, 0.03]),
            _two_row_blocks([0.02, 0.02]),
            _two_row_blocks([0.01, 0.01]),
        ]
        assert probability_of_backtest_overfitting(trials, blocks=2) == 0.5

    def test_median_rank_counts_as_overfit(self) -> None:
        """Identical trials tie at rank 1.5 of 2: omega = 0.5, lambda = 0, counted (D-4)."""
        a = [0.02, 0.0, 0.01, -0.01]
        assert probability_of_backtest_overfitting([a, list(a)], blocks=2) == 1.0

    def test_flat_block_never_wins_in_sample(self) -> None:
        """A flat block has no Sharpe and ranks below B's negative one, so B wins half {1}.

        Read as Sharpe 0, A would win half {1} and rank first in block 0, giving 0.5.
        """
        a = [0.03, 0.01, 0.0, 0.0]
        b = [0.02, 0.0, 0.0, -0.02]
        assert probability_of_backtest_overfitting([a, b], blocks=2) == 1.0

    def test_constant_non_zero_block_is_flat(self) -> None:
        """A block of equal non-zero returns is flat even when rounding leaves a tiny variance."""
        a = [0.03, 0.01, 0.01, 0.01]
        b = [0.02, 0.0, 0.0, -0.02]
        assert probability_of_backtest_overfitting([a, b], blocks=2) == 1.0

    def test_uneven_rows_split_into_near_equal_blocks(self) -> None:
        """T = 5, S = 2 gives blocks of 2 and 3 rows; A dominates, so PBO = 0."""
        a = [0.03, 0.01, 0.02, 0.0, 0.01]
        b = [r - 0.02 for r in a]
        assert probability_of_backtest_overfitting([a, b], blocks=2) == 0.0


class TestInvalidInput:
    """Inputs CSCV cannot use are caller errors, never a number."""

    @pytest.mark.parametrize("blocks", [0, 1, 3])
    def test_blocks_must_be_even_and_at_least_two(self, blocks: int) -> None:
        """S must split into two equal halves of blocks."""
        with pytest.raises(ValueError, match="blocks must be an even number >= 2"):
            probability_of_backtest_overfitting([[0.1, 0.2] * 4, [0.2, 0.1] * 4], blocks=blocks)

    def test_needs_two_trials(self) -> None:
        """With one trial there is nothing to rank against."""
        with pytest.raises(ValueError, match="at least 2 trials"):
            probability_of_backtest_overfitting([[0.1, 0.2, 0.3, 0.4]], blocks=2)

    def test_trials_must_share_length(self) -> None:
        """Every trial covers the same periods."""
        with pytest.raises(ValueError, match="same number of returns"):
            probability_of_backtest_overfitting([[0.1] * 4, [0.1] * 5], blocks=2)

    def test_needs_two_rows_per_block(self) -> None:
        """A one-row block has no sample dispersion."""
        with pytest.raises(ValueError, match="at least 4 returns"):
            probability_of_backtest_overfitting([[0.1, 0.2, 0.3], [0.3, 0.2, 0.1]], blocks=2)


_UNITS = st.integers(min_value=-50, max_value=50)


@st.composite
def _trial_matrices(draw: st.DrawFn) -> tuple[list[list[float]], int]:
    blocks = draw(st.sampled_from([2, 4, 6]))
    rows = draw(st.integers(min_value=2 * blocks, max_value=4 * blocks))
    n = draw(st.integers(min_value=2, max_value=5))
    trials = [[draw(_UNITS) / 1000 for _ in range(rows)] for _ in range(n)]
    return trials, blocks


class TestProperties:
    """Properties that hold for every trial matrix."""

    @given(_trial_matrices())
    def test_pbo_is_a_share_of_the_halves(self, case: tuple[list[list[float]], int]) -> None:
        """PBO is k / C(S, S/2) for an integer k in [0, C(S, S/2)]."""
        trials, blocks = case
        halves = math.comb(blocks, blocks // 2)
        pbo = probability_of_backtest_overfitting(trials, blocks=blocks)
        assert 0.0 <= pbo <= 1.0
        assert pbo * halves == pytest.approx(round(pbo * halves), abs=1e-9)

    @given(_trial_matrices())
    def test_doubling_every_return_changes_nothing(
        self, case: tuple[list[list[float]], int]
    ) -> None:
        """Sharpe is scale-free and doubling is exact in binary floating point."""
        trials, blocks = case
        doubled = [[2.0 * r for r in trial] for trial in trials]
        assert probability_of_backtest_overfitting(
            doubled, blocks=blocks
        ) == probability_of_backtest_overfitting(trials, blocks=blocks)

    # SEV-1: a family with a true ranking must not look overfit (ADR-021 §5)
    @given(
        noise=st.lists(_UNITS, min_size=8, max_size=24),
        n=st.integers(min_value=2, max_value=6),
    )
    def test_shifted_copies_of_one_series_are_never_overfit(self, noise: list[int], n: int) -> None:
        """Trials e + mu_i share every block's dispersion, so the IS winner is the OOS winner."""
        blocks = 4
        rows = len(noise) - len(noise) % blocks
        base = [k / 1000 for k in noise[:rows]]
        size = rows // blocks
        assume(all(len(set(noise[g * size : (g + 1) * size])) > 1 for g in range(blocks)))
        trials = [[r + i / 1000 for r in base] for i in range(n)]
        assert probability_of_backtest_overfitting(trials, blocks=blocks) == 0.0
