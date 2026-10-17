"""Append-only, hash-chained JSONL decision journal.

One JSON object per line: ``{"kind", "data", "prev", "hash"}``, where
``hash = sha256(prev + canonical_json(kind, data))`` and ``prev`` is the
previous line's hash (64 zeros for the first). Editing, reordering or
deleting a line breaks the chain, and :meth:`JsonlDecisionJournal.entries`
raises instead of returning a doctored history.

Reference: issue #363; openspec/changes/363-decision-journal D-2.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any

from hermes.domain.journal.entries import Decision, OutcomeKind, Review, Score
from hermes.ports.decision_journal import JournalIntegrityError

__all__ = ["GENESIS_HASH", "JsonlDecisionJournal"]

GENESIS_HASH = "0" * 64


def _chain_hash(prev: str, kind: str, data: dict[str, Any]) -> str:
    canonical = json.dumps({"kind": kind, "data": data}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256((prev + canonical).encode("utf-8")).hexdigest()


def _encode(entry: Decision | Review) -> tuple[str, dict[str, Any]]:
    if isinstance(entry, Decision):
        return "decision", {
            "entry_id": entry.entry_id,
            "recorded_at": entry.recorded_at.isoformat(),
            "decision": entry.decision,
            "options": list(entry.options),
            "chosen": entry.chosen,
            "reasoning": entry.reasoning,
            "expected_outcome": entry.expected_outcome,
            "review_on": entry.review_on.isoformat(),
            "outcome_kind": entry.outcome_kind.value,
            "corrects": entry.corrects,
        }
    return "review", {
        "entry_id": entry.entry_id,
        "recorded_at": entry.recorded_at.isoformat(),
        "decision_id": entry.decision_id,
        "actual_outcome": entry.actual_outcome,
        "score": entry.score.value,
        "evidence": list(entry.evidence),
    }


def _decode(kind: str, data: dict[str, Any]) -> Decision | Review:
    if kind == "decision":
        return Decision(
            entry_id=data["entry_id"],
            recorded_at=datetime.fromisoformat(data["recorded_at"]),
            decision=data["decision"],
            options=tuple(data["options"]),
            chosen=data["chosen"],
            reasoning=data["reasoning"],
            expected_outcome=data["expected_outcome"],
            review_on=date.fromisoformat(data["review_on"]),
            outcome_kind=OutcomeKind(data["outcome_kind"]),
            corrects=data["corrects"],
        )
    if kind == "review":
        return Review(
            entry_id=data["entry_id"],
            recorded_at=datetime.fromisoformat(data["recorded_at"]),
            decision_id=data["decision_id"],
            actual_outcome=data["actual_outcome"],
            score=Score(data["score"]),
            evidence=tuple(data["evidence"]),
        )
    raise ValueError(f"unknown entry kind {kind!r}")


class JsonlDecisionJournal:
    """:class:`~hermes.ports.decision_journal.DecisionJournalPort` over one JSONL file.

    Args:
        path: The journal file. Created on first append.
    """

    def __init__(self, path: Path) -> None:
        self._path = path

    def append(self, entry: Decision | Review) -> None:
        """Verify the chain, then append ``entry`` linked to the last hash.

        Raises:
            JournalIntegrityError: The existing file fails verification.
        """
        prev = self._verified_tail()
        kind, data = _encode(entry)
        line = {"kind": kind, "data": data, "prev": prev, "hash": _chain_hash(prev, kind, data)}
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(line, sort_keys=True) + "\n")

    def entries(self) -> Sequence[Decision | Review]:
        """Return every entry, verifying the whole chain first.

        Raises:
            JournalIntegrityError: A line is malformed, edited, reordered or
                missing.
        """
        return [entry for entry, _ in self._read()]

    def _verified_tail(self) -> str:
        records = self._read()
        return records[-1][1] if records else GENESIS_HASH

    def _read(self) -> list[tuple[Decision | Review, str]]:
        if not self._path.exists():
            return []
        try:
            text = self._path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise JournalIntegrityError(f"{self._path}: not UTF-8 text: {exc}") from exc
        out: list[tuple[Decision | Review, str]] = []
        prev = GENESIS_HASH
        for number, raw in enumerate(text.splitlines(), 1):
            try:
                line = json.loads(raw)
                kind, data, line_prev, line_hash = (
                    line["kind"],
                    line["data"],
                    line["prev"],
                    line["hash"],
                )
                entry = _decode(kind, data)
            except (ValueError, KeyError, TypeError, AttributeError, RecursionError) as exc:
                raise JournalIntegrityError(
                    f"{self._path}:{number}: malformed entry: {exc}"
                ) from exc
            if line_prev != prev or line_hash != _chain_hash(prev, kind, data):
                raise JournalIntegrityError(
                    f"{self._path}:{number}: hash chain broken; the journal was edited"
                )
            out.append((entry, line_hash))
            prev = line_hash
        return out
