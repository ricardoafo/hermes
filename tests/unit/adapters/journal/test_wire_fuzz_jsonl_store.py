"""Fuzz property for the decision journal reader (issue #842).

For any file content, ``JsonlDecisionJournal.entries`` returns entries or
raises ``JournalIntegrityError``, nothing else. Each mutated entry is
re-chained with a valid hash, so the entry decoder is reached instead of the
chain check. Seeds are a decision and a review encoded by the store itself.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Final

from hypothesis import example, given
from hypothesis import strategies as st

from hermes.adapters.journal.jsonl_store import (
    GENESIS_HASH,
    JsonlDecisionJournal,
    _chain_hash,
    _encode,
)
from hermes.domain.journal.entries import Decision, OutcomeKind, Review, Score
from hermes.ports.decision_journal import JournalIntegrityError
from tests.unit.adapters.wire_fuzz import FUZZ_SLOW, foreign_error, mutated

_AT: Final = datetime(2026, 10, 3, 14, 0, tzinfo=UTC)
_DECISION: Final = _encode(
    Decision(
        entry_id="d-1",
        recorded_at=_AT,
        decision="hold the fuzz budget",
        options=("hold", "raise"),
        chosen="hold",
        reasoning="the unit tier must stay fast",
        expected_outcome="under ten seconds",
        review_on=date(2026, 11, 1),
        outcome_kind=OutcomeKind.PROCESS,
    )
)
_REVIEW: Final = _encode(
    Review(
        entry_id="r-1",
        recorded_at=_AT,
        decision_id="d-1",
        actual_outcome="it stayed fast",
        score=Score.HIT,
        evidence=("https://example.invalid/run/1",),
    )
)
_SEED: Final = {"decision": _DECISION[1], "review": _REVIEW[1]}


def _chained(kind: object, data: object) -> str:
    if not isinstance(kind, str) or not isinstance(data, dict):
        return json.dumps({"kind": kind, "data": data, "prev": GENESIS_HASH, "hash": ""}) + "\n"
    line = {
        "kind": kind,
        "data": data,
        "prev": GENESIS_HASH,
        "hash": _chain_hash(GENESIS_HASH, kind, data),
    }
    return json.dumps(line, sort_keys=True) + "\n"


@FUZZ_SLOW
@given(kind=st.sampled_from(sorted(_SEED)), data=st.data())
@example(kind="decision", data=None)
def test_a_rechained_journal_entry_reads_or_raises_integrity_error(
    kind: str, data: st.DataObject | None, tmp_path: Path
) -> None:
    """Invariant: a validly chained line reads as an entry or raises JournalIntegrityError."""
    seed = _SEED[kind]
    body = {**seed, "options": [1, 2], "chosen": 1} if data is None else data.draw(mutated(seed))
    path = tmp_path / "journal.jsonl"
    path.write_text(_chained(kind, body), encoding="utf-8")
    assert foreign_error(JsonlDecisionJournal(path).entries, JournalIntegrityError) == ""


@FUZZ_SLOW
@given(raw=st.one_of(st.binary(max_size=24), st.just(b"\xff\xfe\n"), st.just(b"[" * 5000)))
@example(raw=b"\xff\xfe\n")
@example(raw=b"[" * 5000)
def test_a_journal_that_is_not_json_lines_raises_integrity_error(
    raw: bytes, tmp_path: Path
) -> None:
    """Invariant: bytes that are not UTF-8 JSON lines make entries() raise JournalIntegrityError."""
    path = tmp_path / "journal.jsonl"
    path.write_bytes(raw)
    assert foreign_error(JsonlDecisionJournal(path).entries, JournalIntegrityError) == ""
