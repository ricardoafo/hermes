"""Unit tests for ``hermes ledger-check`` (#559).

Invariant under test: the command prints one line per check and maps the
outcome to its exit code (0 all pass, 1 a failure, 3 no ledger).
"""

from __future__ import annotations

import io
import sqlite3
from pathlib import Path

from hermes.adapters.ledger.migration_runner import apply_migrations
from hermes.entrypoints import cli
from hermes.entrypoints import ledger_check as module


def _ledger(tmp_path: Path) -> Path:
    db = tmp_path / "ledger.sqlite3"
    conn = sqlite3.connect(db)
    apply_migrations(conn)
    conn.close()
    return db


def _run(argv: list[str]) -> tuple[int, str]:
    out = io.StringIO()
    return module.main(argv, stream=out), out.getvalue()


def test_clean_ledger_exits_0_with_one_line_per_check(tmp_path: Path) -> None:
    """Four checks, four ok lines."""
    code, out = _run(["--db", str(_ledger(tmp_path))])
    assert code == 0
    assert [line.split(":")[0] for line in out.splitlines()] == [
        "ok   integrity",
        "ok   migrations",
        "ok   lot remainders",
        "ok   orphans",
    ]


def test_a_failed_check_exits_1(tmp_path: Path) -> None:
    """One broken remainder fails the run."""
    db = _ledger(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO lots (lot_id, asset, original_qty, remaining_qty, opened_at, strategy_id) "
        "VALUES ('L1', 'BTC', '1', '2', 't', 's')"
    )
    conn.commit()
    conn.close()
    code, out = _run(["--db", str(db)])
    assert code == 1
    assert "FAIL lot remainders: L1: original 1 - consumed 0 != remaining 2" in out


def test_missing_ledger_exits_3(tmp_path: Path) -> None:
    """No ledger, no checks."""
    code, out = _run(["--db", str(tmp_path / "absent.sqlite3")])
    assert code == 3
    assert out.startswith("ERROR: Ledger error: ledger ")


def test_cli_routes_ledger_check(tmp_path: Path) -> None:
    """`hermes ledger-check` reaches this command."""
    out = io.StringIO()
    assert cli.main(["ledger-check", "--db", str(_ledger(tmp_path))], stream=out) == 0
    assert "ok   orphans" in out.getvalue()
