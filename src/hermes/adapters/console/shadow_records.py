"""Read-only file sources for the Shadow test screen (issue #718).

:class:`RunsDirectory` reads the day-0 records ``hermes shadow-start`` writes
(issue #797): one JSON file per counted run, ``<strategy>-<UTC stamp>.json`` in
the ``runs`` directory beside the ledger, created once and never rewritten.
The console reads the file format only; it imports nothing from the command
that writes it. :class:`NotebookFile` reads the lab notebook shipped in the
release. Neither writes anything.

Reference: openspec/changes/718-shadow-screen/change.md (D-2, D-3); ADR-021 §2.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Final, cast

from hermes.application.console.shadow_screen import (
    NotebookEntry,
    ReadyRule,
    ReadyVerdict,
    ShadowRecordError,
    ShadowRunRecord,
    parse_notebook,
)

__all__ = [
    "RUNS_DIR_NAME",
    "NotebookFile",
    "RunsDirectory",
    "ShadowRecordError",
    "parse_run_record",
    "runs_dir_for",
]

RUNS_DIR_NAME: Final = "runs"

type _Json = Mapping[str, object]


def runs_dir_for(ledger_path: Path) -> Path:
    """Return the runs directory of a ledger: its directory / ``runs``.

    Args:
        ledger_path: The ledger file.

    Returns:
        Where ``hermes shadow-start`` writes the day-0 records.
    """
    return ledger_path.parent / RUNS_DIR_NAME


def _text(document: _Json, key: str) -> str:
    value = document.get(key)
    if not isinstance(value, str) or not value:
        msg = f"field {key!r} must be a non-empty string"
        raise ShadowRecordError(msg)
    return value


def _aware(raw: str, key: str) -> datetime:
    try:
        value = datetime.fromisoformat(raw)
    except ValueError as exc:
        msg = f"field {key!r} is not an ISO timestamp: {raw!r}"
        raise ShadowRecordError(msg) from exc
    if value.utcoffset() is None:
        msg = f"field {key!r} has no timezone offset: {raw!r}"
        raise ShadowRecordError(msg)
    return value


def _verdict(raw: object, rule: str) -> ReadyVerdict:
    try:
        return ReadyVerdict(str(raw))
    except ValueError as exc:
        msg = f"Ready rule {rule!r} has an unknown verdict {raw!r}"
        raise ShadowRecordError(msg) from exc


def _rules(raw: object) -> tuple[ReadyRule, ...]:
    if raw is None:
        return ()
    if isinstance(raw, Mapping):
        return tuple(ReadyRule(str(rule), _verdict(v, str(rule))) for rule, v in raw.items())
    if not isinstance(raw, list):
        msg = "field 'checks' must be a list or an object"
        raise ShadowRecordError(msg)
    rules: list[ReadyRule] = []
    for item in raw:
        if not isinstance(item, Mapping):
            msg = "every entry of 'checks' must be an object"
            raise ShadowRecordError(msg)
        entry = cast("_Json", item)
        name = entry.get("rule", entry.get("name"))
        if not isinstance(name, str) or not name:
            msg = "every entry of 'checks' needs a 'rule'"
            raise ShadowRecordError(msg)
        rules.append(ReadyRule(name, _verdict(entry.get("verdict"), name)))
    return tuple(rules)


def parse_run_record(document: _Json) -> ShadowRunRecord:
    """Return the fields the console shows from one day-0 record.

    Args:
        document: The decoded JSON object.

    Returns:
        Run id, strategy, day 0, release and the Ready-rule verdicts; the
        hashes and the package version are left for ``hermes certify``.

    Raises:
        ShadowRecordError: A field is missing or invalid.
    """
    return ShadowRunRecord(
        run_id=_text(document, "run_id"),
        strategy_id=_text(document, "strategy_id"),
        day_zero_at=_aware(_text(document, "day_zero_at"), "day_zero_at"),
        release=_text(document, "release"),
        ready_rules=_rules(document.get("checks")),
    )


def _read(path: Path) -> ShadowRunRecord:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        msg = f"{path.name}: {exc}"
        raise ShadowRecordError(msg) from exc
    if not isinstance(document, Mapping):
        msg = f"{path.name}: a day-0 record must be a JSON object"
        raise ShadowRecordError(msg)
    try:
        return parse_run_record(cast("_Json", document))
    except ShadowRecordError as exc:
        msg = f"{path.name}: {exc}"
        raise ShadowRecordError(msg) from exc


class RunsDirectory:
    """Day-0 records in one runs directory; implements ``ShadowRunRecords``.

    Args:
        runs_dir: The directory; a missing one means no run has counted yet.
    """

    def __init__(self, runs_dir: Path) -> None:
        """Remember the directory; nothing is read until asked."""
        self._dir = runs_dir

    @property
    def directory(self) -> Path:
        """Return the directory the records are read from."""
        return self._dir

    def latest_run(self, strategy_id: str) -> ShadowRunRecord | None:
        """Return the record of ``strategy_id`` with the latest day 0.

        A file whose name starts with the strategy but whose ``strategy_id``
        differs (another strategy sharing the prefix) is not this strategy's.

        Args:
            strategy_id: The roster's strategy name.

        Returns:
            The newest record, or ``None`` when the strategy has none.

        Raises:
            ShadowRecordError: A record of the strategy cannot be read.
        """
        if not self._dir.is_dir():
            return None
        records = [
            record
            for path in sorted(self._dir.glob(f"{strategy_id}-*.json"))
            if (record := _read(path)).strategy_id == strategy_id
        ]
        return max(records, key=lambda r: r.day_zero_at, default=None)


class NotebookFile:
    """The lab notebook file; implements ``NotebookSource``.

    Args:
        path: The notebook, or ``None`` when the release ships none.
    """

    def __init__(self, path: Path | None) -> None:
        """Remember the path; nothing is read until asked."""
        self._path = path

    def notebook_entries(self) -> tuple[NotebookEntry, ...] | None:
        """Return the entries oldest first, or ``None`` when the file is not here."""
        if self._path is None:
            return None
        try:
            text = self._path.read_text(encoding="utf-8")
        except OSError:
            return None
        return parse_notebook(text)
