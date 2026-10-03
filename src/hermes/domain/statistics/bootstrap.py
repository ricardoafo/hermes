"""Circular block bootstrap confidence interval on the Sharpe ratio.

Resampling whole blocks keeps the autocorrelation inside each block, which an
i.i.d. bootstrap would destroy. The circular variant wraps blocks around the
end of the series so every observation is equally likely to be drawn.

Reference: pre-registration-v1.md §8.2 (10,000 resamples, 30-day blocks,
H1 needs the 95% lower bound above 0);
openspec/changes/354-certification-statistics D-3.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from hermes.domain.statistics.rng import SplitMix64 as _SplitMix64
from hermes.domain.statistics.sharpe import sharpe_ratio

__all__ = ["SharpeInterval", "block_bootstrap_sharpe_ci", "percentile"]


@dataclass(frozen=True, slots=True)
class SharpeInterval:
    """A two-sided percentile interval on per-period Sharpe."""

    lower: float
    upper: float
    resamples: int
    """Resamples with a defined Sharpe; degenerate resamples are dropped."""


def block_bootstrap_sharpe_ci(
    returns: Sequence[float],
    *,
    seed: int,
    resamples: int = 10_000,
    block_length: int = 30,
    confidence: float = 0.95,
) -> SharpeInterval | None:
    """Return the circular block bootstrap percentile interval on Sharpe.

    Args:
        returns: Per-period (daily) returns.
        seed: Seed for the block-start generator; the same seed and inputs give
            the same interval on every platform and Python version.
        resamples: Number of bootstrap resamples.
        block_length: Block length in periods.
        confidence: Two-sided confidence level.

    Returns:
        The interval, or ``None`` when the series is shorter than one block or
        no resample has a defined Sharpe.

    Raises:
        ValueError: Non-positive ``resamples`` or ``block_length``, or a
            confidence outside (0, 1).
    """
    if resamples < 1 or block_length < 1:
        raise ValueError("resamples and block_length must be positive")
    if not 0.0 < confidence < 1.0:
        raise ValueError(f"confidence must be in (0, 1), got {confidence}")
    n = len(returns)
    if n < block_length:
        return None

    rng = _SplitMix64(seed)
    blocks_needed = -(-n // block_length)
    sharpes: list[float] = []
    for _ in range(resamples):
        sample: list[float] = []
        for _ in range(blocks_needed):
            start = rng.below(n)
            sample.extend(returns[(start + i) % n] for i in range(block_length))
        value = sharpe_ratio(sample[:n])
        if value is not None:
            sharpes.append(value)
    if not sharpes:
        return None

    sharpes.sort()
    tail = (1.0 - confidence) / 2.0
    return SharpeInterval(
        lower=percentile(sharpes, tail),
        upper=percentile(sharpes, 1.0 - tail),
        resamples=len(sharpes),
    )


def percentile(ordered: Sequence[float], q: float) -> float:
    """Linear-interpolated quantile of an ascending sequence."""
    position = q * (len(ordered) - 1)
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)
