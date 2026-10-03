"""Hash-chained JSON Lines file for the trial log.

One trial per line. Each line carries ``prev_sha256`` (the previous line's
``sha256``, or 64 zeros for the first) and ``sha256``, the SHA-256 of the
record's canonical JSON concatenated with ``prev_sha256``. Reading verifies the
whole chain, so an edited, removed or reordered line is detected instead of
silently lowering the trial count. The file is only ever opened for append.

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

from hermes.domain.calibration.trial_record import DatasetRef, TrialRecord
from hermes.domain.errors import TrialLogError
from hermes.domain.market_data.dataset_manifest import DataSplit

__all__ = ["GENESIS_SHA256", "JsonlTrialLog"]

GENESIS_SHA256 = "0" * 64


@dataclass(frozen=True, slots=True)
class JsonlTrialLog:
    """Append-only trial log in a JSON Lines file.

    Args:
        path: Log file path. Created with its parent directory on first append.
    """

    path: Path

    def append(self, record: TrialRecord) -> None:
        """Verify the chain, then append ``record`` and fsync.

        Raises:
            TrialLogError: The existing chain is broken or the write fails.
        """
        prev = self._verified_lines()[-1]["sha256"] if self.path.exists() else GENESIS_SHA256
        body = _serialise(record)
        line = {**body, "prev_sha256": prev, "sha256": _chain_hash(body, prev)}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8", newline="\n") as fh:
                fh.write(json.dumps(line, sort_keys=True) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
        except OSError as exc:
            raise TrialLogError(path=str(self.path), msg=f"append failed: {exc}") from exc

    def records(self) -> tuple[TrialRecord, ...]:
        """Return every record after verifying the chain; empty when the file is absent.

        Raises:
            TrialLogError: The chain is broken or a line is malformed.
        """
        if not self.path.exists():
            return ()
        records = []
        for number, line in enumerate(self._verified_lines(), start=1):
            try:
                records.append(_parse(line))
            except (KeyError, TypeError, ValueError) as exc:
                raise TrialLogError(path=str(self.path), msg=f"line {number}: {exc}") from exc
        return tuple(records)

    def _verified_lines(self) -> list[dict[str, Any]]:
        try:
            text = self.path.read_text(encoding="utf-8")
        except OSError as exc:
            raise TrialLogError(path=str(self.path), msg=f"read failed: {exc}") from exc
        lines: list[dict[str, Any]] = []
        prev = GENESIS_SHA256
        for number, raw in enumerate(text.splitlines(), start=1):
            try:
                line = json.loads(raw)
                body = {k: v for k, v in line.items() if k not in {"prev_sha256", "sha256"}}
                claimed_prev, claimed = line["prev_sha256"], line["sha256"]
            except (json.JSONDecodeError, KeyError, AttributeError) as exc:
                raise TrialLogError(
                    path=str(self.path), msg=f"line {number} is malformed: {exc}"
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
