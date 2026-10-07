"""The one way Hermes opens its SQLite ledger: with a busy timeout.

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

Reference: issue #855; ADR-003 §03.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

__all__ = ["BUSY_TIMEOUT_SECONDS", "open_ledger"]

BUSY_TIMEOUT_SECONDS = 10.0


def open_ledger(path: Path, *, read_only: bool = False) -> sqlite3.Connection:
    """Open the ledger at ``path`` with :data:`BUSY_TIMEOUT_SECONDS`.

    Args:
        path: The SQLite ledger file.
        read_only: Open with ``mode=ro``: the file must exist, and no write
            goes through.

    Returns:
        A connection with the busy timeout set. The caller closes it.

    Raises:
        sqlite3.Error: The file cannot be opened.
    """
    if read_only:
        return sqlite3.connect(
            f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=BUSY_TIMEOUT_SECONDS
        )
    return sqlite3.connect(path, timeout=BUSY_TIMEOUT_SECONDS)
