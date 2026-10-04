"""Tests for ``hermes ledger verify`` (#538).

Invariants protected here:

- a clean ledger prints ``OK — N events, 0 gaps, schema vX`` and exits 0;
- findings are listed one per line and exit 1;
- ``--json`` is one document with the same facts;
- usage errors exit 2; a missing or non-SQLite ledger exits 3;
- the command is registered as ``ledger``.
"""

from __future__ import annotations

import io
import json
import sqlite3
from pathlib import Path

import pytest

from hermes.adapters.ledger.migration_runner import MIGRATIONS_DIR, apply_migrations
from hermes.entrypoints import cli
from hermes.entrypoints import ledger as module
from hermes.entrypoints.commands import discover

_LATEST = int(sorted(MIGRATIONS_DIR.glob("*.sql"))[-1].name.split("_", 1)[0])


@pytest.fixture
def ledger(tmp_path: Path) -> Path:
    """Build a fully migrated ledger with 3 events (ids 1-3)."""
    db = tmp_path / "ledger.sqlite3"
    conn = sqlite3.connect(db)
    apply_migrations(conn)
    for _ in range(3):
        conn.execute(
            "INSERT INTO events (event_type, event_schema_version, correlation_id,"
            " occurred_at, payload_json, recorded_at) VALUES ('t', 1, 'c', 'now', '{}', 'now')"
        )
    conn.commit()
    conn.close()
    return db


def _run(argv: list[str]) -> tuple[int, str]:
    out = io.StringIO()
    return module.main(argv, stream=out), out.getvalue()


def test_clean_ledger_prints_ok_line(ledger: Path) -> None:
    """A clean ledger prints the runbook's OK line and exits 0."""
    assert _run(["verify", "--db", str(ledger)]) == (
        0,
        f"OK — 3 events, 0 gaps, schema v{_LATEST}\n",
    )


def test_gap_is_listed_and_exits_one(ledger: Path) -> None:
    """A deleted middle event exits 1 and names the missing id."""
    conn = sqlite3.connect(ledger)
    conn.execute("DELETE FROM events WHERE id = 2")
    conn.commit()
    conn.close()
    assert _run(["verify", "--db", str(ledger)]) == (
        1,
        f"FINDINGS — 2 events, 1 gaps, schema v{_LATEST}\n- event id gap: 2\n",
    )


def test_json_document(ledger: Path) -> None:
    """--json emits one document carrying the report's facts."""
    code, out = _run(["verify", "--db", str(ledger), "--json"])
    assert code == 0
    assert json.loads(out) == {
        "ok": True,
        "integrity": ["ok"],
        "schema_level": _LATEST,
        "pending_migrations": [],
        "event_count": 3,
        "gaps": [],
        "findings": [],
    }


def test_missing_ledger_exits_three(tmp_path: Path) -> None:
    """No file at --db exits 3 with the path in the message."""
    missing = tmp_path / "nope.sqlite3"
    assert _run(["verify", "--db", str(missing)]) == (3, f"ERROR: no ledger at {missing}.\n")


def test_non_sqlite_file_exits_three(tmp_path: Path) -> None:
    """A file that is not a SQLite database is unreadable: exit 3."""
    junk = tmp_path / "junk.sqlite3"
    junk.write_bytes(b"this is not a database" * 100)
    code, out = _run(["verify", "--db", str(junk)])
    assert code == 3
    assert out.startswith("ERROR: could not read the ledger:")


def test_no_subcommand_prints_usage_and_exits_two() -> None:
    """`hermes ledger` alone is a usage error."""
    code, out = _run([])
    assert code == 2
    assert out.startswith("ERROR: expected the subcommand `verify`.\nUsage: hermes ledger verify")


def test_unknown_subcommand_exits_two() -> None:
    """An unknown subcommand is a usage error."""
    assert _run(["repair"])[0] == 2


def test_bad_flag_exits_two() -> None:
    """An unknown flag is a usage error."""
    code, out = _run(["verify", "--bogus"])
    assert code == 2
    assert out.startswith("ERROR: bad arguments.\n")


@pytest.mark.parametrize("argv", [["--help"], ["verify", "-h"]])
def test_help_exits_zero_with_exit_codes(argv: list[str]) -> None:
    """Help documents the exit codes and returns 0."""
    code, out = _run(argv)
    assert code == 0
    assert "Exit codes: 0 OK, 1 findings, 2 usage, 3 ledger missing or unreadable." in out


def test_registered_and_dispatched_through_cli(ledger: Path) -> None:
    """The cli dispatches `ledger verify` to this module."""
    assert "ledger" in discover()
    out = io.StringIO()
    assert cli.main(["ledger", "verify", "--db", str(ledger)], stream=out) == 0
    assert out.getvalue().startswith("OK — 3 events")
