"""Unit tests for the decision journal service and its JSONL store (#363)."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from hermes.adapters.journal.jsonl_store import JsonlDecisionJournal
from hermes.application.journal.service import JournalError, due, record_decision, record_review
from hermes.domain.journal.entries import Decision, OutcomeKind, Score
from hermes.infrastructure.file_lock import FileLock
from hermes.ports.decision_journal import DecisionJournalPort, JournalIntegrityError

_DAY = datetime(2026, 10, 3, 9, 0, tzinfo=UTC)
_LATER = datetime(2026, 11, 3, 9, 0, tzinfo=UTC)


def _decision(
    entry_id: str = "d1",
    *,
    kind: OutcomeKind = OutcomeKind.PROCESS,
    review_on: date = date(2026, 11, 1),
    corrects: str | None = None,
) -> Decision:
    return Decision(
        entry_id=entry_id,
        recorded_at=_DAY,
        decision="Slim the pre-push hook",
        options=("keep full preflight", "slim pre-push"),
        chosen="slim pre-push",
        reasoning="CI runs the slow checks on every head",
        expected_outcome="pushes under a minute, no extra red CI",
        review_on=review_on,
        outcome_kind=kind,
        corrects=corrects,
    )


@pytest.fixture
def journal(tmp_path: Path) -> JsonlDecisionJournal:
    """Return an empty journal in a temp file."""
    return JsonlDecisionJournal(tmp_path / "journal.jsonl")


def test_store_satisfies_the_port(journal: JsonlDecisionJournal) -> None:
    """The JSONL store is a structural DecisionJournalPort."""
    assert isinstance(journal, DecisionJournalPort)


def test_round_trip_keeps_the_decision(journal: JsonlDecisionJournal) -> None:
    """A recorded decision reads back equal."""
    record_decision(journal, _decision())
    assert list(journal.entries()) == [_decision()]


# SEV-1: append-only audit trail; an edit must never pass as history
def test_editing_a_line_breaks_the_chain(journal: JsonlDecisionJournal, tmp_path: Path) -> None:
    """Changing a stored field is detected and reading fails."""
    record_decision(journal, _decision())
    record_decision(journal, _decision("d2"))
    path = tmp_path / "journal.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    first = json.loads(lines[0])
    first["data"]["chosen"] = "keep full preflight"
    path.write_text(json.dumps(first) + "\n" + lines[1] + "\n", encoding="utf-8")
    with pytest.raises(JournalIntegrityError, match="chain"):
        journal.entries()


def test_deleting_a_line_breaks_the_chain(journal: JsonlDecisionJournal, tmp_path: Path) -> None:
    """Removing the first entry leaves the second pointing at a missing hash."""
    record_decision(journal, _decision())
    record_decision(journal, _decision("d2"))
    path = tmp_path / "journal.jsonl"
    path.write_text(path.read_text(encoding="utf-8").splitlines()[1] + "\n", encoding="utf-8")
    with pytest.raises(JournalIntegrityError, match="chain"):
        journal.entries()


def test_correction_is_a_new_entry(journal: JsonlDecisionJournal) -> None:
    """A correction names the original, and both stay in the journal."""
    record_decision(journal, _decision())
    record_decision(journal, _decision("d2", corrects="d1"))
    assert [e.entry_id for e in journal.entries()] == ["d1", "d2"]


def test_correcting_an_unknown_decision_is_refused(journal: JsonlDecisionJournal) -> None:
    """A correction must point at a recorded decision."""
    with pytest.raises(JournalError, match="not a recorded decision"):
        record_decision(journal, _decision(corrects="nope"))


def test_duplicate_id_is_refused(journal: JsonlDecisionJournal) -> None:
    """Entry ids are unique."""
    record_decision(journal, _decision())
    with pytest.raises(JournalError, match="already exists"):
        record_decision(journal, _decision())


def test_review_before_its_date_is_refused(journal: JsonlDecisionJournal) -> None:
    """A decision is scored on or after its review date, not before."""
    record_decision(journal, _decision())
    with pytest.raises(JournalError, match="due for review on 2026-11-01"):
        record_review(
            journal,
            entry_id="r1",
            decision_id="d1",
            actual_outcome="pushes take a minute",
            score=Score.HIT,
            evidence=("https://github.com/ricardoafo/hermes/pull/418",),
            now=_DAY,
            certified=False,
        )


def test_due_process_decision_is_reviewed_once(journal: JsonlDecisionJournal) -> None:
    """After its date a process decision is scored, and a second review is refused."""
    record_decision(journal, _decision())
    review = record_review(
        journal,
        entry_id="r1",
        decision_id="d1",
        actual_outcome="pushes take a minute",
        score=Score.HIT,
        evidence=("https://github.com/ricardoafo/hermes/pull/418",),
        now=_LATER,
        certified=False,
    )
    assert (review.score, review.decision_id) == (Score.HIT, "d1")
    with pytest.raises(JournalError, match="already reviewed"):
        record_review(
            journal,
            entry_id="r2",
            decision_id="d1",
            actual_outcome="again",
            score=Score.MISS,
            evidence=("x",),
            now=_LATER,
            certified=False,
        )


# SEV-1: ruling 1 — no review may reveal shadow P&L before certification
def test_strategy_decision_is_sealed_until_certification(journal: JsonlDecisionJournal) -> None:
    """A strategy decision cannot be scored before certification, and is due-but-sealed."""
    record_decision(journal, _decision(kind=OutcomeKind.STRATEGY))
    with pytest.raises(JournalError, match="sealed until certification"):
        record_review(
            journal,
            entry_id="r1",
            decision_id="d1",
            actual_outcome="shadow results",
            score=Score.HIT,
            evidence=("x",),
            now=_LATER,
            certified=False,
        )
    report = due(journal, today=_LATER.date(), certified=False)
    assert ([d.entry_id for d in report.sealed], report.reviewable) == (["d1"], ())


def test_strategy_decision_opens_after_certification(journal: JsonlDecisionJournal) -> None:
    """Once certified, the strategy decision is reviewable."""
    record_decision(journal, _decision(kind=OutcomeKind.STRATEGY))
    report = due(journal, today=_LATER.date(), certified=True)
    assert ([d.entry_id for d in report.reviewable], report.sealed) == (["d1"], ())


def test_due_skips_future_and_reviewed(journal: JsonlDecisionJournal) -> None:
    """Only unreviewed decisions at or past their date are due."""
    record_decision(journal, _decision())
    record_decision(journal, _decision("d2", review_on=date(2027, 1, 1)))
    report = due(journal, today=_LATER.date(), certified=False)
    assert [d.entry_id for d in report.reviewable] == ["d1"]


@pytest.mark.parametrize(
    ("build", "match"),
    [
        (lambda: replace(_decision(), chosen="something else"), "not one of the options"),
        (lambda: replace(_decision(), options=()), "at least one"),
        (lambda: replace(_decision(), reasoning="  "), "reasoning"),
        (lambda: replace(_decision(), review_on=date(2026, 1, 1)), "before the decision"),
        (lambda: replace(_decision(), recorded_at=_DAY.replace(tzinfo=None)), "timezone-aware"),
    ],
)
def test_invalid_decisions_are_rejected(build: Callable[[], Decision], match: str) -> None:
    """Every field the journal needs to score later must be present and consistent."""
    with pytest.raises(ValueError, match=match):
        build()


# -- concurrent appenders (issue #855) ----------------------------------------


def test_an_append_waits_while_another_appender_holds_the_lock(
    journal: JsonlDecisionJournal, tmp_path: Path
) -> None:
    """A journal append writes nothing while another appender holds the lock.

    Without the lock both appenders link to the same tail hash and the
    journal forks: every later read fails as an edited history.
    """
    record_decision(journal, _decision())
    other = FileLock(journal.lock_path)
    assert other.try_acquire()
    waiting = threading.Thread(target=journal.append, args=(_decision("d2"),))
    waiting.start()
    waiting.join(timeout=0.3)
    lines_while_held = len((tmp_path / "journal.jsonl").read_text(encoding="utf-8").splitlines())
    other.release()
    waiting.join(timeout=10)
    assert (lines_while_held, [e.entry_id for e in journal.entries()]) == (1, ["d1", "d2"])


def test_concurrent_journal_appenders_leave_one_unbroken_chain(
    journal: JsonlDecisionJournal,
) -> None:
    """Four appenders racing 25 appends each leave 100 entries on one verified chain."""

    def appender(worker: int) -> None:
        for i in range(25):
            journal.append(_decision(f"w{worker}-{i}"))

    workers = [threading.Thread(target=appender, args=(w,)) for w in range(4)]
    for thread in workers:
        thread.start()
    for thread in workers:
        thread.join()
    expected = sorted(f"w{w}-{i}" for w in range(4) for i in range(25))
    assert sorted(e.entry_id for e in journal.entries()) == expected
