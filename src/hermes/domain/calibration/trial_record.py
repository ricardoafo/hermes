"""Trial records — one per backtest run, for an honest Deflated Sharpe trial count.

Pre-registration v1 §8.1 counts every backtest run during Train and Validation
as a trial, and the DSR penalises the best result by the number of trials. A
:class:`TrialRecord` captures one run: what was tested (strategy, parameters,
pinned datasets, split, window), what it produced (metrics), and on which code
(git SHA). :func:`summarise_trials` turns the log into the trial count and the
Sharpe variance the DSR needs.

Reference: pre-registration-v1.md §8.1, openspec/archive/357-trial-log/change.md.
"""

import re
import statistics
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType

from hermes.domain.market_data.dataset_manifest import DataSplit

__all__ = ["DatasetRef", "TrialRecord", "TrialSummary", "summarise_trials"]

_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
# A sample variance needs at least two observations.
_MIN_SHARPES_FOR_VARIANCE = 2


@dataclass(frozen=True, slots=True)
class DatasetRef:
    """A pinned dataset a trial read, by manifest id and content hash."""

    dataset_id: str
    content_sha256: str

    def __post_init__(self) -> None:
        """Validate the hash format."""
        if not _SHA256_HEX.match(self.content_sha256):
            raise ValueError(
                f"DatasetRef.content_sha256 is not a sha256 hex digest: {self.content_sha256!r}"
            )


@dataclass(frozen=True, slots=True)
class TrialRecord:
    """One backtest run.

    Args:
        recorded_at: When the run finished (UTC).
        strategy_id: Strategy variant id.
        parameters: Every strategy parameter the run used, as strings.
        split: ``TRAIN``, or ``VALIDATION`` when the evaluation window touches
            Validation. OOS runs are certification, not trials.
        window_start: Evaluation window start (UTC, inclusive).
        window_end: Evaluation window end (UTC, exclusive).
        datasets: Pinned datasets the run read, warm-up included.
        metrics: Headline metrics of the run, as strings.
        sharpe: Per-period Sharpe ratio of the run, when the driver computes one.
        git_sha: Commit the run executed.
        git_dirty: Whether tracked files differed from that commit.
    """

    recorded_at: datetime
    strategy_id: str
    parameters: Mapping[str, str]
    split: DataSplit
    window_start: datetime
    window_end: datetime
    datasets: tuple[DatasetRef, ...]
    metrics: Mapping[str, str]
    sharpe: float | None
    git_sha: str
    git_dirty: bool

    def __post_init__(self) -> None:
        """Validate times, split, datasets and the git SHA, then freeze the mappings."""
        for name, value in (
            ("recorded_at", self.recorded_at),
            ("window_start", self.window_start),
            ("window_end", self.window_end),
        ):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"TrialRecord.{name} must be timezone-aware UTC, got {value!r}")
        if self.window_start >= self.window_end:
            raise ValueError("TrialRecord.window_start must precede window_end")
        if self.split is DataSplit.OOS:
            raise ValueError("TrialRecord.split cannot be OOS; the OOS test is certification")
        if not self.strategy_id:
            raise ValueError("TrialRecord.strategy_id must not be empty")
        if not self.datasets:
            raise ValueError("TrialRecord.datasets must name at least one pinned dataset")
        if not _GIT_SHA.match(self.git_sha):
            raise ValueError(f"TrialRecord.git_sha must be a 40-char hex SHA, got {self.git_sha!r}")
        object.__setattr__(self, "parameters", MappingProxyType(dict(self.parameters)))
        object.__setattr__(self, "metrics", MappingProxyType(dict(self.metrics)))


@dataclass(frozen=True, slots=True)
class TrialSummary:
    """What the DSR needs from the trial log.

    Args:
        count: Every trial, Train and Validation.
        validation_count: Trials whose window touched Validation.
        sharpe_variance: Sample variance of the trials' Sharpe ratios, or
            ``None`` when fewer than two trials report one.
    """

    count: int
    validation_count: int
    sharpe_variance: float | None


def summarise_trials(
    records: Iterable[TrialRecord], *, strategy_id: str | None = None
) -> TrialSummary:
    """Return the trial count and Sharpe variance of ``records``.

    Args:
        records: The trial log.
        strategy_id: When set, only that strategy's trials count.

    Returns:
        The summary.
    """
    selected = [r for r in records if strategy_id is None or r.strategy_id == strategy_id]
    sharpes = [r.sharpe for r in selected if r.sharpe is not None]
    return TrialSummary(
        count=len(selected),
        validation_count=sum(1 for r in selected if r.split is DataSplit.VALIDATION),
        sharpe_variance=statistics.variance(sharpes)
        if len(sharpes) >= _MIN_SHARPES_FOR_VARIANCE
        else None,
    )
