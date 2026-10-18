"""Judge every candidate of a research batch at one stage and record the verdicts.

:func:`judge_stage` runs the generic gate (:func:`judge_candidate`) over each
candidate's evidence folder with one trial summary for the whole log
(ADR-026 §4). After Train, the candidates whose gate passed go on to
Validation (ADR-026 §3). After Validation, :func:`judge_batch` applies the
selection rule the batch signed and names the champion, or none (ADR-026 §5).

:func:`verdict_record` turns one candidate's verdict into the committed record
(contract §5): statuses only, never a metric value (ADR-012 D-06).

Reference: ADR-026 §3-§5; issue #1059; the research funnel interface contract
§4 and §5; openspec/changes/1059-funnel-cli/change.md.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Final, Literal

from hermes.application.calibration.batch_judge import (
    CandidateResult,
    SelectionRule,
    batch_trial_summary,
    judge_batch,
)
from hermes.application.calibration.evidence_files import load_funnel_evidence
from hermes.application.calibration.research_batch import Candidate, ResearchBatch
from hermes.application.calibration.signed_gate import (
    CandidateEvidence,
    CandidateJudge,
    CheckResult,
    judge_candidate,
)
from hermes.domain.calibration.trial_record import TrialRecord
from hermes.domain.market_data.dataset_manifest import DataSplit
from hermes.domain.statistics.performance import daily_returns

__all__ = [
    "MISSING_EVIDENCE",
    "VERDICT_SCHEMA",
    "CandidateVerdict",
    "Stage",
    "StageOutcome",
    "judge_stage",
    "stage_summary",
    "verdict_record",
]

Stage = Literal["train", "validation"]

MISSING_EVIDENCE: Final = "missing_evidence"
"""The one check a candidate with no evidence folder gets: FAIL."""

VERDICT_SCHEMA: Final = "hermes.verdict/1"

_SPLITS: Final[Mapping[Stage, DataSplit]] = {
    "train": DataSplit.TRAIN,
    "validation": DataSplit.VALIDATION,
}

_STAGE_NAMES: Final[Mapping[Stage, str]] = {"train": "Train", "validation": "Validation"}


@dataclass(frozen=True, slots=True)
class CandidateVerdict:
    """One candidate's verdict at one stage.

    Args:
        candidate: The batch's candidate.
        checks: The gate's checks, or the single ``missing_evidence`` FAIL.
        passed: Whether no check failed.
        dsr: The DSR with the batch-wide trial count, for the judge only;
            never printed or recorded.
        window: The evidence's first and last day, or ``None`` without evidence.
        returns: The daily returns over ``window``, for the tie-break only.
        trial_lines: The candidate's trial-log lines for this batch and stage.
    """

    candidate: Candidate
    checks: tuple[CheckResult, ...]
    passed: bool
    dsr: float | None
    window: tuple[date, date] | None
    returns: tuple[float, ...]
    trial_lines: int

    @property
    def strategy_id(self) -> str:
        """The candidate's strategy id."""
        return self.candidate.strategy_id


@dataclass(frozen=True, slots=True)
class StageOutcome:
    """Every candidate's verdict at one stage, and who goes on.

    Args:
        batch_id: The batch.
        stage: ``train`` or ``validation``.
        verdicts: One per candidate, in batch order.
        goes_on: After Train, the candidates that go on to Validation, in batch
            order; empty after Validation.
        survivors: After Validation, the candidates the selection rule keeps,
            in rule order; empty after Train.
    """

    batch_id: str
    stage: Stage
    verdicts: tuple[CandidateVerdict, ...]
    goes_on: tuple[str, ...]
    survivors: tuple[str, ...]

    @property
    def champion(self) -> str | None:
        """The first survivor, or ``None``; always ``None`` after Train."""
        return self.survivors[0] if self.survivors else None


