"""JSON-file certification store: one ``<strategy_id>.json`` per strategy.

Writes go to a temporary file in the same directory and replace the target, so
a crash mid-write leaves the previous record intact.

Reference: openspec/archive/355-certification-gates/change.md D-7.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from hermes.domain.calibration.certification import (
    CertificationRecord,
    CertificationState,
    Transition,
)
from hermes.ports.certification import CertificationStoreError

__all__ = ["JsonCertificationStore"]

_SCHEMA_VERSION = 1


class JsonCertificationStore:
    """:class:`~hermes.ports.certification.CertificationStorePort` over a directory.

    Args:
        directory: Where the record files live. Created on first save.
    """

    def __init__(self, directory: Path) -> None:
        self._directory = directory

    def path_for(self, strategy_id: str) -> Path:
        """Return the file that holds ``strategy_id``'s record."""
        return self._directory / f"{strategy_id}.json"

    def load(self, strategy_id: str) -> CertificationRecord | None:
        """Return the stored record, or ``None`` when no file exists.

        Raises:
            CertificationStoreError: The file is not a valid record.
        """
        path = self.path_for(strategy_id)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            record = _from_payload(payload)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise CertificationStoreError(f"unreadable certification record {path}: {exc}") from exc
        if record.strategy_id != strategy_id:
            raise CertificationStoreError(
                f"{path} holds strategy {record.strategy_id!r}, expected {strategy_id!r}"
            )
        return record

    def save(self, record: CertificationRecord) -> None:
        """Atomically replace the record file for ``record.strategy_id``.

        Raises:
            CertificationStoreError: The file could not be written.
        """
        target = self.path_for(record.strategy_id)
        tmp: Path | None = None
        try:
            self._directory.mkdir(parents=True, exist_ok=True)
            fd, name = tempfile.mkstemp(dir=self._directory, suffix=".tmp")
            tmp = Path(name)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(_to_payload(record), handle, indent=2, sort_keys=True)
                handle.write("\n")
            tmp.replace(target)
        except OSError as exc:
            if tmp is not None:
                tmp.unlink(missing_ok=True)
            raise CertificationStoreError(f"could not write {target}: {exc}") from exc


def _dt(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _parse_dt(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp {value!r} has no timezone")
    return parsed


def _to_payload(record: CertificationRecord) -> dict[str, Any]:
    return {
        "schema_version": _SCHEMA_VERSION,
        "strategy_id": record.strategy_id,
        "state": record.state.value,
        "parameter_fingerprint": record.parameter_fingerprint,
        "oos_evaluated_at": _dt(record.oos_evaluated_at),
        "transitions": [
            {
                "from": t.from_state.value,
                "to": t.to_state.value,
                "at": _dt(t.at),
                "reason": t.reason,
                "evidence_sha256": t.evidence_sha256,
            }
            for t in record.transitions
        ],
    }


def _from_payload(payload: dict[str, Any]) -> CertificationRecord:
    if payload["schema_version"] != _SCHEMA_VERSION:
        raise ValueError(f"unsupported schema_version {payload['schema_version']!r}")
    transitions = []
    for item in payload["transitions"]:
        at = _parse_dt(item["at"])
        if at is None:
            raise ValueError("transition without a timestamp")
        transitions.append(
            Transition(
                from_state=CertificationState(item["from"]),
                to_state=CertificationState(item["to"]),
                at=at,
                reason=str(item["reason"]),
                evidence_sha256=item["evidence_sha256"],
            )
        )
    return CertificationRecord(
        strategy_id=str(payload["strategy_id"]),
        state=CertificationState(payload["state"]),
        parameter_fingerprint=payload["parameter_fingerprint"],
        oos_evaluated_at=_parse_dt(payload["oos_evaluated_at"]),
        transitions=tuple(transitions),
    )
