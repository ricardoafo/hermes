"""Unit tests for the circular block bootstrap CI on Sharpe (pre-registration §8.2)."""

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from hermes.domain.statistics.bootstrap import _SplitMix64, block_bootstrap_sharpe_ci

_DRIFT = [0.003, 0.001] * 60
_NOISE = [0.01, -0.01, 0.002, -0.002] * 30


def test_generator_matches_the_splitmix64_reference() -> None:
    """Seed 0 gives the published first SplitMix64 output, pinning reproducibility."""
    assert _SplitMix64(0).next64() == 0xE220A8397B1DCDAF


def test_same_seed_same_interval() -> None:
    """A seed reproduces the interval exactly (auditability of §8.2 results)."""
    first = block_bootstrap_sharpe_ci(_NOISE, seed=7, resamples=300)
    second = block_bootstrap_sharpe_ci(_NOISE, seed=7, resamples=300)
    assert first is not None
    assert first.resamples == 300
    assert first == second


def test_steady_gains_have_a_positive_lower_bound() -> None:
    """Returns that are always positive give a CI entirely above zero."""
    interval = block_bootstrap_sharpe_ci(_DRIFT, seed=1, resamples=300)
    assert interval is not None
    assert interval.resamples == 300
    assert interval.lower > 0.0


def test_zero_mean_noise_straddles_zero() -> None:
    """Mean-zero returns give a lower bound at or below zero, so H1 is not accepted."""
    interval = block_bootstrap_sharpe_ci(_NOISE, seed=3, resamples=300)
    assert interval is not None
    assert interval.lower <= 0.0 <= interval.upper


def test_series_shorter_than_a_block_is_not_computed() -> None:
    """29 returns give no interval (the gate fails closed); 30 give one."""
    short = block_bootstrap_sharpe_ci(_NOISE[:29], seed=0, resamples=50)
    full = block_bootstrap_sharpe_ci(_NOISE[:30], seed=0, resamples=50)
    assert (short, None if full is None else full.resamples) == (None, 50)


def test_flat_series_is_not_computed() -> None:
    """A flat series has no Sharpe in any resample, so there is no interval."""
    flat = block_bootstrap_sharpe_ci([0.0] * 60, seed=0, resamples=50)
    noisy = block_bootstrap_sharpe_ci(_NOISE[:60], seed=0, resamples=50)
    assert (flat, None if noisy is None else noisy.resamples) == (None, 50)


def test_invalid_sizes_raise() -> None:
    """Non-positive resample count or block length is a caller error."""
    with pytest.raises(ValueError, match="must be positive"):
        block_bootstrap_sharpe_ci(_DRIFT, seed=0, resamples=0)
    with pytest.raises(ValueError, match="must be positive"):
        block_bootstrap_sharpe_ci(_DRIFT, seed=0, block_length=0)


@pytest.mark.parametrize("confidence", [0.0, 1.0])
def test_confidence_outside_unit_interval_raises(confidence: float) -> None:
    """Confidence must be strictly between 0 and 1."""
    with pytest.raises(ValueError, match="confidence"):
        block_bootstrap_sharpe_ci(_DRIFT, seed=0, confidence=confidence)


@settings(max_examples=25)
@given(seed=st.integers(min_value=0, max_value=2**64 - 1))
def test_lower_bound_never_exceeds_upper(seed: int) -> None:
    """For any seed the interval is ordered."""
    interval = block_bootstrap_sharpe_ci(_NOISE, seed=seed, resamples=100)
    assert interval is not None
    assert interval.lower <= interval.upper


@given(n=st.integers(min_value=1, max_value=10_000))
def test_below_stays_in_range(n: int) -> None:
    """The block-start draw is always a valid index."""
    rng = _SplitMix64(n)
    assert {rng.below(n) for _ in range(20)} <= set(range(n))
