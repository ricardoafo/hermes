"""Decision journal entries: a Code Owner decision and its later review.

Entries are immutable and the journal is append-only. A correction is a new
decision that names the one it corrects, never an edit.

A decision whose outcome depends on strategy results (``OutcomeKind.STRATEGY``)
cannot be reviewed until the strategy is certified: scoring it earlier would
reveal shadow P&L (pre-registration ruling 1, ADR-012 D-06).

Reference: issue #363; ADR-012 D-05; openspec/archive/363-decision-journal.
"""

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum

__all__ = ["Decision", "OutcomeKind", "Review", "Score"]


class OutcomeKind(StrEnum):
    """What the expected outcome depends on."""

    PROCESS = "process"
    """Engineering, process or tooling: reviewable when due."""

    STRATEGY = "strategy"
    """Strategy results: sealed until certification (ruling 1)."""


class Score(StrEnum):
    """How the decision turned out against its expected outcome."""

    HIT = "hit"
    PARTIAL = "partial"
    MISS = "miss"


def _require_text(value: str, field: str) -> None:
    if not value.strip():
        raise ValueError(f"{field} must not be empty")


@dataclass(frozen=True, slots=True)
class Decision:
    """One Code Owner decision with what was expected of it."""

    entry_id: str
    recorded_at: datetime
    decision: str
    options: tuple[str, ...]
    chosen: str
    reasoning: str
    expected_outcome: str
    review_on: date
    outcome_kind: OutcomeKind
    corrects: str | None = None

    def __post_init__(self) -> None:
        """Validate the entry.

        Raises:
            ValueError: Empty text, no options, a chosen option that is not
                one of the options, a naive timestamp, or a review date before
                the decision.
        """
        for field in ("entry_id", "decision", "chosen", "reasoning", "expected_outcome"):
            _require_text(getattr(self, field), field)
        if not self.options or any(not o.strip() for o in self.options):
            raise ValueError("options must list at least one non-empty option")
        if self.chosen not in self.options:
            raise ValueError(f"chosen {self.chosen!r} is not one of the options")
        if self.recorded_at.tzinfo is None:
            raise ValueError("recorded_at must be timezone-aware")
        if self.review_on < self.recorded_at.date():
            raise ValueError("review_on must not be before the decision date")


@dataclass(frozen=True, slots=True)
class Review:
    """The scored outcome of one decision."""

    entry_id: str
    recorded_at: datetime
    decision_id: str
    actual_outcome: str
    score: Score
    evidence: tuple[str, ...]

    def __post_init__(self) -> None:
        """Validate the entry.

        Raises:
            ValueError: Empty text, no evidence, or a naive timestamp.
        """
        for field in ("entry_id", "decision_id", "actual_outcome"):
            _require_text(getattr(self, field), field)
        if not self.evidence or any(not e.strip() for e in self.evidence):
            raise ValueError("a review needs at least one evidence link")
        if self.recorded_at.tzinfo is None:
            raise ValueError("recorded_at must be timezone-aware")
