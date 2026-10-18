"""Probability of backtest overfitting by combinatorially symmetric cross-validation.

CSCV cuts the T periods of N trials into S contiguous blocks. Every way of
choosing S/2 blocks as in-sample, with the rest out of sample, picks the trial
with the best in-sample Sharpe and asks where it ranks out of sample. PBO is
the share of those choices where it ranks at or below the median.

Reference: ADR-021 §5; Bailey, Borwein, López de Prado and Zhu (2015), "The
Probability of Backtest Overfitting"; openspec/archive/v2-backtest-funnel D-4.
"""

import math
from collections.abc import Sequence
from itertools import combinations

__all__ = ["probability_of_backtest_overfitting"]

_MIN_TRIALS = 2
_MIN_BLOCKS = 2
_MIN_ROWS_PER_BLOCK = 2
"""A block needs two returns for a sample standard deviation."""
_FLAT_TOLERANCE = 1e-12
"""Variance below this share of the mean square is cancellation noise, not dispersion."""


def _block_bounds(rows: int, blocks: int) -> list[int]:
    return [rows * b // blocks for b in range(blocks + 1)]


def _sharpe(total: float, total_sq: float, count: int) -> float:
    """Sharpe from a sum and a sum of squares; ``-inf`` when it is undefined.

    A trial with no dispersion in a sample cannot be told apart from noise, so
    it never wins in sample and ranks last out of sample (fails closed).
    """
    mean = total / count
    variance = (total_sq - total * mean) / (count - 1)
    if variance <= _FLAT_TOLERANCE * total_sq / count:
        return -math.inf
    return mean / math.sqrt(variance)


def _average_rank(values: Sequence[float], index: int) -> float:
    """1-based ascending rank of ``values[index]``; ties share their average rank."""
    target = values[index]
    below = sum(1 for v in values if v < target)
    equal = sum(1 for v in values if v == target)
    return below + (equal + 1) / 2


def probability_of_backtest_overfitting(trials: Sequence[Sequence[float]], *, blocks: int) -> float:
    """Return the CSCV probability of backtest overfitting.

    Args:
        trials: One sequence of per-period returns per trial, all over the
            same periods.
        blocks: S, the number of contiguous blocks; even and at least 2. When
            S does not divide T, block sizes differ by at most one period.

    Returns:
        PBO in [0, 1]: the share of the C(S, S/2) in-sample choices whose
        in-sample winner (first on a tie) has out-of-sample logit
        ``ln(w / (1 - w)) <= 0``, with ``w = rank / (N + 1)``.

    Raises:
        ValueError: Fewer than two trials, trials of different lengths, an odd
            or too small S, or fewer than two returns per block.
    """
    if blocks < _MIN_BLOCKS or blocks % 2:
        raise ValueError(f"blocks must be an even number >= 2, got {blocks}")
    if len(trials) < _MIN_TRIALS:
        raise ValueError(f"CSCV needs at least {_MIN_TRIALS} trials, got {len(trials)}")
    rows = len(trials[0])
    if any(len(trial) != rows for trial in trials):
        raise ValueError("every trial must have the same number of returns")
    if rows < _MIN_ROWS_PER_BLOCK * blocks:
        raise ValueError(
            f"{blocks} blocks need at least {_MIN_ROWS_PER_BLOCK * blocks} returns, got {rows}"
        )

    bounds = _block_bounds(rows, blocks)
    sizes = [bounds[b + 1] - bounds[b] for b in range(blocks)]
    sums = [[math.fsum(t[bounds[b] : bounds[b + 1]]) for b in range(blocks)] for t in trials]
    squares = [
        [math.fsum(r * r for r in t[bounds[b] : bounds[b + 1]]) for b in range(blocks)]
        for t in trials
    ]
    every_block = frozenset(range(blocks))
    n = len(trials)
    overfit = 0
    halves = 0
    for chosen in combinations(range(blocks), blocks // 2):
        rest = every_block.difference(chosen)
        in_count = sum(sizes[b] for b in chosen)
        out_count = rows - in_count
        in_sample = [
            _sharpe(
                math.fsum(sums[i][b] for b in chosen),
                math.fsum(squares[i][b] for b in chosen),
                in_count,
            )
            for i in range(n)
        ]
        out_of_sample = [
            _sharpe(
                math.fsum(sums[i][b] for b in rest),
                math.fsum(squares[i][b] for b in rest),
                out_count,
            )
            for i in range(n)
        ]
        winner = max(range(n), key=lambda i: in_sample[i])
        omega = _average_rank(out_of_sample, winner) / (n + 1)
        halves += 1
        if math.log(omega / (1.0 - omega)) <= 0.0:
            overfit += 1
    return overfit / halves