def judge_stage(
    batch: ResearchBatch,
    stage: Stage,
    evidence_dir: Path,
    records: Iterable[TrialRecord],
    *,
    judge: CandidateJudge = judge_candidate,
) -> StageOutcome:
    """Judge every candidate of ``batch`` at ``stage``.

    Args:
        batch: The signed batch.
        stage: ``train`` or ``validation``.
        evidence_dir: Holds ``<strategy_id>/`` with the three evidence files of
            each candidate that ran; a candidate without a folder fails
            ``missing_evidence``.
        records: The whole trial log, the batch's runs appended.
        judge: The per-candidate gate.

    Returns:
        The verdicts and who goes on (Train) or survives (Validation).

    Raises:
        ValueError: A candidate's evidence is unusable or not a ``stage`` run,
            or the evidence windows of the candidates differ.
        OSError: An evidence file cannot be read.
    """
    log = tuple(records)
    trials = batch_trial_summary(log)
    verdicts: list[CandidateVerdict] = []
    for candidate in batch.candidates:
        lines = _trial_lines(log, batch.batch_id, candidate.strategy_id, stage)
        folder = evidence_dir / candidate.strategy_id
        if not folder.is_dir():
            verdicts.append(_missing(candidate, stage, lines))
            continue
        files = CandidateEvidence(
            performance=folder / "performance.json",
            evidence=folder / "evidence.json",
            funnel_evidence=folder / "funnel-evidence.json",
        )
        result = judge(candidate.strategy_id, stage, files, batch, trials)
        funnel = load_funnel_evidence(files.funnel_evidence, strategy_id=candidate.strategy_id)
        verdicts.append(
            CandidateVerdict(
                candidate=candidate,
                checks=result.checks,
                passed=result.passed,
                dsr=result.dsr,
                window=(funnel.start_date, funnel.end_date),
                returns=tuple(daily_returns(funnel.daily_equity)),
                trial_lines=lines,
            )
        )
    if stage == "train":
        goes_on = tuple(v.strategy_id for v in verdicts if v.passed)
        return StageOutcome(batch.batch_id, stage, tuple(verdicts), goes_on, ())
    _refuse_mixed_windows(verdicts)
    judgement = judge_batch(_judged(verdicts), SelectionRule(fdr_q=batch.selection.fdr_q))
    return StageOutcome(batch.batch_id, stage, tuple(verdicts), (), judgement.survivors)


def verdict_record(outcome: StageOutcome, verdict: CandidateVerdict) -> dict[str, object]:
    """Return the committed verdict record (contract §5) for one candidate.

    Statuses only: ``run_id`` and ``trial_lines`` are the only numbers, and
    ``run_id`` and ``commit`` are ``None`` until the workflow fills them.
    """
    window = (
        None
        if verdict.window is None
        else {"start": verdict.window[0].isoformat(), "end": verdict.window[1].isoformat()}
    )
    at_train = outcome.stage == "train"
    return {
        "schema": VERDICT_SCHEMA,
        "batch_id": outcome.batch_id,
        "strategy_id": verdict.strategy_id,
        "idea_family": verdict.candidate.idea_family,
        "stage": outcome.stage,
        "run_id": None,
        "commit": None,
        "window": window,
        "trial_lines": verdict.trial_lines,
        "checks": [{"check": c.check, "status": c.status} for c in verdict.checks],
        "passed": verdict.passed,
        "goes_on": verdict.strategy_id in outcome.goes_on if at_train else None,
        "champion": None if at_train else verdict.strategy_id == outcome.champion,
    }


def stage_summary(outcome: StageOutcome) -> dict[str, object]:
    """Return ``validation-candidates.json`` (Train) or ``selection.json`` (Validation)."""
    if outcome.stage == "train":
        return {"batch_id": outcome.batch_id, "stage": "train", "candidates": list(outcome.goes_on)}
    return {
        "batch_id": outcome.batch_id,
        "stage": "validation",
        "survivors": list(outcome.survivors),
        "champion": outcome.champion,
    }


def _trial_lines(log: Iterable[TrialRecord], batch_id: str, strategy_id: str, stage: Stage) -> int:
    split = _SPLITS[stage]
    return sum(
        1
        for r in log
        if r.strategy_id == strategy_id
        and r.split is split
        and r.parameters.get("batch.id") == batch_id
    )


def _missing(candidate: Candidate, stage: Stage, trial_lines: int) -> CandidateVerdict:
    return CandidateVerdict(
        candidate=candidate,
        checks=(CheckResult(MISSING_EVIDENCE, "FAIL", f"no {_STAGE_NAMES[stage]} evidence"),),
        passed=False,
        dsr=None,
        window=None,
        returns=(),
        trial_lines=trial_lines,
    )


def _refuse_mixed_windows(verdicts: Iterable[CandidateVerdict]) -> None:
    """Every judged candidate must cover the same days, or the tie-break compares unlike series."""
    windows = {v.window for v in verdicts if v.window is not None}
    if len(windows) > 1:
        raise ValueError(f"the candidates' evidence covers different windows: {sorted(windows)}")


def _judged(verdicts: list[CandidateVerdict]) -> list[CandidateResult]:
    # A candidate without evidence takes part in the BH step with p = 1 and is
    # never eligible, so its returns are never read; zeros only give the
    # series the common length the judge checks.
    length = max((len(v.returns) for v in verdicts), default=0)
    return [
        CandidateResult(
            strategy_id=v.strategy_id,
            gate_passed=v.passed,
            dsr=v.dsr,
            daily_returns=v.returns if v.window is not None else (0.0,) * length,
        )
        for v in verdicts
    ]
