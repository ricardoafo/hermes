"""One backtest gate for any signed strategy: v2 and every batch candidate.

ADR-026 §1 and §4: every candidate in a batch is judged by the same gate, with
thresholds read from the signed batch, so a new strategy needs no gate code.
:func:`evaluate_signed_gate` is that gate: v1's backtest gate (the §9 rows
pre-registration v1 signed) with the ``dsr`` row at the signed floor and the
ADR-021 §5 funnel rows after it. Pre-registration v2 and every batch
candidate go through it; only the thresholds differ, and they come from the
signed document.

:func:`judge_candidate` is the call the batch judge (issue #1059) makes once
per candidate. Its DSR uses the trial summary the judge passes in, so every
candidate in a batch is deflated by the same N (ADR-026 §4). On a Train-only
run ``validation_vs_train`` is not applicable (issue #1037): it compares the
Validation stretch with Train, and a Train run has no Validation stretch.

Reference: ADR-026 §1, §4; ADR-021 §5; issue #1058.
"""

import hashlib
import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Literal, Protocol

from hermes.application.calibration.backtest_funnel import (
    FunnelEvidence,
    FunnelThresholds,
    evaluate_funnel,
    validate_evidence,
)
from hermes.application.calibration.certification_gates import (
    CertificationRecord,
    GateCheck,
    GateEvidence,
    GateReport,
    evaluate_next_gate,
)
from hermes.application.calibration.evidence_files import (
    EvidenceError,
    load_evidence,
    load_funnel_evidence,
    load_performance,
)
from hermes.application.calibration.lifecycle import CertificationState
from hermes.application.calibration.research_batch import ResearchBatch, heading_level
from hermes.application.calibration.window_metrics import TrialStats, compute_window_metrics
from hermes.application.strategy_protocol.preregistration import PreRegistration
from hermes.domain.calibration.trial_record import TrialSummary

__all__ = [
    "NOT_APPLICABLE_ON_TRAIN",
    "CandidateEvidence",
    "CandidateJudge",
    "CheckResult",
    "CheckStatus",
    "GateResult",
    "Split",
    "candidate_gate_evidence",
    "check_result",
    "evaluate_signed_gate",
    "evidence_split",
    "judge_candidate",
    "require_v1_secondary",
]

CheckStatus = Literal["PASS", "FAIL", "NA"]
Split = Literal["train", "validation"]


@dataclass(frozen=True, slots=True)
class CheckResult:
    """One gate check. ``detail`` may hold values and is never printed to a job summary."""

    check: str
    status: CheckStatus
    detail: str


@dataclass(frozen=True, slots=True)
class GateResult:
    """A candidate's verdict on one split.

    ``checks`` holds the §9 metrics first, then ``dsr``, then the funnel checks.
    ``dsr`` is for the batch judge only and is never printed.

    Raises:
        ValueError: ``passed`` disagrees with the checks: it is True exactly
            when every check is PASS or NA and there is at least one check.
    """

    strategy_id: str
    split: Split
    checks: tuple[CheckResult, ...]
    dsr: float | None
    passed: bool

    def __post_init__(self) -> None:
        """Refuse a verdict whose ``passed`` the checks do not support."""
        supported = bool(self.checks) and all(c.status != "FAIL" for c in self.checks)
        if self.passed != supported:
            raise ValueError(
                f"{self.strategy_id} {self.split}: passed={self.passed} but the checks say "
                f"{supported}"
            )


@dataclass(frozen=True, slots=True)
class CandidateEvidence:
    """The three files a candidate's backtest job writes (contract §2)."""

    performance: Path
    evidence: Path
    funnel_evidence: Path


class CandidateJudge(Protocol):
    """``judge_candidate``: the call the batch judge makes once per candidate."""

    def __call__(
        self,
        strategy_id: str,
        split: Split,
        evidence: CandidateEvidence,
        batch: ResearchBatch,
        trials: TrialSummary,
    ) -> GateResult:
        """Judge ``strategy_id`` on ``split`` against the thresholds ``batch`` signed.

        Raises:
            ValueError: ``batch`` does not name ``strategy_id``, or the evidence
                is unusable.
        """
        ...


NOT_APPLICABLE_ON_TRAIN: Final = frozenset({"validation_vs_train"})
"""Checks a Train-only run cannot judge (issue #1037); binding on Validation."""

