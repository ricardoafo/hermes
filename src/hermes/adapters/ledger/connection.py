"""The one way Hermes opens its SQLite ledger: in WAL, with a busy timeout.

Serve writes the ledger while the console, the bot, the tax pack and
``hermes status`` read it. With the rollback journal a reader's open read
transaction makes serve's commit wait, and a writer's commit makes a new
reader wait. The busy timeout turns that wait into a retry instead of an
immediate "database is locked".

:data:`BUSY_TIMEOUT_SECONDS` is 10 s. Every ledger read in Hermes is one
short query set (well under a second on the MVP1 ledger), so 10 s covers a
slow reader with a wide margin; a lock still held after 10 s means something
is stuck, and serve should fail loudly rather than wait for ever. Serve
handles one bar per 4 h, so a wait of that length loses nothing, and it stays
far below systemd's 90 s stop timeout, so a SIGTERM during a wait is still
handled.

A read-write connection puts the ledger in WAL (ADR-003 §03), so a reader
never blocks serve's commit and a commit never blocks a reader. WAL keeps two
files beside the ledger, ``-wal`` and ``-shm``. The read-only units (bot, tax
pack, archive check, health, daily line) cannot create them: ``/var/lib/hermes``
is read-only to them. SQLite deletes both files when the last connection
closes, so a reader started while serve is stopped could not open the ledger.
A read-write connection therefore sets ``SQLITE_DBCONFIG_NO_CKPT_ON_CLOSE``:
closing leaves both files in place, and SQLite's automatic checkpoint still
moves committed pages into the main file while serve runs.
:data:`JOURNAL_SIZE_LIMIT_BYTES` truncates the ``-wal`` file after each
checkpoint, so the files left behind stay small.

The ``-wal`` file can hold committed rows the main file does not have yet.
Copy the ledger with SQLite's backup API, never with ``cp``; a ledger file put
back by hand needs its ``-wal`` and ``-shm`` files removed (``rollback.sh``
does both).

Reference: issues #855, #879; ADR-003 §03.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Self

from hermes.domain.errors import LedgerError

__all__ = [
    "BUSY_TIMEOUT_SECONDS",
    "JOURNAL_SIZE_LIMIT_BYTES",
    "ReadOnlyLedgerReader",
    "open_ledger",
    "open_read_only",
]

BUSY_TIMEOUT_SECONDS = 10.0
JOURNAL_SIZE_LIMIT_BYTES = 4 * 1024 * 1024


def open_ledger(
    path: Path, *, read_only: bool = False, any_thread: bool = False
) -> sqlite3.Connection:
    """Open the ledger at ``path`` with :data:`BUSY_TIMEOUT_SECONDS`.

    A read-write connection also switches the ledger to WAL and keeps the
    ``-wal`` and ``-shm`` files on close (see the module docstring).

    Args:
        path: The SQLite ledger file.
        read_only: Open with ``mode=ro``: the file must exist, and no write
            goes through.
        any_thread: Let a thread other than the opening one use a read-only
            connection, one thread at a time (the console reads in a worker,
            issue #1001). Python's SQLite is built serialized, so this is safe.

    Returns:
        A connection with the busy timeout set. The caller closes it.

    Raises:
        sqlite3.Error: The file cannot be opened.
        ValueError: ``any_thread`` was asked for a connection that can write.
    """
    if any_thread and not read_only:
        raise ValueError("only a read-only ledger connection may move between threads")
    if read_only:
        return sqlite3.connect(
            f"{path.resolve().as_uri()}?mode=ro",
            uri=True,
            timeout=BUSY_TIMEOUT_SECONDS,
            check_same_thread=not any_thread,
        )
    connection = sqlite3.connect(path, timeout=BUSY_TIMEOUT_SECONDS)
    connection.setconfig(sqlite3.SQLITE_DBCONFIG_NO_CKPT_ON_CLOSE, True)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute(f"PRAGMA journal_size_limit={JOURNAL_SIZE_LIMIT_BYTES}")
    except sqlite3.OperationalError:
        connection.close()
        raise
    except sqlite3.DatabaseError:
        # A file that is not a ledger fails again on the caller's first query,
        # inside the handler that names it (serve's StartupError, tax's exit 1).
        pass
    return connection


def open_read_only(path: Path, *, any_thread: bool = False) -> sqlite3.Connection:
    """Open the existing ledger at ``path`` read-only, as every reader does.

    Args:
        path: The ledger file.
        any_thread: See :func:`open_ledger`.

    Returns:
        A ``mode=ro`` connection with the busy timeout set. The caller closes it.

    Raises:
        LedgerError: SQLite refused to open the file.
    """
    try:
        return open_ledger(path, read_only=True, any_thread=any_thread)
    except sqlite3.Error as exc:
        raise LedgerError(msg=f"cannot open {path} read-only: {exc}") from exc


class ReadOnlyLedgerReader:
    """The open, close and ``with`` lifecycle every read-only ledger reader shares.

    A reader subclasses this and adds only its queries over ``self._conn``.
    One copy keeps every reader on the same rules: a missing file is
    ``FileNotFoundError`` (a ``mode=ro`` open would otherwise create
    nothing and fail later with a less useful message), SQLite's own errors
    become ``LedgerError``, and the connection never writes (issue #1178).
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        """Wrap an already-open connection.

        Args:
            connection: A connection opened by :meth:`open` (or a test's own).
        """
        self._conn = connection

    @classmethod
    def open(cls, path: Path, *, any_thread: bool = False) -> Self:
        """Open ``path`` read-only.

        Args:
            path: The ledger file.
            any_thread: Let a worker thread use the connection, one thread at
                a time; see :func:`open_ledger`.

        Returns:
            A reader over a ``mode=ro`` connection.

        Raises:
            FileNotFoundError: There is no file at ``path``.
            LedgerError: SQLite refused to open the file.
        """
        resolved = path.resolve()
        if not resolved.is_file():
            raise FileNotFoundError(str(path))
        return cls(open_read_only(path, any_thread=any_thread))

    def close(self) -> None:
        """Close the connection."""
        self._conn.close()

    def __enter__(self) -> Self:
        """Return ``self`` for ``with`` blocks."""
        return self

    def __exit__(self, *_exc_info: object) -> None:
        """Close the connection, whatever ended the ``with`` block."""
        self.close()
