"""Unit tests for the decision journal service and its JSONL store (#363)."""

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
from hermes.ports.decision_journal import JournalIntegrityError

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


# -- a torn last line (issue #891) ---------------------------------------------

_REPAIRED_AT = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)


def _tear(journal: JsonlDecisionJournal) -> bytes:
    """Cut the next append halfway, as a power loss would, and return the bytes left."""
    torn = b'{"data": {"entry_id": "d9", "decision": "Cut sh'
    with journal.lock_path.with_suffix("").open("ab") as fh:
        fh.write(torn)
    return torn


def test_a_torn_last_line_blocks_the_next_decision(journal: JsonlDecisionJournal) -> None:
    """Without a repair nothing more can be recorded, and the error names the fix."""
    record_decision(journal, _decision())
    _tear(journal)
    with pytest.raises(JournalIntegrityError, match=r":2: malformed.*hermes journal repair"):
        record_decision(journal, _decision("d2"))


def test_a_repair_keeps_every_entry_and_lets_the_next_one_in(
    journal: JsonlDecisionJournal,
) -> None:
    """The whole decision survives the repair, and the next decision lands after it."""
    record_decision(journal, _decision())
    _tear(journal)
    journal.repair(repaired_at=_REPAIRED_AT)
    record_decision(journal, _decision("d2"))
    assert [e.entry_id for e in journal.entries()] == ["d1", "d2"]


def test_a_repair_records_the_drop_as_a_chain_line(
    journal: JsonlDecisionJournal, tmp_path: Path
) -> None:
    """The journal keeps a repair line naming the dropped bytes by their hash."""
    record_decision(journal, _decision())
    torn = _tear(journal)
    dropped = journal.repair(repaired_at=_REPAIRED_AT)
    lines = [json.loads(raw) for raw in (tmp_path / "journal.jsonl").read_text().splitlines()]
    assert dropped is not None
    assert (lines[-1]["kind"], lines[-1]["data"], lines[-1]["prev"]) == (
        "repair",
        {
            "repaired_at": "2026-10-17T12:00:00+00:00",
            "dropped_line": 2,
            "dropped_bytes": len(torn),
            "dropped_sha256": dropped.sha256,
        },
        lines[0]["hash"],
    )


def test_a_repair_refuses_a_journal_edited_before_the_torn_line(
    journal: JsonlDecisionJournal, tmp_path: Path
) -> None:
    """An edit breaks the chain in the middle; a repair never hides it."""
    record_decision(journal, _decision())
    path = tmp_path / "journal.jsonl"
    path.write_text(path.read_text().replace("Slim the", "Grow the"))
    _tear(journal)
    before = path.read_bytes()
    with pytest.raises(JournalIntegrityError, match="hash chain broken"):
        journal.repair(repaired_at=_REPAIRED_AT)
    assert path.read_bytes() == before


def test_a_journal_repair_with_nothing_torn_changes_nothing(
    journal: JsonlDecisionJournal, tmp_path: Path
) -> None:
    """A whole journal is left byte for byte as it was."""
    record_decision(journal, _decision())
    before = (tmp_path / "journal.jsonl").read_bytes()
    assert (
        journal.repair(repaired_at=_REPAIRED_AT),
        (tmp_path / "journal.jsonl").read_bytes(),
    ) == (
        None,
        before,
    )
