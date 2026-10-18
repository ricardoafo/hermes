"""Unit tests for ``open_ledger`` (issues #855, #879).

Invariants under test:

- Every ledger connection, read-write or read-only, carries the busy timeout,
  so contention is waited out instead of failing at once.
- A read-only connection cannot write.
- A read-write connection puts the ledger in WAL with a bounded ``-wal`` file.
- Closing the last read-write connection leaves the ``-wal`` and ``-shm``
  files, so a read-only unit that cannot write the ledger directory still
  opens the ledger, and sees every committed row, while serve is stopped.
- ``ReadOnlyLedgerReader`` (#1178): a missing file is ``FileNotFoundError``,
  SQLite's refusal is ``LedgerError``, the reader cannot write, and leaving
  a ``with`` block closes it.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import closing
from pathlib import Path

import pytest

from hermes.adapters.ledger import connection as connection_module
from hermes.adapters.ledger.connection import (
    BUSY_TIMEOUT_SECONDS,
    JOURNAL_SIZE_LIMIT_BYTES,
    ReadOnlyLedgerReader,
    open_ledger,
)
from hermes.domain.errors import LedgerError

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
    ``-wal`` file must be visible.

    The VPS mount fails the write open with EROFS, this test with EACCES.
    SQLite does not tell them apart: when ``open(O_RDWR)`` of ``-shm`` fails
    for any reason it opens it ``O_RDONLY`` and runs read-only
    (``unixOpenSharedMemory`` in ``os_unix.c``), so both take the same path.
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


def _count_in_thread(connection: sqlite3.Connection) -> list[object]:
    """Run one query on another thread; return its row or the error it raised."""
    outcome: list[object] = []

    def query() -> None:
        try:
            outcome.append(connection.execute("SELECT count(*) FROM t").fetchone())
        except sqlite3.ProgrammingError as exc:
            outcome.append(type(exc))

    worker = threading.Thread(target=query)
    worker.start()
    worker.join()
    return outcome


def test_an_any_thread_read_only_connection_answers_another_thread(tmp_path: Path) -> None:
    """The console's worker can read through a connection opened on the UI thread (#1001)."""
    db = tmp_path / "ledger.sqlite3"
    with closing(sqlite3.connect(db)) as conn:
        conn.execute("CREATE TABLE t (x INTEGER)")
        conn.executemany("INSERT INTO t VALUES (?)", [(1,), (2,)])
        conn.commit()
    with closing(open_ledger(db, read_only=True, any_thread=True)) as conn:
        assert _count_in_thread(conn) == [(2,)]


def test_a_default_read_only_connection_stays_on_its_own_thread(tmp_path: Path) -> None:
    """Without any_thread, SQLite refuses a query from another thread, as before."""
    db = tmp_path / "ledger.sqlite3"
    with closing(sqlite3.connect(db)) as conn:
        conn.execute("CREATE TABLE t (x INTEGER)")
        conn.commit()
    with closing(open_ledger(db, read_only=True)) as conn:
        assert _count_in_thread(conn) == [sqlite3.ProgrammingError]


def test_a_writing_connection_never_moves_between_threads(tmp_path: Path) -> None:
    """any_thread is refused for a read-write connection, which could write from anywhere."""
    with pytest.raises(ValueError, match="only a read-only ledger connection"):
        open_ledger(tmp_path / "ledger.sqlite3", any_thread=True)


class _Reader(ReadOnlyLedgerReader):
    def count(self) -> int:
        return int(self._conn.execute("SELECT count(*) FROM t").fetchone()[0])

    def write(self) -> None:
        self._conn.execute("INSERT INTO t VALUES (2)")


def _ledger(tmp_path: Path) -> Path:
    db = tmp_path / "hermes.sqlite3"
    with closing(open_ledger(db)) as conn:
        conn.execute("CREATE TABLE t (x INTEGER)")
        conn.execute("INSERT INTO t VALUES (1)")
        conn.commit()
    return db


def test_a_reader_over_a_missing_file_is_file_not_found(tmp_path: Path) -> None:
    """Opening a reader on a path with no file raises FileNotFoundError naming the path."""
    missing = tmp_path / "absent.sqlite3"

    with pytest.raises(FileNotFoundError, match=r"absent\.sqlite3"):
        _Reader.open(missing)


def test_sqlites_refusal_to_open_is_a_ledger_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A sqlite3.Error at open becomes LedgerError, the only error reader callers handle."""

    def refuse(*_args: object, **_kwargs: object) -> sqlite3.Connection:
        raise sqlite3.OperationalError("unable to open database file")

    monkeypatch.setattr(connection_module, "open_ledger", refuse)

    with pytest.raises(LedgerError, match="read-only: unable to open database file"):
        _Reader.open(_ledger(tmp_path))


def test_a_reader_cannot_write(tmp_path: Path) -> None:
    """The reader's connection is mode=ro: a write is refused and the row count stays 1."""
    db = _ledger(tmp_path)

    with _Reader.open(db) as reader, pytest.raises(sqlite3.OperationalError, match="readonly"):
        reader.write()
    with _Reader.open(db) as reader:
        assert reader.count() == 1


def test_leaving_the_with_block_closes_the_reader(tmp_path: Path) -> None:
    """After the with block the connection is closed, so a further query fails."""
    with _Reader.open(_ledger(tmp_path)) as reader:
        assert reader.count() == 1

    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        reader.count()


def test_any_thread_reaches_the_readers_connection(tmp_path: Path) -> None:
    """``open(any_thread=True)`` hands the reader a connection another thread may use."""
    with _Reader.open(_ledger(tmp_path), any_thread=True) as reader:
        answers: list[int] = []
        worker = threading.Thread(target=lambda: answers.append(reader.count()))
        worker.start()
        worker.join()

    assert answers == [1]