_EVALUATED_HERE: Final = frozenset(
    {CertificationState.UNTRUSTED, CertificationState.DEAD, CertificationState.CERTIFIED}
)
_UNTRUSTED_GATE_TIME: Final = datetime(1970, 1, 1, tzinfo=UTC)
"""The gate out of UNTRUSTED reads no clock; :func:`judge_candidate` passes this."""

_UNCHANGED: Final = "Unchanged from v1"
_NINE: Final = re.compile(r"^9\.?(\s|$)")


def evaluate_signed_gate(
    record: CertificationRecord,
    evidence: GateEvidence,
    *,
    now: datetime,
    v1: PreRegistration,
    dsr_min: float,
    thresholds: FunnelThresholds,
    funnel: FunnelEvidence | None,
    refusal: str,
) -> GateReport:
    """Evaluate the gate out of the record's state for a strategy signed with a funnel.

    Pure, like :func:`evaluate_next_gate`. Out of ``UNTRUSTED`` it is v1's
    backtest gate with the ``dsr`` row at ``>= dsr_min`` and the funnel rows
    appended; ``DEAD`` and ``CERTIFIED`` are refused as in v1; every other
    state is refused with ``refusal``, because no later gate is signed yet.

    Args:
        record: The strategy's record, after ``apply_guardrails``.
        evidence: The submitted evidence.
        now: Current UTC time.
        v1: v1's pre-registration, whose §9 secondary thresholds are kept.
        dsr_min: The signed DSR floor.
        thresholds: The signed ``backtest_funnel`` block.
        funnel: The run's validated funnel evidence; ``None`` fails the gate.
        refusal: Why a state with no signed gate is refused.

    Returns:
        The :class:`GateReport` for the next transition.
    """
    if record.state not in _EVALUATED_HERE:
        return GateReport(record.strategy_id, record.state, None, refused_reason=refusal)
    report = evaluate_next_gate(record, evidence, now=now, pre_reg=v1)
    if record.state != CertificationState.UNTRUSTED:
        return report
    dsr = evidence.backtest.dsr if evidence.backtest else None
    rows = tuple(_dsr_row(row, dsr, dsr_min) if row.name == "dsr" else row for row in report.checks)
    return replace(report, checks=(*rows, *_funnel_rows(funnel, thresholds)))


def _dsr_row(row: GateCheck, dsr: float | None, floor: float) -> GateCheck:
    return replace(row, requirement=f">= {floor:g}", passed=dsr is not None and dsr >= floor)


def _funnel_rows(
    funnel: FunnelEvidence | None, thresholds: FunnelThresholds
) -> tuple[GateCheck, ...]:
    if funnel is None:
        return (
            GateCheck("funnel", "ADR-021 §5 funnel (--funnel-evidence)", "not computed", False),
        )
    return evaluate_funnel(funnel, thresholds)


def require_v1_secondary(section: str) -> None:
    """Refuse a candidate whose §9 does not keep v1's secondary thresholds.

    A batch signs no §9 numbers of its own: each candidate's section is written
    from pre-registration v2, whose §9 secondary paragraph keeps v1's. The gate
    reads that sentence the way v2's parser does, so a section that signs
    other numbers is refused instead of judged against thresholds it did not sign.

    Args:
        section: The candidate's pre-registration section.

    Raises:
        ValueError: No heading starting ``Secondary`` under a ``9.`` heading,
            or its first line does not start "Unchanged from v1".
    """
    in_nine = in_secondary = False
    nine_level = 0
    for line in section.splitlines():
        level = heading_level(line)
        if level:
            title = line[level:].strip()
            if _NINE.match(title):
                in_nine, nine_level, in_secondary = True, level, False
            elif in_nine and level <= nine_level:
                in_nine = in_secondary = False
            else:
                in_secondary = in_nine and title.startswith("Secondary")
        elif in_secondary and line.strip():
            if line.strip().startswith(_UNCHANGED):
                return
            break
    raise ValueError(f"§9 secondary metrics do not say {_UNCHANGED!r}")


def evidence_split(funnel: FunnelEvidence, thresholds: FunnelThresholds) -> Split:
    """``train`` when the run ends on or before ``train_end``, else ``validation``."""
    return "train" if funnel.end_date <= thresholds.train_end else "validation"


