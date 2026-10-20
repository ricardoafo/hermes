"""The sweep candidates sent to Validation, and the batch its champion rule judges (ADR-031 §3-§4).

After Train, a sweep sends at most one candidate per family to Validation: the
Train passer with the highest Train DSR, computed and compared in code
(:func:`family_winners`). The champion rule then runs Benjamini-Hochberg over
those candidates only, so m is their count (the "family winners" rule the Code
Owner chose on 19 Oct); :func:`validation_batch` is the batch it judges.

The DSR used to rank is the judge's, with the batch-wide trial count; it is
never printed or recorded (ADR-012 D-06).

Reference: ADR-031 §3, §4; issues #1310, #1311.
"""

from dataclasses import replace

from hermes.application.calibration.batch_stage import StageOutcome
from hermes.application.calibration.research_batch import ResearchBatch

__all__ = ["family_winners", "validation_batch"]


def family_winners(outcome: StageOutcome) -> tuple[str, ...]:
    """The Train passer with the highest DSR in each family, in batch order.

    A tie keeps the candidate that comes first in the batch, which is the
    sweep's signed expansion order, so the pick never depends on anything
    but the verdicts.

    Args:
        outcome: The sweep's Train verdicts.

    Returns:
        At most one strategy id per family.

    Raises:
        ValueError: ``outcome`` is not a Train outcome.
    """
    if outcome.stage != "train":
        raise ValueError(f"family winners come from Train, not {outcome.stage}")
    best: dict[str, tuple[float, int]] = {}
    for index, verdict in enumerate(outcome.verdicts):
        if not verdict.passed or verdict.dsr is None:
            continue
        family = verdict.candidate.idea_family
        if family not in best or verdict.dsr > best[family][0]:
            best[family] = (verdict.dsr, index)
    picked = sorted(index for _, index in best.values())
    return tuple(outcome.verdicts[i].strategy_id for i in picked)


def validation_batch(batch: ResearchBatch, winners: tuple[str, ...]) -> ResearchBatch:
    """``batch`` with only the candidates sent to Validation, so BH's m is their count.

    Raises:
        ValueError: A winner is not a candidate of ``batch``.
    """
    known = {c.strategy_id for c in batch.candidates}
    if unknown := sorted(set(winners) - known):
        raise ValueError(f"not candidates of {batch.batch_id}: {unknown}")
    chosen = set(winners)
    return replace(batch, candidates=tuple(c for c in batch.candidates if c.strategy_id in chosen))
