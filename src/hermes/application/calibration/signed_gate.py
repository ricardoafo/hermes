"""The generic backtest gate's interface: one gate for any signed batch candidate.

ADR-026 §1 and §4: every candidate in a batch is judged by the same gate, with
thresholds read from the signed batch, so a new strategy needs no gate code.
This module fixes the types the gate returns and the call the batch judge
(issue #1059) makes; the gate itself lands with issue #1058.

Reference: ADR-026 §1, §4; research-funnel interface contract §3.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from hermes.application.calibration.research_batch import ResearchBatch
from hermes.domain.calibration.trial_record import TrialSummary

__all__ = [
    "CandidateEvidence",
    "CandidateJudge",
    "CheckResult",
    "CheckStatus",
    "GateResult",
    "Split",
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
