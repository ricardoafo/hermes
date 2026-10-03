"""Classify and summarise backtest trials for the Deflated Sharpe Ratio.

Reference: pre-registration-v1.md §4 and §8.1, openspec/changes/357-trial-log/change.md.
"""

from __future__ import annotations

from datetime import datetime

from hermes.domain.calibration.trial_record import TrialSummary, summarise_trials
from hermes.domain.market_data.dataset_manifest import DatasetManifest, DataSplit
from hermes.ports.trial_log import TrialLogPort

__all__ = ["trial_split", "trial_summary"]


def trial_split(manifest: DatasetManifest, start: datetime, end: datetime) -> DataSplit:
    """Return the split a trial evaluating ``[start, end)`` counts against.

    Validation is to be touched sparingly (pre-registration §4), so a window
    that reaches into it at all counts as a Validation touch.

    Args:
        manifest: Manifest holding the split windows.
        start: Evaluation window start (UTC, inclusive).
        end: Evaluation window end (UTC, exclusive).

    Returns:
        ``VALIDATION`` when the window touches Validation, else ``TRAIN``.

    Raises:
        ValueError: The window touches OOS or lies outside every split.
    """
    splits = manifest.splits_intersecting(start, end)
    if DataSplit.OOS in splits:
        raise ValueError("a trial window cannot touch OOS; the OOS test is certification")
    if not splits:
        raise ValueError(f"window {start.isoformat()}..{end.isoformat()} lies outside every split")
    return DataSplit.VALIDATION if DataSplit.VALIDATION in splits else DataSplit.TRAIN


def trial_summary(log: TrialLogPort, *, strategy_id: str | None = None) -> TrialSummary:
    """Return the trial count and Sharpe variance read from ``log``.

    Args:
        log: The trial log; its chain is verified on read.
        strategy_id: When set, only that strategy's trials count.

    Returns:
        The summary the DSR takes its trial count from.
    """
    return summarise_trials(log.records(), strategy_id=strategy_id)
