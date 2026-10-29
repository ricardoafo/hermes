"""Decision journal service: record decisions, list what is due, record reviews.

Rules:

- Append-only: corrections are new decisions naming the one they correct.
- A decision is reviewed once, on or after its review date.
- A ``STRATEGY`` decision stays sealed until the strategy is certified. Its
  review is refused, and :func:`due` lists it as sealed without scoring it,
  so no review can reveal shadow P&L early (ruling 1, ADR-012 D-06).

Reference: issue #363; openspec/archive/363-decision-journal.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime

from hermes.domain.journal.entries import Decision, OutcomeKind, Review, Score
from hermes.ports.decision_journal import DecisionJournalPort

__all__ = ["DueReport", "JournalError", "due", "record_decision", "record_review"]


class JournalError(ValueError):
    """A journal operation broke one of the rules above."""


@dataclass(frozen=True, slots=True)
class DueReport:
    """Decisions whose review date has passed and that have no review yet."""

    reviewable: tuple[Decision, ...]
    sealed: tuple[Decision, ...]
    """Strategy decisions held until certification."""


def _decisions(entries: Sequence[Decision | Review]) -> dict[str, Decision]:
    return {e.entry_id: e for e in entries if isinstance(e, Decision)}


def _reviewed_ids(entries: Sequence[Decision | Review]) -> set[str]:
    return {e.decision_id for e in entries if isinstance(e, Review)}


def record_decision(journal: DecisionJournalPort, decision: Decision) -> None:
    """Append ``decision``.

    Raises:
        JournalError: The id is taken, or ``corrects`` names no decision.
    """
    entries = journal.entries()
    known = _decisions(entries)
    if decision.entry_id in {e.entry_id for e in entries}:
        raise JournalError(f"entry id {decision.entry_id} already exists")
    if decision.corrects is not None and decision.corrects not in known:
        raise JournalError(f"corrects {decision.corrects}, which is not a recorded decision")
    journal.append(decision)


def record_review(
    journal: DecisionJournalPort,
    *,
    entry_id: str,
    decision_id: str,
    actual_outcome: str,
    score: Score,
    evidence: tuple[str, ...],
    now: datetime,
    certified: bool,
) -> Review:
    """Score one decision and append the review.

    Args:
        journal: The journal.
        entry_id: Id for the review entry.
        decision_id: The decision being scored.
        actual_outcome: What happened.
        score: Hit, partial or miss against the expected outcome.
        evidence: Links that back the outcome.
        now: Current UTC time.
        certified: Whether the strategy has passed OOS certification. Strategy
            decisions cannot be reviewed before it has.

    Returns:
        The appended review.

    Raises:
        JournalError: Unknown decision, already reviewed, not yet due, or a
            sealed strategy decision.
    """
    entries = journal.entries()
    if entry_id in {e.entry_id for e in entries}:
        raise JournalError(f"entry id {entry_id} already exists")
    decision = _decisions(entries).get(decision_id)
    if decision is None:
        raise JournalError(f"no decision {decision_id}")
    if decision_id in _reviewed_ids(entries):
        raise JournalError(f"decision {decision_id} is already reviewed")
    if now.date() < decision.review_on:
        raise JournalError(
            f"decision {decision_id} is due for review on {decision.review_on.isoformat()}"
        )
    if decision.outcome_kind is OutcomeKind.STRATEGY and not certified:
        raise JournalError(
            f"decision {decision_id} depends on strategy results and stays sealed until "
            "certification (ruling 1)"
        )
    review = Review(
        entry_id=entry_id,
        recorded_at=now,
        decision_id=decision_id,
        actual_outcome=actual_outcome,
        score=score,
        evidence=evidence,
    )
    journal.append(review)
    return review


def due(journal: DecisionJournalPort, *, today: date, certified: bool) -> DueReport:
    """Return unreviewed decisions whose review date is today or earlier.

    Args:
        journal: The journal.
        today: The current UTC date.
        certified: Whether the strategy has passed OOS certification.

    Returns:
        Reviewable decisions, and strategy decisions held until certification.
    """
    entries = journal.entries()
    reviewed = _reviewed_ids(entries)
    pending = [
        d
        for d in _decisions(entries).values()
        if d.entry_id not in reviewed and d.review_on <= today
    ]
    sealed = tuple(d for d in pending if d.outcome_kind is OutcomeKind.STRATEGY and not certified)
    reviewable = tuple(d for d in pending if d not in sealed)
    return DueReport(reviewable=reviewable, sealed=sealed)