def candidate_gate_evidence(
    strategy_id: str, evidence: CandidateEvidence, batch: ResearchBatch, trials: TrialSummary
) -> tuple[GateEvidence, FunnelEvidence]:
    """Read a candidate's three files and compute its Train + Validation metrics.

    The DSR is computed from the performance file's returns with ``trials``,
    not with the ``trials`` block the run wrote, so every candidate is
    deflated by the N the batch judge counted (ADR-026 §4). The returned
    evidence hash chains the three files' hashes in the order ``hermes
    certify`` reads them.

    Raises:
        EvidenceError: A file is not valid evidence, or the performance file
            is not a ``train_val`` window.
        FunnelEvidenceError: The funnel evidence is malformed, for another
            strategy, or reaches the OOS window.
        OSError: A file cannot be read.
    """
    gate = load_evidence(evidence.evidence)
    window, record = load_performance(evidence.performance)
    if window != "train_val":
        raise EvidenceError(f"{evidence.performance} is a {window} window, not train_val")
    stats = (
        None
        if trials.count < 1 or trials.sharpe_variance is None
        else TrialStats(trials.count, trials.sharpe_variance)
    )
    funnel = validate_evidence(
        load_funnel_evidence(evidence.funnel_evidence, strategy_id=strategy_id),
        batch.funnel.thresholds,
    )
    digest = gate.sha256 or ""
    for path in (evidence.performance, evidence.funnel_evidence):
        file_digest = hashlib.sha256(path.read_bytes()).hexdigest()
        digest = hashlib.sha256((digest + file_digest).encode("ascii")).hexdigest()
    metrics = compute_window_metrics(replace(record, trials=stats))
    return replace(gate, backtest=metrics, sha256=digest), funnel


def judge_candidate(
    strategy_id: str,
    split: Split,
    evidence: CandidateEvidence,
    batch: ResearchBatch,
    trials: TrialSummary,
) -> GateResult:
    """Judge a batch candidate on ``split`` against the thresholds its batch signed.

    Args:
        strategy_id: The candidate.
        split: ``train`` for a Train-only run, ``validation`` for Train + Validation.
        evidence: The candidate's three files (contract §2).
        batch: The signed batch that names the candidate.
        trials: The trial summary of the whole log (ADR-026 §4).

    Returns:
        One check per gate row, ``validation_vs_train`` NA on ``train``.

    Raises:
        ValueError: The batch does not name the candidate, its section does
            not keep v1's §9 secondary thresholds, the evidence does not match
            ``split``, or a file is not valid evidence (``EvidenceError`` and
            ``FunnelEvidenceError`` are ``ValueError``).
        OSError: A file cannot be read.
    """
    candidate = batch.candidate(strategy_id)
    if candidate is None:
        raise ValueError(f"batch {batch.batch_id} does not name {strategy_id!r}")
    require_v1_secondary(batch.section_text(candidate))
    gate_evidence, funnel = candidate_gate_evidence(strategy_id, evidence, batch, trials)
    if evidence_split(funnel, batch.funnel.thresholds) != split:
        raise ValueError(
            f"{strategy_id}: the evidence ends {funnel.end_date}, which is not a {split} run "
            f"(train_end {batch.funnel.thresholds.train_end})"
        )
    report = evaluate_signed_gate(
        CertificationRecord(strategy_id),
        gate_evidence,
        now=_UNTRUSTED_GATE_TIME,
        v1=PreRegistration(),
        dsr_min=batch.funnel.dsr_min,
        thresholds=batch.funnel.thresholds,
        funnel=funnel,
        refusal="",
    )
    checks = tuple(check_result(row, split) for row in report.checks)
    return GateResult(
        strategy_id=strategy_id,
        split=split,
        checks=checks,
        dsr=gate_evidence.backtest.dsr if gate_evidence.backtest else None,
        passed=all(c.status != "FAIL" for c in checks),
    )


def check_result(row: GateCheck, split: Split) -> CheckResult:
    """One gate row as PASS, FAIL or, for a check a Train run cannot judge, NA."""
    if split == "train" and row.name in NOT_APPLICABLE_ON_TRAIN:
        return CheckResult(row.name, "NA", "not applicable: judged on Train only")
    return CheckResult(
        row.name,
        "PASS" if row.passed else "FAIL",
        f"observed {row.observed:<22} required {row.requirement}",
    )
