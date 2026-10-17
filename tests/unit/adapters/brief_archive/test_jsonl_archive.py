"""Tests for the kept supervisor briefs (issue #897).

Invariants:

- a kept brief reads back exactly as it was published, digest flag included;
- briefs list newest first;
- one brief per kind and UTC day is kept: a second run the same day keeps the first;
- a last line cut short by a power loss is skipped, and the next append starts
  on a line of its own, so one torn write costs one brief and nothing else;
- a line that is valid JSON but not a brief, or a file that is not text, raises
  ``BriefArchiveError`` and never another exception.
"""

import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Final

import pytest
from hypothesis import example, given
from hypothesis import strategies as st

from hermes.adapters.brief_archive import JsonlBriefArchive
from hermes.application.console.views import BriefSection, ConsoleBrief
from hermes.domain.errors import BriefArchiveError
from tests.unit.adapters.wire_fuzz import FUZZ_SLOW, foreign_error, mutated

_DAY: Final = datetime(2026, 10, 3, 5, 15, tzinfo=UTC)


def _brief(
    at: datetime = _DAY, *, kind: str = "daily", text: str = "Health ok.", digest: bool = False
) -> ConsoleBrief:
    sections = (
        BriefSection("Health", text, ("health",)),
        BriefSection("What the engine did", "BTC/USDT: no signal.", ("engine", "signals")),
    )
    return ConsoleBrief(kind, at, sections, digest=digest)


def _archive(tmp_path: Path) -> JsonlBriefArchive:
    return JsonlBriefArchive(tmp_path / "briefs.jsonl")


def test_a_kept_brief_reads_back_unchanged(tmp_path: Path) -> None:
    """Invariant: the Briefs screen shows the text that passed the gate, not a recomposition."""
    archive = _archive(tmp_path)
    kept = _brief(text="Health ok, 2 units. Ünïcode ✓", digest=True)

    assert archive.append(kept) is True

    assert archive.briefs() == (kept,)


def test_briefs_list_newest_first_whatever_the_order_kept(tmp_path: Path) -> None:
    """Invariant: the screen's list is ordered by the instant a brief describes."""
    archive = _archive(tmp_path)
    older, newer = _brief(_DAY - timedelta(days=2)), _brief(_DAY)
    archive.append(_brief(_DAY - timedelta(days=1)))
    archive.append(newer)
    archive.append(older)

    assert [b.as_of for b in archive.briefs()] == [
        _DAY,
        _DAY - timedelta(days=1),
        _DAY - timedelta(days=2),
    ]


def test_a_second_run_the_same_day_keeps_the_first_brief(tmp_path: Path) -> None:
    """Invariant: the archive holds what was published, once per kind and UTC day."""
    archive = _archive(tmp_path)
    first = _brief(text="Health ok.")

    assert archive.append(first) is True
    assert archive.append(_brief(_DAY + timedelta(hours=3), text="Health down.")) is False

    assert archive.briefs() == (first,)


def test_the_utc_day_decides_not_the_offset_the_instant_is_written_in(tmp_path: Path) -> None:
    """Invariant: 00:30 on the 4th in Madrid is 22:30 on the 3rd UTC, the same day as 05:15 UTC."""
    archive = _archive(tmp_path)
    madrid = timezone(timedelta(hours=2))
    archive.append(_brief(datetime(2026, 10, 4, 0, 30, tzinfo=madrid)))

    assert archive.append(_brief(datetime(2026, 10, 3, 23, 0, tzinfo=UTC))) is False
    assert archive.append(_brief(datetime(2026, 10, 4, 0, 0, tzinfo=UTC))) is True


def test_a_daily_and_a_weekly_brief_of_one_day_are_both_kept(tmp_path: Path) -> None:
    """Invariant: the once-a-day rule is per kind."""
    archive = _archive(tmp_path)

    assert archive.append(_brief(kind="daily")) is True
    assert archive.append(_brief(kind="weekly")) is True

    assert {b.kind for b in archive.briefs()} == {"daily", "weekly"}


