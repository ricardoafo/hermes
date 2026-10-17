"""Unit tests for ``hermes journal`` (#363)."""

from __future__ import annotations

import io
import json
from datetime import date, timedelta
from pathlib import Path

from hermes.adapters.certification.json_store import JsonCertificationStore
from hermes.application.calibration.certification_gates import CertificationRecord
from hermes.application.calibration.lifecycle import CertificationState
from hermes.entrypoints import journal as journal_module
from hermes.infrastructure.time import now_utc


def _run(tmp_path: Path, *args: str) -> tuple[int, str]:
    out = io.StringIO()
    code = journal_module.main(
        ["--journal", str(tmp_path / "j.jsonl"), "--store-dir", str(tmp_path / "cert"), *args],
        stream=out,
    )
    return code, out.getvalue()


def _run_split(tmp_path: Path, *args: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = journal_module.main(
        ["--journal", str(tmp_path / "j.jsonl"), "--store-dir", str(tmp_path / "cert"), *args],
        stream=out,
        err=err,
    )
    return code, out.getvalue(), err.getvalue()


def _add(tmp_path: Path, kind: str, review_on: date) -> str:
    code, out = _run(
        tmp_path,
        "add",
        "--decision",
        "Pick the M-09 thresholds",
        "--option",
        "ADR-011",
        "--option",
        "success-criteria draft",
        "--chosen",
        "ADR-011",
        "--reasoning",
        "Accepted ADR beats a draft",
        "--expected",
        "fewer false blocks",
        "--review-on",
        review_on.isoformat(),
        "--kind",
        kind,
    )
    assert code == 0, out
    return out.split()[1]


def test_add_then_list(tmp_path: Path) -> None:
    """A recorded decision shows in the list with its chosen option and review date."""
    entry_id = _add(tmp_path, "process", date(2027, 1, 1))
    code, out = _run(tmp_path, "list")
    assert code == 0
    assert f"DECISION {entry_id}" in out
    assert "-> ADR-011; review 2027-01-01" in out


# SEV-1: ruling 1 — a strategy decision must not be scored before certification
def test_strategy_decision_is_sealed_without_certification(tmp_path: Path) -> None:
    """Without certification a due strategy decision is listed sealed and cannot be scored."""
    today = now_utc().date()
    entry_id = _add(tmp_path, "strategy", today)
    _, due_out = _run(tmp_path, "due")
    code, review_out, review_err = _run_split(
        tmp_path, "review", entry_id, "--outcome", "x", "--score", "hit", "--evidence", "y"
    )
    assert f"SEALED  {entry_id}" in due_out
    assert (code, review_out, "sealed until certification" in review_err) == (1, "", True)


def test_certified_strategy_unseals(tmp_path: Path) -> None:
    """An OOS_CERTIFIED record unseals strategy decisions for review."""
    JsonCertificationStore(tmp_path / "cert").save(
        CertificationRecord("swing-donchian-4h", CertificationState.OOS_CERTIFIED)
    )
    entry_id = _add(tmp_path, "strategy", now_utc().date())
    code, out = _run(
        tmp_path,
        "review",
        entry_id,
        "--outcome",
        "held up",
        "--score",
        "hit",
        "--evidence",
        "https://example.invalid/report",
    )
    assert (code, out.strip()) == (0, f"REVIEWED {entry_id}: hit")


def test_future_review_is_refused(tmp_path: Path) -> None:
    """Reviewing before the review date exits 1."""
    entry_id = _add(tmp_path, "process", now_utc().date() + timedelta(days=30))
    code, out, err = _run_split(
        tmp_path, "review", entry_id, "--outcome", "x", "--score", "miss", "--evidence", "y"
    )
    assert (code, out, err.startswith("REFUSED")) == (1, "", True)


def test_bad_arguments_exit_two(tmp_path: Path) -> None:
    """A missing subcommand is a usage error."""
    code, _ = _run(tmp_path)
    assert code == 2


def test_help_on_the_command_prints_exit_codes_and_returns_zero(tmp_path: Path) -> None:
    """``-h`` documents the exit codes, returns 0 and leaves no ERROR line."""
    code, out = _run(tmp_path, "-h")
    assert (code, "Exit codes: 0 ok" in out, "ERROR" in out) == (0, True, False)


def test_help_on_each_subcommand_names_that_subcommand(tmp_path: Path) -> None:
    """``--help`` after a subcommand prints that subcommand's usage without required args."""
    usages = {name: _run(tmp_path, name, "--help") for name in ("add", "due", "list", "review")}
    assert {name: (c, f"hermes journal {name}" in out) for name, (c, out) in usages.items()} == {
        name: (0, True) for name in usages
    }


def test_list_json_prints_one_document_with_every_entry(tmp_path: Path) -> None:
    """``list --json`` is a single JSON document holding the decisions in the journal."""
    entry_id = _add(tmp_path, "process", date(2027, 1, 1))
    code, out = _run(tmp_path, "list", "--json")
    entries = json.loads(out)["entries"]
    assert (code, len(entries), entries[0]["entry_id"], entries[0]["review_on"]) == (
        0,
        1,
        entry_id,
        "2027-01-01",
    )


def test_list_json_includes_reviews_as_typed_entries(tmp_path: Path) -> None:
    """A review serialises with ``type`` review and its score."""
    entry_id = _add(tmp_path, "process", now_utc().date())
    _run(tmp_path, "review", entry_id, "--outcome", "ok", "--score", "hit", "--evidence", "e")
    _, out = _run(tmp_path, "list", "--json")
    kinds = [(e["type"], e.get("score")) for e in json.loads(out)["entries"]]
    assert kinds == [("decision", None), ("review", "hit")]


def test_due_json_splits_reviewable_and_sealed(tmp_path: Path) -> None:
    """``due --json`` lists a due process decision as reviewable and a strategy one as sealed."""
    process_id = _add(tmp_path, "process", now_utc().date())
    strategy_id = _add(tmp_path, "strategy", now_utc().date())
    code, out = _run(tmp_path, "due", "--json")
    report = json.loads(out)
    assert (
        code,
        [e["entry_id"] for e in report["reviewable"]],
        [e["entry_id"] for e in report["sealed"]],
    ) == (0, [process_id], [strategy_id])


def test_repair_without_confirm_names_the_torn_line_and_changes_nothing(tmp_path: Path) -> None:
    """A dry run refuses (exit 1) with the line, and leaves the journal as it was (#891)."""
    _add(tmp_path, "process", date(2099, 1, 1))
    path = tmp_path / "j.jsonl"
    with path.open("ab") as fh:
        fh.write(b'{"kind": "deci')
    before = path.read_bytes()
    code, _, err = _run_split(tmp_path, "repair")
    assert (code, "line 2 (14 bytes" in err, "--confirm REPAIR" in err, path.read_bytes()) == (
        1,
        True,
        True,
        before,
    )


def test_repair_with_confirm_drops_the_torn_line_and_list_works_again(tmp_path: Path) -> None:
    """With --confirm REPAIR the journal reads again and keeps its one decision (#891)."""
    _add(tmp_path, "process", date(2099, 1, 1))
    with (tmp_path / "j.jsonl").open("ab") as fh:
        fh.write(b'{"kind": "deci')
    repaired, out = _run(tmp_path, "repair", "--confirm", "REPAIR")
    listed, listing = _run(tmp_path, "list")
    assert (
        repaired,
        out.startswith("REPAIRED: dropped line 2"),
        listed,
        listing.count("DECISION"),
    ) == (
        0,
        True,
        0,
        1,
    )


def test_repair_of_a_whole_journal_says_so(tmp_path: Path) -> None:
    """Nothing torn: exit 0 and one line saying so (#891)."""
    _add(tmp_path, "process", date(2099, 1, 1))
    assert _run(tmp_path, "repair") == (0, "Nothing to repair: the last line is whole.\n")
