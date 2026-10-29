"""Sharpe-ratio statistics: per-period Sharpe, moments, PSR and the Deflated Sharpe Ratio.

All Sharpe ratios here are per period (not annualised), which is the unit the
PSR and DSR formulas use. Kurtosis is the plain fourth standardised moment
(3 for a normal distribution), not excess kurtosis.

Reference: pre-registration-v1.md §8.1; Bailey and López de Prado (2014),
"The Deflated Sharpe Ratio"; openspec/archive/354-certification-statistics D-2.
"""

import math
from collections.abc import Sequence
from statistics import NormalDist, fmean

__all__ = [
    "deflated_sharpe_ratio",
    "expected_max_sharpe",
    "kurtosis",
    "probabilistic_sharpe_ratio",
    "sharpe_ratio",
    "sharpe_t_statistic",
    "skewness",
]

_EULER_MASCHERONI = 0.5772156649015329
_NORMAL = NormalDist()
_MIN_POINTS = 2
"""Fewest returns a sample moment or sqrt(T - 1) can use."""


def _centred_moment(returns: Sequence[float], order: int) -> float:
    mean = fmean(returns)
    return fmean([(r - mean) ** order for r in returns])


def sharpe_ratio(returns: Sequence[float]) -> float | None:
    """Return mean over sample standard deviation, per period.

    Args:
        returns: Per-period returns.

    Returns:
        The Sharpe ratio, or ``None`` with fewer than two returns or zero
        dispersion.
    """
    if len(returns) < _MIN_POINTS:
        return None
    mean = fmean(returns)
    variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    if variance <= 0.0:
        return None
    return mean / math.sqrt(variance)


def sharpe_t_statistic(returns: Sequence[float]) -> float | None:
    """Return the t-statistic of the mean return, SR * sqrt(T).

    Args:
        returns: Per-period returns.

    Returns:
        ``mean / (s / sqrt(T))`` with ``s`` the sample standard deviation, or
        ``None`` when the Sharpe ratio is undefined.

    Reference: ADR-021 §5; openspec/archive/v2-backtest-funnel D-5.
    """
    sharpe = sharpe_ratio(returns)
    if sharpe is None:
        return None
    return sharpe * math.sqrt(len(returns))


def skewness(returns: Sequence[float]) -> float | None:
    """Return the third standardised moment (population form).

    Args:
        returns: Per-period returns.

    Returns:
        Skewness, or ``None`` with fewer than two returns or zero dispersion.
    """
    if len(returns) < _MIN_POINTS:
        return None
    m2 = _centred_moment(returns, 2)
    if m2 <= 0.0:
        return None
    return float(_centred_moment(returns, 3) / m2**1.5)


def kurtosis(returns: Sequence[float]) -> float | None:
    """Return the fourth standardised moment, non-excess (normal = 3).

    Args:
        returns: Per-period returns.

    Returns:
        Kurtosis, or ``None`` with fewer than two returns or zero dispersion.
    """
    if len(returns) < _MIN_POINTS:
        return None
    m2 = _centred_moment(returns, 2)
    if m2 <= 0.0:
        return None
    return _centred_moment(returns, 4) / m2**2


def expected_max_sharpe(n_trials: int, sharpe_variance: float) -> float:
    """Return SR0, the expected maximum Sharpe of ``n_trials`` skill-less trials.

    SR0 = sqrt(V) * ((1 - g) Phi^-1(1 - 1/N) + g Phi^-1(1 - 1/(N*e))),
    with g the Euler-Mascheroni constant.

    Args:
        n_trials: Number of trials N, at least 1. One trial gives 0.
        sharpe_variance: Variance V of the trials' per-period Sharpe ratios.

    Returns:
        SR0 per period.

    Raises:
        ValueError: ``n_trials`` below 1 or a negative variance.
    """
    if n_trials < 1:
        raise ValueError(f"n_trials must be at least 1, got {n_trials}")
    if sharpe_variance < 0.0:
        raise ValueError(f"sharpe_variance must be non-negative, got {sharpe_variance}")
    if n_trials == 1:
        return 0.0
    first = _NORMAL.inv_cdf(1.0 - 1.0 / n_trials)
    second = _NORMAL.inv_cdf(1.0 - 1.0 / (n_trials * math.e))
    return math.sqrt(sharpe_variance) * (
        (1.0 - _EULER_MASCHERONI) * first + _EULER_MASCHERONI * second
    )


def probabilistic_sharpe_ratio(
    sharpe: float,
    benchmark: float,
    n_observations: int,
    skew: float,
    kurt: float,
) -> float | None:
    """Return the probability that the true Sharpe exceeds ``benchmark``.

    PSR = Phi((SR - SR*) sqrt(T - 1) / sqrt(1 - skew SR + (kurt - 1)/4 * SR²)).

    Args:
        sharpe: Observed per-period Sharpe SR.
        benchmark: Benchmark per-period Sharpe SR*.
        n_observations: Number of returns T.
        skew: Skewness skew of the returns.
        kurt: Non-excess kurtosis kurt of the returns.

    Returns:
        The PSR in [0, 1], or ``None`` when T < 2 or the variance term is not
        positive.
    """
    if n_observations < _MIN_POINTS:
        return None
    variance_term = 1.0 - skew * sharpe + (kurt - 1.0) / 4.0 * sharpe**2
    if variance_term <= 0.0:
        return None
    z = (sharpe - benchmark) * math.sqrt(n_observations - 1) / math.sqrt(variance_term)
    return _NORMAL.cdf(z)


def deflated_sharpe_ratio(
    returns: Sequence[float], n_trials: int, sharpe_variance: float
) -> float | None:
    """Return the Deflated Sharpe Ratio of ``returns`` (pre-registration §8.1).

    The PSR of the observed Sharpe against SR0, the Sharpe the best of
    ``n_trials`` skill-less trials would show by chance.

    Args:
        returns: Per-period returns of the selected strategy.
        n_trials: Number of trials run while developing it.
        sharpe_variance: Variance of the trials' per-period Sharpe ratios.

    Returns:
        The DSR in [0, 1], or ``None`` when the returns are degenerate.
    """
    sharpe = sharpe_ratio(returns)
    skew = skewness(returns)
    kurt = kurtosis(returns)
    if sharpe is None or skew is None or kurt is None:
        return None
    benchmark = expected_max_sharpe(n_trials, sharpe_variance)
    return probabilistic_sharpe_ratio(sharpe, benchmark, len(returns), skew, kurt)
