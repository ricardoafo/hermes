"""Hash-chained JSON Lines file for the trial log.

One trial per line. Each line carries ``prev_sha256`` (the previous line's
``sha256``, or 64 zeros for the first) and ``sha256``, the SHA-256 of the
record's canonical JSON concatenated with ``prev_sha256``. Reading verifies the
whole chain, so an edited, removed or reordered line is detected instead of
silently lowering the trial count. The file is only ever opened for append.
The read-verify-append runs under an exclusive lock on ``<path>.lock``, so two
runs appending at once cannot both link to the same last line and fork the
chain (issue #855).

A torn last line left by a power loss blocks every read and append until
:meth:`JsonlTrialLog.repair` drops it (issue #891). The drop is itself a chain
line, ``{"kind": "repair", ...}``, so the log keeps the record that a line went
missing; repair lines are not trials and :meth:`JsonlTrialLog.records` skips
them.

Reference: openspec/changes/357-trial-log/change.md D-2.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from hermes.adapters.jsonl_tail import TornLine, replace_torn_line, torn_line
from hermes.domain.calibration.trial_record import DatasetRef, TrialRecord
from hermes.domain.errors import TrialLogError
from hermes.domain.market_data.dataset_manifest import DataSplit
from hermes.infrastructure.file_lock import locked

__all__ = ["GENESIS_SHA256", "REPAIR_KIND", "JsonlTrialLog"]

GENESIS_SHA256 = "0" * 64
REPAIR_KIND = "repair"

APPEND_LOCK_TIMEOUT_SECONDS = 10.0
"""How long an append waits for another one; an append takes milliseconds."""


@dataclass(frozen=True, slots=True)
class JsonlTrialLog:
    """Append-only trial log in a JSON Lines file.

    Args:
        path: Log file path. Created with its parent directory on first append.
    """

    path: Path

    @property
    def lock_path(self) -> Path:
        """The lock file every append holds while it reads, verifies and writes."""
        return self.path.with_name(self.path.name + ".lock")

    def append(self, record: TrialRecord) -> None:
        """Verify the chain, then append ``record`` and fsync, under the append lock.

        Raises:
            TrialLogError: The existing chain is broken, another append held
                the lock for :data:`APPEND_LOCK_TIMEOUT_SECONDS`, or the write
                fails.
        """
        try:
            with locked(self.lock_path, timeout=APPEND_LOCK_TIMEOUT_SECONDS):
                self._append_unlocked(record)
        except OSError as exc:
            raise TrialLogError(path=str(self.path), msg=f"append failed: {exc}") from exc

    def _append_unlocked(self, record: TrialRecord) -> None:
        prev = self._verified_lines()[-1]["sha256"] if self.path.exists() else GENESIS_SHA256
        body = _serialise(record)
        line = {**body, "prev_sha256": prev, "sha256": _chain_hash(body, prev)}
        with self.path.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(line, sort_keys=True) + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    def records(self) -> tuple[TrialRecord, ...]:
        """Return every record after verifying the chain; empty when the file is absent.

        Raises:
            TrialLogError: The chain is broken or a line is malformed.
        """
        if not self.path.exists():
            return ()
        records = []
        for number, line in enumerate(self._verified_lines(), start=1):
            if line.get("kind") == REPAIR_KIND:
                continue
            try:
                records.append(_parse(line))
            except (KeyError, TypeError, ValueError, AttributeError) as exc:
                raise TrialLogError(path=str(self.path), msg=f"line {number}: {exc}") from exc
        return tuple(records)

    def torn_line(self) -> TornLine | None:
        """Return the torn last line, or ``None`` when there is none or no file.

        Raises:
            TrialLogError: The file cannot be read.
        """
        if not self.path.exists():
            return None
        try:
            return torn_line(self.path)
        except OSError as exc:
            raise TrialLogError(path=str(self.path), msg=f"read failed: {exc}") from exc

    def repair(self, *, repaired_at: datetime) -> TornLine | None:
        """Drop a torn last line and record the drop as a chain line, under the append lock.

        Every line before the torn one must verify; a log broken anywhere else
        is refused, because only an edit breaks a chain in the middle.

        Returns:
            The line dropped, or ``None`` when there was nothing to repair.

        Raises:
            TrialLogError: The lines before the torn one fail verification,
                the lock is held by another append, or the write fails.
        """
        try:
            with locked(self.lock_path, timeout=APPEND_LOCK_TIMEOUT_SECONDS):
                torn = self.torn_line()
                if torn is None:
                    return None
                kept = self.path.read_bytes()[: torn.offset].decode("utf-8")
                prev = self._verified_lines(kept)[-1]["sha256"] if kept else GENESIS_SHA256
                body = {
                    "kind": REPAIR_KIND,
                    "repaired_at": repaired_at.isoformat(),
                    "dropped_line": torn.number,
                    "dropped_bytes": torn.size,
                    "dropped_sha256": torn.sha256,
                }
                line = {**body, "prev_sha256": prev, "sha256": _chain_hash(body, prev)}
                replace_torn_line(self.path, torn, json.dumps(line, sort_keys=True))
                return torn
        except OSError as exc:
            raise TrialLogError(path=str(self.path), msg=f"repair failed: {exc}") from exc

    def _verified_lines(self, text: str | None = None) -> list[dict[str, Any]]:
        if text is None:
            try:
                text = self.path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                raise TrialLogError(path=str(self.path), msg=f"read failed: {exc}") from exc
        lines: list[dict[str, Any]] = []
        prev = GENESIS_SHA256
        for number, raw in enumerate(text.splitlines(), start=1):
            try:
                line = json.loads(raw)
                body = {k: v for k, v in line.items() if k not in {"prev_sha256", "sha256"}}
                claimed_prev, claimed = line["prev_sha256"], line["sha256"]
            except (json.JSONDecodeError, KeyError, AttributeError, RecursionError) as exc:
                raise TrialLogError(
                    path=str(self.path),
                    msg=f"line {number} is malformed: {exc}; if a power loss cut the last "
                    "append, `hermes trials repair` drops it",
                ) from exc
            if claimed_prev != prev or claimed != _chain_hash(body, prev):
                raise TrialLogError(
                    path=str(self.path),
                    msg=f"hash chain broken at line {number}; "
                    "a trial was edited, removed or reordered",
                )
            lines.append(line)
            prev = claimed
        if not lines:
            raise TrialLogError(path=str(self.path), msg="file exists but holds no trials")
        return lines


def _chain_hash(body: dict[str, Any], prev: str) -> str:
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256((canonical + prev).encode("utf-8")).hexdigest()


def _serialise(record: TrialRecord) -> dict[str, Any]:
    return {
        "recorded_at": record.recorded_at.isoformat(),
        "strategy_id": record.strategy_id,
        "parameters": dict(record.parameters),
        "split": record.split.value,
        "window_start": record.window_start.isoformat(),
        "window_end": record.window_end.isoformat(),
        "datasets": [
            {"dataset_id": d.dataset_id, "content_sha256": d.content_sha256}
            for d in record.datasets
        ],
        "metrics": dict(record.metrics),
        "sharpe": record.sharpe,
        "git_sha": record.git_sha,
        "git_dirty": record.git_dirty,
    }


def _parse(line: dict[str, Any]) -> TrialRecord:
    return TrialRecord(
        recorded_at=datetime.fromisoformat(line["recorded_at"]),
        strategy_id=line["strategy_id"],
        parameters={str(k): str(v) for k, v in line["parameters"].items()},
        split=DataSplit(line["split"]),
        window_start=datetime.fromisoformat(line["window_start"]),
        window_end=datetime.fromisoformat(line["window_end"]),
        datasets=tuple(DatasetRef(d["dataset_id"], d["content_sha256"]) for d in line["datasets"]),
        metrics={str(k): str(v) for k, v in line["metrics"].items()},
        sharpe=None if line["sharpe"] is None else float(line["sharpe"]),
        git_sha=line["git_sha"],
        git_dirty=bool(line["git_dirty"]),
    )
