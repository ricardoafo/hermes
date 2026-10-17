"""A reader holding a read transaction does not make serve's write fail (issue #855).

Real SQLite file, two connections. With the rollback journal a reader's open
read transaction keeps a SHARED lock, and the writer's COMMIT must wait for it.
Python's default busy wait is 5 s; the reader here holds for longer than that
and well under :data:`BUSY_TIMEOUT_SECONDS`, so only the ledger's own timeout
lets the write through.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from contextlib import closing
from pathlib import Path

import pytest

from hermes.adapters.ledger.connection import BUSY_TIMEOUT_SECONDS, open_ledger

pytestmark = pytest.mark.integration

_READER_HOLDS_SECONDS = 6.0
"""Past Python's 5 s default busy wait, inside the ledger's 10 s one."""


def test_a_reader_holding_a_read_transaction_does_not_fail_a_serve_write(tmp_path: Path) -> None:
    """Serve's commit waits for a long reader (tax pack, bot, console) and then lands.

    Before #855 the commit raised "database is locked" after 5 s, serve logged
    a LedgerError and exited 1.
    """
    assert _READER_HOLDS_SECONDS < BUSY_TIMEOUT_SECONDS
    db = tmp_path / "ledger.sqlite3"
    with closing(sqlite3.connect(db)) as setup:
        setup.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, body TEXT)")
        setup.commit()
    holding = threading.Event()

    def long_reader() -> None:
        with closing(open_ledger(db, read_only=True)) as reader:
            reader.isolation_level = None
            reader.execute("BEGIN")
            reader.execute("SELECT COUNT(*) FROM events").fetchone()
            holding.set()
            time.sleep(_READER_HOLDS_SECONDS)
            reader.execute("COMMIT")

    thread = threading.Thread(target=long_reader)
    thread.start()
    assert holding.wait(timeout=10)
    started = time.monotonic()
    with closing(open_ledger(db)) as writer:
        writer.execute("INSERT INTO events (body) VALUES ('bar')")
        writer.commit()
    waited = time.monotonic() - started
    thread.join()
    with closing(open_ledger(db, read_only=True)) as check:
        rows = check.execute("SELECT body FROM events").fetchall()
    assert (waited > 5.0, rows) == (True, [("bar",)])
