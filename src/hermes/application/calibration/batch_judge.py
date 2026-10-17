"""Judge a research batch at its end and pick the champion by the signed rule.

Every candidate in a batch is judged once all of the batch's trials are in the
log, so each faces the same trial count (ADR-026 §4): :func:`batch_trial_summary`
holds that N. :func:`judge_batch` then applies the selection rule signed with the
batch (ADR-026 §5): the gate, DSR row included, decides who is eligible,
Benjamini-Hochberg over the whole batch controls false discoveries, and the
champion is the surviving candidate with the highest DSR. Pure: the judgement
is decided in code (ADR-012 D-08) and does not depend on the order the
candidates ran in.

Reference: ADR-026 §3-§5; ADR-021 §5; issue #1059; the research funnel
interface contract §4; openspec/changes/1059-batch-judge/change.md.
"""

import math
import statistics
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from fractions import Fraction

from hermes.domain.calibration.trial_record import TrialRecord, TrialSummary, summarise_trials
from hermes.domain.statistics.false_discovery import benjamini_hochberg

__all__ = [
    "BatchJudgement",
    "CandidateResult",
    "SelectionRule",
    "batch_trial_summary",
    "judge_batch",
]

_UNDEFINED_CORRELATION = 1.0
"""A flat series has no correlation; counting it as 1 ranks it last in a tie (fails closed)."""


@dataclass(frozen=True, slots=True)
class SelectionRule:
    """The champion rule a batch signs before any run (ADR-026 §5).

    The DSR floor is not here: it is a gate row, judged with the batch-wide N.

    Args:
        fdr_q: The Benjamini-Hochberg level, 0 < q < 1.
    """

    fdr_q: float

    def __post_init__(self) -> None:
        """Refuse a level outside (0, 1)."""
        if not 0.0 < self.fdr_q < 1.0:
            raise ValueError(f"fdr_q must be in (0, 1), got {self.fdr_q}")


@dataclass(frozen=True, slots=True)
class CandidateResult:
    """What one candidate's gate produced, as the batch judge needs it.

    Args:
        strategy_id: The candidate's strategy id.
        gate_passed: Whether every gate row passed, the DSR row included.
        dsr: The DSR taken with :func:`batch_trial_summary`, or ``None`` when it
            could not be computed.
        daily_returns: The candidate's daily returns over the judged window,
            for the correlation tie-break.
    """

    strategy_id: str
    gate_passed: bool
    dsr: float | None
    daily_returns: tuple[float, ...]

    def __post_init__(self) -> None:
        """Refuse a DSR that is not a probability."""
        if self.dsr is not None and not 0.0 <= self.dsr <= 1.0:
            raise ValueError(f"dsr must be in [0, 1] or None, got {self.dsr}")


@dataclass(frozen=True, slots=True)
class BatchJudgement:
    """The batch's verdict. Ids only: ordinal, never outcome values (ADR-026 §5).

    Args:
        eligible: Candidates whose gate passed, in rule order. After Train,
            these go on to Validation (ADR-026 §3).
        survivors: Eligible candidates Benjamini-Hochberg keeps, in rule order.
    """

    eligible: tuple[str, ...]
    survivors: tuple[str, ...]

    @property
    def champion(self) -> str | None:
        """The first survivor, or ``None`` when the batch has no champion."""
        return self.survivors[0] if self.survivors else None


def batch_trial_summary(records: Iterable[TrialRecord]) -> TrialSummary:
    """Return the trial summary every candidate's DSR is deflated by.

    Every line of the experiment's log counts, for every strategy and split,
    v1 and v2 included (ADR-026 §4, ADR-012 D-11), and the Sharpe variance is
    over the same lines. The raw count is an upper bound on any implied number
    of independent trials, so its N is the conservative one.

    Args:
        records: The whole trial log, once the batch's runs are appended.

    Returns:
        The summary, with no strategy filter.
    """
    return summarise_trials(records)


def judge_batch(candidates: Sequence[CandidateResult], rule: SelectionRule) -> BatchJudgement:
    """Apply the signed selection rule to every candidate of a batch at once.

    Args:
        candidates: One result per candidate, in any order.
        rule: The batch's signed selection rule.

    Returns:
        The eligible candidates and the survivors, each in rule order.

    Raises:
        ValueError: No candidate, a repeated strategy id, or return series of
            different lengths.
    """
    _refuse_malformed(candidates)
    eligible = [c for c in candidates if c.gate_passed and c.dsr is not None]
    # BH runs over the whole batch: with m = the eligible count it could never
    # remove anyone at q = 1 - floor, since every eligible p = 1 - DSR is <= q.
    p_values = [Fraction(1) if c.dsr is None else 1 - _decimal(c.dsr) for c in candidates]
    kept = benjamini_hochberg(p_values, q=_decimal(rule.fdr_q))
    rejected = {c.strategy_id for c, k in zip(candidates, kept, strict=True) if k}
    survivors = [c for c in eligible if c.strategy_id in rejected]
    return BatchJudgement(eligible=_rank(eligible), survivors=_rank(survivors))


def _decimal(value: float) -> Fraction:
    """Read a float as the decimal it prints as: 0.95 is 19/20, not its binary neighbour."""
    return Fraction(repr(value))


def _refuse_malformed(candidates: Sequence[CandidateResult]) -> None:
    if not candidates:
        raise ValueError("a batch needs at least one candidate")
    ids = [c.strategy_id for c in candidates]
    repeated = sorted({i for i in ids if ids.count(i) > 1})
    if repeated:
        raise ValueError(f"strategy ids appear more than once: {repeated}")
    if len({len(c.daily_returns) for c in candidates}) > 1:
        raise ValueError("every candidate needs the same number of daily returns")


def _rank(candidates: Sequence[CandidateResult]) -> tuple[str, ...]:
    """Order by DSR, highest first; then lowest mean correlation with the rest; then id."""

    def key(candidate: CandidateResult) -> tuple[float, float, str]:
        others = [o for o in candidates if o is not candidate]
        mean_correlation = (
            statistics.fmean(_correlation(candidate, o) for o in others) if others else 0.0
        )
        return (-(candidate.dsr or 0.0), mean_correlation, candidate.strategy_id)

    return tuple(c.strategy_id for c in sorted(candidates, key=key))


def _correlation(a: CandidateResult, b: CandidateResult) -> float:
    try:
        value = statistics.correlation(a.daily_returns, b.daily_returns)
    except statistics.StatisticsError:
        return _UNDEFINED_CORRELATION
    return value if math.isfinite(value) else _UNDEFINED_CORRELATION
