"""Unit tests for ``open_ledger`` (issue #855).

Invariants under test:

- Every ledger connection, read-write or read-only, carries the busy timeout,
  so contention is waited out instead of failing at once.
- A read-only connection cannot write.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from hermes.adapters.ledger.connection import BUSY_TIMEOUT_SECONDS, open_ledger


@pytest.mark.parametrize("read_only", [False, True])
def test_every_ledger_connection_waits_out_a_lock(tmp_path: Path, read_only: bool) -> None:
    """Both kinds of connection report the 10 s busy timeout to SQLite."""
    db = tmp_path / "ledger.sqlite3"
    sqlite3.connect(db).close()
    with closing(open_ledger(db, read_only=read_only)) as conn:
        timeout_ms = conn.execute("PRAGMA busy_timeout").fetchone()[0]
    assert timeout_ms == int(BUSY_TIMEOUT_SECONDS * 1000) == 10_000


def test_a_read_only_connection_refuses_a_write(tmp_path: Path) -> None:
    """``read_only=True`` opens with mode=ro: a write raises instead of landing."""
    db = tmp_path / "ledger.sqlite3"
    with closing(sqlite3.connect(db)) as setup:
        setup.execute("CREATE TABLE t (x INTEGER)")
    with closing(open_ledger(db, read_only=True)) as conn, pytest.raises(sqlite3.OperationalError):
        conn.execute("INSERT INTO t VALUES (1)")
