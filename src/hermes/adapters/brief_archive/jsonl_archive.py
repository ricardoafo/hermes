"""Append-only JSON Lines archive of the briefs the supervisor desk published.

One brief per line, stored exactly as it passed the output gate (ADR-012 D-08):
the archive never composes or edits text, so the Briefs screen shows what the
desk published on the day and nothing recomposed from newer data. A line holds
the brief's kind, the instant it describes, its gate state and its cited items.

A write cut short by a power loss leaves a last line that is not valid JSON.
Reads skip such a line, and the next append starts on a fresh line, so one torn
write costs one brief and never blocks the archive.

Reference: issue #897.
"""

import json
from collections.abc import Mapping
from datetime import UTC, date, datetime
from pathlib import Path

from hermes.application.console.views import BriefSection, ConsoleBrief
from hermes.domain.errors import BriefArchiveError
from hermes.infrastructure.file_lock import locked

__all__ = ["APPEND_LOCK_TIMEOUT_SECONDS", "JsonlBriefArchive"]

APPEND_LOCK_TIMEOUT_SECONDS = 10.0
"""How long an append waits for another one; an append takes milliseconds."""


class JsonlBriefArchive:
    """Published briefs kept in one JSON Lines file.

    Args:
        path: The archive file. Created with its parent directory on first append.
    """

    def __init__(self, path: Path) -> None:
        self._path = path

    @property
    def lock_path(self) -> Path:
        """The lock file every append holds while it checks for the day and writes."""
        return self._path.with_name(self._path.name + ".lock")

    def append(self, brief: ConsoleBrief) -> bool:
        """Keep ``brief`` unless a brief of its kind is already kept for its UTC day.

        A second run on one day (a manual run beside the timer, or a missed run
        caught up at boot) keeps the first brief: the archive holds what was
        published, once per day.

        Args:
            brief: The brief as it passed the output gate.

        Returns:
            ``True`` when the brief was written, ``False`` when that day was already kept.

        Raises:
            BriefArchiveError: The existing file cannot be read.
            OSError: The write fails, or another append held the lock for
                :data:`APPEND_LOCK_TIMEOUT_SECONDS` (a :class:`TimeoutError`).
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with locked(self.lock_path, timeout=APPEND_LOCK_TIMEOUT_SECONDS):
            day = _utc_day(brief.as_of)
            if any(k.kind == brief.kind and _utc_day(k.as_of) == day for k in self.briefs()):
                return False
            line = json.dumps(_encode(brief), sort_keys=True, ensure_ascii=False)
            glue = "\n" if self._ends_mid_line() else ""
            with self._path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(f"{glue}{line}\n")
            return True

    def briefs(self) -> tuple[ConsoleBrief, ...]:
        """Return every kept brief, newest first.

        Raises:
            BriefArchiveError: The file is not UTF-8 text, or a line is valid
                JSON but not a brief.
        """
        if not self._path.exists():
            return ()
        try:
            text = self._path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise BriefArchiveError(str(self._path), f"not UTF-8 text: {exc}") from exc
        kept: list[ConsoleBrief] = []
        for number, raw in enumerate(text.splitlines(), 1):
            if not raw.strip():
                continue
            try:
                document = json.loads(raw)
            except RecursionError as exc:
                raise BriefArchiveError(str(self._path), f"line {number} nests too deep") from exc
            except ValueError:
                continue
            kept.append(self._decode(document, number))
        return tuple(sorted(kept, key=lambda b: b.as_of, reverse=True))

    def _ends_mid_line(self) -> bool:
        if not self._path.exists() or self._path.stat().st_size == 0:
            return False
        with self._path.open("rb") as handle:
            handle.seek(-1, 2)
            return handle.read(1) != b"\n"

    def _decode(self, document: object, number: int) -> ConsoleBrief:
        try:
            return _decode(document)
        except (KeyError, TypeError, ValueError) as exc:
            raise BriefArchiveError(
                str(self._path), f"line {number} is not a brief: {exc!r}"
            ) from exc


def _utc_day(moment: datetime) -> date:
    return moment.astimezone(UTC).date()


def _encode(brief: ConsoleBrief) -> dict[str, object]:
    return {
        "kind": brief.kind,
        "as_of": brief.as_of.isoformat(),
        "digest": brief.digest,
        "sections": [
            {"heading": s.heading, "text": s.text, "sources": list(s.sources)}
            for s in brief.sections
        ],
    }


def _decode(document: object) -> ConsoleBrief:
    if not isinstance(document, Mapping):
        raise TypeError("a brief is a JSON object")
    as_of = datetime.fromisoformat(str(document["as_of"]))
    if as_of.tzinfo is None:
        raise ValueError("as_of carries no timezone")
    raw_sections = document["sections"]
    if not isinstance(raw_sections, list):
        raise TypeError("sections is a list")
    sections = tuple(_decode_section(s) for s in raw_sections)
    digest = document["digest"]
    if not isinstance(digest, bool):
        raise TypeError("digest is a boolean")
    return ConsoleBrief(str(document["kind"]), as_of, sections, digest=digest)


def _decode_section(raw: object) -> BriefSection:
    if not isinstance(raw, Mapping) or not isinstance(raw["sources"], list):
        raise TypeError("a section is an object with a list of sources")
    return BriefSection(
        str(raw["heading"]), str(raw["text"]), tuple(str(c) for c in raw["sources"])
    )
