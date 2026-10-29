"""Shadow-parity calibration metrics M-09 and M-10, computed blind.

Shadow places no real orders, so "sim vs real" means the live shadow run
against a backtest replay of the same days (Code Owner ruling, 2026-10-03).
Thresholds are ADR-011 D-19's (Accepted), not the success-criteria draft's:

- **M-09 P&L correlation**: Pearson r between the two cumulative net P&L
  curves over matched trades must exceed 0.85, and the lower bound of its
  bootstrap 95% CI must exceed 0.70. Bootstrap: 10,000 resamples of trade
  pairs (SA-5 §3.2), kept in time order before cumulating. Fewer than 30
  matched trades is "insufficient" (SA-5 §3.1).
- **M-10 Sharpe match**: |shadow Sharpe - replay Sharpe| must be below 0.3,
  each a 30-day rolling Sharpe on daily returns annualised with √365 (the
  same convention as the §10 shadow kill).

Blindness (ruling 1 / ADR-012 D-06, binding rule 4 of the 2026-10-03
shadow-P&L decision): a :class:`MetricVerdict` exposes only its status. The
raw value is sealed: it is left out of ``repr`` and ``str``, and
:meth:`MetricVerdict.unseal` refuses until the strategy is certified. Every
notification, log line or report built from a verdict therefore carries
pass, block or insufficient only.

Reference: issue #360; ADR-011 D-19; decisions journal
``2026-10-03-calibration-metrics-and-shadow-sharpe-kill``.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from itertools import accumulate

from hermes.application.strategy_protocol.shadow_sharpe_kill import rolling_annualised_sharpe
from hermes.domain.statistics.bootstrap import percentile
from hermes.domain.statistics.correlation import pearson
from hermes.domain.statistics.rng import SplitMix64

__all__ = [
    "M09_CI_LOWER_MIN",
    "M09_MIN_TRADES",
    "M09_R_MIN",
    "M10_MAX_SHARPE_GAP",
    "MetricStatus",
    "MetricVerdict",
    "SealedValueError",
    "m09_pnl_correlation",
    "m10_sharpe_match",
]

M09_R_MIN = 0.85
"""ADR-011 D-19: Pearson r must exceed this."""

M09_CI_LOWER_MIN = 0.70
"""ADR-011 D-19: the bootstrap 95% CI lower bound must exceed this."""

M09_MIN_TRADES = 30
"""SA-5 §3.1: below 30 matched trades the correlation is not evaluated."""

M10_MAX_SHARPE_GAP = 0.3
"""ADR-011 D-19: the annualised Sharpe gap must stay below this."""

_RESAMPLES = 10_000
"""SA-5 §3.2: bootstrap resamples for the correlation CI."""


class MetricStatus(StrEnum):
    """What a verdict may show before certification."""

    OK = "pass"
    BLOCK = "block"
    INSUFFICIENT = "insufficient"


class SealedValueError(PermissionError):
    """A sealed metric value was requested before certification."""


@dataclass(frozen=True, slots=True)
class MetricVerdict:
    """A calibration result whose raw value stays sealed until certification."""

    metric: str
    status: MetricStatus
    _value: float | None = field(default=None, repr=False, compare=False)

    def __str__(self) -> str:
        """Render metric and status only."""
        return f"{self.metric}: {self.status}"

    def unseal(self, *, certified: bool) -> float | None:
        """Return the raw value, only once the strategy is certified.

        Raises:
            SealedValueError: ``certified`` is false.
        """
        if not certified:
            raise SealedValueError(f"{self.metric} value is sealed until certification")
        return self._value


def _cumulative(values: Sequence[float]) -> list[float]:
    return list(accumulate(values))


def m09_pnl_correlation(
    shadow_trade_pnl: Sequence[float],
    replay_trade_pnl: Sequence[float],
    *,
    seed: int,
    resamples: int = _RESAMPLES,
) -> MetricVerdict:
    """M-09: correlation of cumulative net P&L, shadow against backtest replay.

    Args:
        shadow_trade_pnl: Net P&L of each matched trade in the live shadow run.
        replay_trade_pnl: Net P&L of the same trades in the backtest replay.
        seed: Bootstrap seed, so the verdict is reproducible.
        resamples: Bootstrap resamples (tests pass fewer).

    Returns:
        PASS when r > 0.85 and the CI lower bound > 0.70, BLOCK otherwise,
        INSUFFICIENT below 30 matched trades or when r is undefined.

    Raises:
        ValueError: The two series differ in length.
    """
    if len(shadow_trade_pnl) != len(replay_trade_pnl):
        raise ValueError("shadow and replay trades must be matched one to one")
    n = len(shadow_trade_pnl)
    if n < M09_MIN_TRADES:
        return MetricVerdict("M-09", MetricStatus.INSUFFICIENT)
    r = pearson(_cumulative(shadow_trade_pnl), _cumulative(replay_trade_pnl))
    if r is None:
        return MetricVerdict("M-09", MetricStatus.INSUFFICIENT)

    rng = SplitMix64(seed)
    samples: list[float] = []
    for _ in range(resamples):
        picks = sorted(rng.below(n) for _ in range(n))
        value = pearson(
            _cumulative([shadow_trade_pnl[i] for i in picks]),
            _cumulative([replay_trade_pnl[i] for i in picks]),
        )
        if value is not None:
            samples.append(value)
    if not samples:
        return MetricVerdict("M-09", MetricStatus.INSUFFICIENT, r)
    samples.sort()
    ci_lower = percentile(samples, 0.025)
    passed = r > M09_R_MIN and ci_lower > M09_CI_LOWER_MIN
    return MetricVerdict("M-09", MetricStatus.OK if passed else MetricStatus.BLOCK, r)


def m10_sharpe_match(
    shadow_daily_returns: Sequence[float], replay_daily_returns: Sequence[float]
) -> MetricVerdict:
    """M-10: gap between the shadow and replay 30-day annualised Sharpe.

    Args:
        shadow_daily_returns: Daily returns of the live shadow run, oldest first.
        replay_daily_returns: Daily returns of the backtest replay, same days.

    Returns:
        PASS when the gap is below 0.3, BLOCK otherwise, INSUFFICIENT
        without 30 days or with an undefined Sharpe on either side.

    Raises:
        ValueError: The two series differ in length.
    """
    if len(shadow_daily_returns) != len(replay_daily_returns):
        raise ValueError("shadow and replay returns must cover the same days")
    shadow = rolling_annualised_sharpe(shadow_daily_returns)
    replay = rolling_annualised_sharpe(replay_daily_returns)
    if shadow is None or replay is None:
        return MetricVerdict("M-10", MetricStatus.INSUFFICIENT)
    gap = abs(shadow - replay)
    status = MetricStatus.OK if gap < M10_MAX_SHARPE_GAP else MetricStatus.BLOCK
    return MetricVerdict("M-10", status, gap)
