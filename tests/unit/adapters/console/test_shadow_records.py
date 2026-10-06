"""Reading the day-0 records and the lab notebook from files (issue #718).

The record fixture copies the field list ``hermes shadow-start`` writes
(issue #797); the console reads that format without importing the command.

Invariants:

- the runs directory is the ledger's directory / ``runs``;
- the newest record of the strategy wins, by ``day_zero_at``; another
  strategy's files never count, even with a shared name prefix;
- no directory or no file means no record (a warm-up), never an error;
- a record with a missing or naive ``day_zero_at`` or an unknown verdict is an
  error, never a silent warm-up;
- the notebook file reads to its entries, and a missing file to ``None``.
"""

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

from hermes.adapters.console.shadow_records import (
    NotebookFile,
    RunsDirectory,
    parse_run_record,
    runs_dir_for,
)
from hermes.application.console.shadow_screen import (
    ReadyRule,
    ReadyVerdict,
    ShadowRecordError,
)

_FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "fixtures"
    / "shadow_runs"
    / "swing-donchian-4h-20261015T000000Z.json"
)
_STRATEGY = "swing-donchian-4h"


def _document() -> dict[str, object]:
    loaded: dict[str, object] = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    return loaded


def _write(folder: Path, name: str, document: dict[str, object]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(json.dumps(document), encoding="utf-8")


def test_runs_dir_sits_beside_the_ledger() -> None:
    """``/var/lib/hermes/hermes.sqlite3`` keeps its records in ``/var/lib/hermes/runs``."""
    assert runs_dir_for(Path("/var/lib/hermes/hermes.sqlite3")) == Path("/var/lib/hermes/runs")


def test_fixture_record_parses_to_the_shown_fields() -> None:
    """Run id, strategy, aware day 0, release and the seven Ready verdicts come through."""
    record = parse_run_record(_document())
    assert (
        record.run_id,
        record.strategy_id,
        record.day_zero_at,
        record.release,
        len(record.ready_rules),
        record.ready_rules[2],
    ) == (
        "swing-donchian-4h-20261015T000000Z",
        _STRATEGY,
        datetime(2026, 10, 15, tzinfo=UTC),
        "2026-10-14-abc1234",
        7,
        ReadyRule("decision-changing defects fixed", ReadyVerdict.BY_HAND),
    )


def test_missing_directory_is_no_record(tmp_path: Path) -> None:
    """Before ``hermes shadow-start`` ever ran there is no runs directory: a warm-up.

    An empty directory and a directory with only another strategy's record
    are the same warm-up.
    """
    other = tmp_path / "other"
    _write(other, "swing-other-20261015T000000Z.json", _document() | {"strategy_id": "swing-other"})
    (tmp_path / "empty").mkdir()
    assert [
        RunsDirectory(folder).latest_run(_STRATEGY)
        for folder in (tmp_path / "runs", tmp_path / "empty", other)
    ] == [None, None, None]


def test_newest_day_zero_of_the_strategy_wins(tmp_path: Path) -> None:
    """Two runs of the strategy: the later day 0; a prefix-sharing strategy is ignored."""
    runs = tmp_path / "runs"
    runs.mkdir()
    shutil.copy(_FIXTURE, runs / _FIXTURE.name)
    later = _document() | {
        "run_id": "swing-donchian-4h-20270201T000000Z",
        "day_zero_at": "2027-02-01T00:00:00+00:00",
    }
    _write(runs, "swing-donchian-4h-20270201T000000Z.json", later)
    other = _document() | {
        "strategy_id": "swing-donchian-4h-fast",
        "day_zero_at": "2028-01-01T00:00:00+00:00",
    }
    _write(runs, "swing-donchian-4h-fast-20280101T000000Z.json", other)
    record = RunsDirectory(runs).latest_run(_STRATEGY)
    assert record is not None
    assert record.run_id == "swing-donchian-4h-20270201T000000Z"


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"day_zero_at": "2026-10-15T00:00:00"}, "no timezone offset"),
        ({"day_zero_at": None}, "'day_zero_at' must be a non-empty string"),
        ({"run_id": ""}, "'run_id' must be a non-empty string"),
        ({"checks": [{"rule": "x", "verdict": "maybe"}]}, "unknown verdict"),
    ],
    ids=["naive", "missing-day-zero", "empty-run-id", "unknown-verdict"],
)
def test_a_broken_record_is_an_error(
    tmp_path: Path, change: dict[str, object], message: str
) -> None:
    """A record that exists but cannot be read is said, never shown as a warm-up."""
    _write(tmp_path, "swing-donchian-4h-20261015T000000Z.json", _document() | change)
    with pytest.raises(ShadowRecordError, match=message):
        RunsDirectory(tmp_path).latest_run(_STRATEGY)


def test_checks_as_an_object_read_as_rule_verdict_pairs() -> None:
    """``checks`` written as ``{rule: verdict}`` reads to the same rules."""
    record = parse_run_record(_document() | {"checks": {"parity": "fail"}})
    assert record.ready_rules == (ReadyRule("parity", ReadyVerdict.FAIL),)


def test_notebook_file_reads_its_entries(tmp_path: Path) -> None:
    """The file's entries come back oldest first; a missing file or path is ``None``."""
    path = tmp_path / "lab-notebook.md"
    path.write_text(
        "## Entries\n\n### 2026-10-06T11:13:17Z | shadow | operator | clock reset\n",
        encoding="utf-8",
    )
    entries = NotebookFile(path).notebook_entries()
    assert entries is not None
    assert (
        [e.action for e in entries],
        NotebookFile(tmp_path / "gone.md").notebook_entries(),
        NotebookFile(None).notebook_entries(),
    ) == (["clock reset"], None, None)
