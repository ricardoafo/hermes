"""Unit tests for ``open_ledger`` (issues #855, #879).

Invariants under test:

- Every ledger connection, read-write or read-only, carries the busy timeout,
  so contention is waited out instead of failing at once.
- A read-only connection cannot write.
- A read-write connection puts the ledger in WAL with a bounded ``-wal`` file.
- Closing the last read-write connection leaves the ``-wal`` and ``-shm``
  files, so a read-only unit that cannot write the ledger directory still
  opens the ledger, and sees every committed row, while serve is stopped.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from hermes.adapters.ledger.connection import (
    BUSY_TIMEOUT_SECONDS,
    JOURNAL_SIZE_LIMIT_BYTES,
    open_ledger,
)

_READ_ONLY_DIR = 0o555
_WRITABLE_DIR = 0o755
_READ_ONLY_FILE = 0o444
_WRITABLE_FILE = 0o644


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


def test_a_read_write_connection_puts_the_ledger_in_wal(tmp_path: Path) -> None:
    """ADR-003 §03: the ledger runs in WAL, and a new connection sees it so."""
    db = tmp_path / "ledger.sqlite3"
    open_ledger(db).close()
    with closing(sqlite3.connect(db)) as plain:
        mode = plain.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode == "wal"


def test_a_read_write_connection_bounds_the_wal_file(tmp_path: Path) -> None:
    """The ``-wal`` file is truncated to 4 MiB after a checkpoint, so it stays small."""
    with closing(open_ledger(tmp_path / "ledger.sqlite3")) as conn:
        limit = conn.execute("PRAGMA journal_size_limit").fetchone()[0]
    assert limit == JOURNAL_SIZE_LIMIT_BYTES == 4_194_304


def test_closing_the_last_writer_keeps_the_wal_and_shm_files(tmp_path: Path) -> None:
    """Both files stay after serve's connection closes; SQLite would delete them otherwise."""
    db = tmp_path / "ledger.sqlite3"
    with closing(open_ledger(db)) as writer:
        writer.execute("CREATE TABLE t (x INTEGER)")
        writer.commit()
    left = sorted(p.name for p in tmp_path.iterdir())
    assert left == ["ledger.sqlite3", "ledger.sqlite3-shm", "ledger.sqlite3-wal"]


@pytest.mark.skipif(
    os.geteuid() == 0, reason="root ignores directory permissions, so nothing is read-only"
)
def test_a_read_only_unit_opens_the_ledger_while_serve_is_stopped(tmp_path: Path) -> None:
    """A reader that can write neither the directory nor any ledger file reads every row.

    This is the tax pack at 07:30 with serve stopped for a deploy. It can
    neither create a ``-wal`` or ``-shm`` file nor open one for writing, as
    under the VPS's read-only mount. Committed rows still sitting in the
    ``-wal`` file must be visible. The real read-only mount is in
    ``tests/integration/adapters/ledger/test_ledger_read_only_mount.py``.
    """
    state = tmp_path / "state"
    state.mkdir()
    db = state / "ledger.sqlite3"
    with closing(open_ledger(db)) as serve:
        serve.execute("CREATE TABLE t (x INTEGER)")
        serve.execute("INSERT INTO t VALUES (1)")
        serve.commit()
    files = sorted(state.iterdir())
    for path in files:
        path.chmod(_READ_ONLY_FILE)
    state.chmod(_READ_ONLY_DIR)
    try:
        with closing(open_ledger(db, read_only=True)) as reader:
            rows = reader.execute("SELECT x FROM t").fetchall()
    finally:
        state.chmod(_WRITABLE_DIR)
        for path in files:
            path.chmod(_WRITABLE_FILE)
    assert ([p.name for p in files], rows) == (
        ["ledger.sqlite3", "ledger.sqlite3-shm", "ledger.sqlite3-wal"],
        [(1,)],
    )
