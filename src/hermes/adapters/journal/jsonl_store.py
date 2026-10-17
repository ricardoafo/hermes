"""Append-only, hash-chained JSONL decision journal.

One JSON object per line: ``{"kind", "data", "prev", "hash"}``, where
``hash = sha256(prev + canonical_json(kind, data))`` and ``prev`` is the
previous line's hash (64 zeros for the first). Editing, reordering or
deleting a line breaks the chain, and :meth:`JsonlDecisionJournal.entries`
raises instead of returning a doctored history. Each read-verify-append runs
under an exclusive lock on ``<path>.lock``, so two appenders cannot link to the
same tail and fork the chain (issue #855).

A torn last line left by a power loss blocks every read and append until
:meth:`JsonlDecisionJournal.repair` drops it (issue #891). The drop is itself a
chain line of kind ``repair``, so the journal keeps the record that a line went
missing; :meth:`JsonlDecisionJournal.entries` does not return it.

Reference: issue #363; openspec/changes/363-decision-journal D-2.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any

from hermes.adapters.jsonl_tail import TornLine, replace_torn_line, torn_line
from hermes.domain.journal.entries import Decision, OutcomeKind, Review, Score
from hermes.infrastructure.file_lock import locked
from hermes.ports.decision_journal import JournalIntegrityError

__all__ = ["GENESIS_HASH", "REPAIR_KIND", "JsonlDecisionJournal"]

GENESIS_HASH = "0" * 64
REPAIR_KIND = "repair"

APPEND_LOCK_TIMEOUT_SECONDS = 10.0
"""How long an append waits for another one; an append takes milliseconds."""


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

    @property
    def lock_path(self) -> Path:
        """The lock file every append holds while it reads, verifies and writes."""
        return self._path.with_name(self._path.name + ".lock")

    def append(self, entry: Decision | Review) -> None:
        """Verify the chain, then append ``entry`` linked to the last hash, under the lock.

        Raises:
            JournalIntegrityError: The existing file fails verification.
            OSError: The write fails, or another append held the lock for
                :data:`APPEND_LOCK_TIMEOUT_SECONDS` (a :class:`TimeoutError`).
        """
        with locked(self.lock_path, timeout=APPEND_LOCK_TIMEOUT_SECONDS):
            prev = self._verified_tail()
            kind, data = _encode(entry)
            line = {
                "kind": kind,
                "data": data,
                "prev": prev,
                "hash": _chain_hash(prev, kind, data),
            }
            with self._path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(line, sort_keys=True) + "\n")

    def entries(self) -> Sequence[Decision | Review]:
        """Return every entry, verifying the whole chain first.

        Raises:
            JournalIntegrityError: A line is malformed, edited, reordered or
                missing.
        """
        return [entry for entry, _ in self._read() if entry is not None]

    def torn_line(self) -> TornLine | None:
        """Return the torn last line, or ``None`` when there is none or no file.

        Raises:
            OSError: The file cannot be read.
        """
        return torn_line(self._path) if self._path.exists() else None

    def repair(self, *, repaired_at: datetime) -> TornLine | None:
        """Drop a torn last line and record the drop as a chain line, under the lock.

        Every line before the torn one must verify; a journal broken anywhere
        else is refused, because only an edit breaks a chain in the middle.

        Returns:
            The line dropped, or ``None`` when there was nothing to repair.

        Raises:
            JournalIntegrityError: The lines before the torn one fail verification.
            OSError: The write fails, or another append held the lock for
                :data:`APPEND_LOCK_TIMEOUT_SECONDS` (a :class:`TimeoutError`).
        """
        with locked(self.lock_path, timeout=APPEND_LOCK_TIMEOUT_SECONDS):
            torn = self.torn_line()
            if torn is None:
                return None
            kept = self._path.read_bytes()[: torn.offset].decode("utf-8")
            records = self._read(kept)
            prev = records[-1][1] if records else GENESIS_HASH
            data = {
                "repaired_at": repaired_at.isoformat(),
                "dropped_line": torn.number,
                "dropped_bytes": torn.size,
                "dropped_sha256": torn.sha256,
            }
            line = {
                "kind": REPAIR_KIND,
                "data": data,
                "prev": prev,
                "hash": _chain_hash(prev, REPAIR_KIND, data),
            }
            replace_torn_line(self._path, torn, json.dumps(line, sort_keys=True))
            return torn

    def _verified_tail(self) -> str:
        records = self._read()
        return records[-1][1] if records else GENESIS_HASH

    def _read(self, text: str | None = None) -> list[tuple[Decision | Review | None, str]]:
        if text is None:
            if not self._path.exists():
                return []
            try:
                text = self._path.read_text(encoding="utf-8")
            except UnicodeDecodeError as exc:
                raise JournalIntegrityError(f"{self._path}: not UTF-8 text: {exc}") from exc
        out: list[tuple[Decision | Review | None, str]] = []
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
                entry = None if kind == REPAIR_KIND else _decode(kind, data)
            except (ValueError, KeyError, TypeError, AttributeError, RecursionError) as exc:
                raise JournalIntegrityError(
                    f"{self._path}:{number}: malformed entry: {exc}; if a power loss cut "
                    "the last append, `hermes journal repair` drops it"
                ) from exc
            if line_prev != prev or line_hash != _chain_hash(prev, kind, data):
                raise JournalIntegrityError(
                    f"{self._path}:{number}: hash chain broken; the journal was edited"
                )
            out.append((entry, line_hash))
            prev = line_hash
        return out