def test_no_archive_file_holds_no_briefs_and_reading_creates_nothing(tmp_path: Path) -> None:
    """Invariant: a fresh machine has an empty list, and the console's read never writes."""
    archive = _archive(tmp_path)

    assert archive.briefs() == ()
    assert list(tmp_path.iterdir()) == []


def test_a_torn_last_line_is_skipped_and_the_next_append_starts_a_new_line(tmp_path: Path) -> None:
    """Invariant: one torn write costs one brief; the archive keeps reading and writing."""
    archive = _archive(tmp_path)
    before, after = _brief(_DAY - timedelta(days=1)), _brief(_DAY)
    archive.append(before)
    with archive._path.open("a", encoding="utf-8") as handle:
        handle.write('{"kind": "daily", "as_of": "2026-10-02T05:1')

    assert archive.briefs() == (before,)
    assert archive.append(after) is True

    assert archive.briefs() == (after, before)


@pytest.mark.parametrize(
    "line",
    [
        "[]",
        "{}",
        '{"kind": "daily", "as_of": "2026-10-03T05:15:00", "digest": false, "sections": []}',
        '{"kind": "daily", "as_of": "2026-10-03T05:15:00+00:00", "digest": "no", "sections": []}',
        '{"kind": "daily", "as_of": "2026-10-03T05:15:00+00:00", "digest": false, "sections": "x"}',
    ],
    ids=["array", "empty", "naive-time", "digest-not-bool", "sections-not-list"],
)
def test_a_line_that_is_not_a_brief_raises_the_archive_error(tmp_path: Path, line: str) -> None:
    """Invariant: a hand edit or a writer bug is named, with its line, not swallowed."""
    archive = _archive(tmp_path)
    archive._path.write_text(line + "\n", encoding="utf-8")

    with pytest.raises(BriefArchiveError, match="line 1 is not a brief"):
        archive.briefs()


def test_a_file_that_is_not_text_raises_the_archive_error(tmp_path: Path) -> None:
    """Invariant: bytes that are not UTF-8 are an archive error, not a UnicodeDecodeError."""
    archive = _archive(tmp_path)
    archive._path.write_bytes(b"\xff\xfe\n")

    with pytest.raises(BriefArchiveError, match="not UTF-8 text"):
        archive.briefs()


def test_a_deeply_nested_line_raises_the_archive_error(tmp_path: Path) -> None:
    """Invariant: a hostile line cannot crash the console with a RecursionError."""
    archive = _archive(tmp_path)
    archive._path.write_text("[" * 100_000 + "]" * 100_000 + "\n", encoding="utf-8")

    with pytest.raises(BriefArchiveError, match="nests too deep"):
        archive.briefs()


def test_append_creates_the_parent_directory(tmp_path: Path) -> None:
    """Invariant: the first timer run on a fresh state directory writes the file."""
    archive = JsonlBriefArchive(tmp_path / "state" / "briefs.jsonl")

    assert archive.append(_brief()) is True

    assert archive.briefs() == (_brief(),)


_SEED: Final = json.loads(
    json.dumps(
        {
            "kind": "daily",
            "as_of": _DAY.isoformat(),
            "digest": False,
            "sections": [{"heading": "Health", "text": "Health ok.", "sources": ["health"]}],
        }
    )
)


@FUZZ_SLOW
@given(data=st.data())
@example(data=None)
def test_any_json_line_reads_as_a_brief_or_raises_the_archive_error(
    data: st.DataObject | None, tmp_path: Path
) -> None:
    """Invariant: whatever the line holds, briefs() returns briefs or raises BriefArchiveError."""
    body = {**_SEED, "sections": "x"} if data is None else data.draw(mutated(_SEED))
    archive = _archive(tmp_path)
    archive._path.write_text(json.dumps(body) + "\n", encoding="utf-8")

    assert foreign_error(archive.briefs, BriefArchiveError) == ""
