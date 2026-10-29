"""A reader holding a read transaction does not hold up serve's write (issues #855, #879).

Real SQLite file, two connections. With the rollback journal a reader's open
read transaction keeps a SHARED lock, so the writer's COMMIT waits for it and
fails once the busy timeout runs out. In WAL the commit lands at once while the
reader keeps reading its own snapshot, however long it holds.
"""

import threading
import time
from contextlib import closing
from pathlib import Path

import pytest

from hermes.adapters.ledger.connection import open_ledger

pytestmark = pytest.mark.integration

_NO_WAIT_SECONDS = 1.0
"""Far under the 10 s busy timeout: the commit did not wait for the reader."""


def test_a_serve_write_lands_at_once_while_a_reader_holds_a_read_transaction(
    tmp_path: Path,
) -> None:
    """Serve commits while a long reader (tax pack, bot, console) is still mid-read.

    The reader keeps its transaction open until after the commit, so under the
    rollback journal this test would wait the full 10 s and fail with
    "database is locked". The reader's snapshot does not see the new row; a
    fresh read does.
    """
    db = tmp_path / "ledger.sqlite3"
    with closing(open_ledger(db)) as setup:
        setup.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, body TEXT)")
        setup.commit()
    holding = threading.Event()
    written = threading.Event()
    seen_by_reader: list[int] = []

    def long_reader() -> None:
        with closing(open_ledger(db, read_only=True)) as reader:
            reader.isolation_level = None
            reader.execute("BEGIN")
            reader.execute("SELECT COUNT(*) FROM events").fetchone()
            holding.set()
            written.wait(timeout=30)
            seen_by_reader.append(reader.execute("SELECT COUNT(*) FROM events").fetchone()[0])
            reader.execute("COMMIT")

    thread = threading.Thread(target=long_reader)
    thread.start()
    assert holding.wait(timeout=10)
    started = time.monotonic()
    try:
        with closing(open_ledger(db)) as writer:
            writer.execute("INSERT INTO events (body) VALUES ('bar')")
            writer.commit()
        waited = time.monotonic() - started
    finally:
        written.set()
        thread.join()
    with closing(open_ledger(db, read_only=True)) as check:
        rows = check.execute("SELECT body FROM events").fetchall()
    assert (waited < _NO_WAIT_SECONDS, seen_by_reader, rows) == (True, [0], [("bar",)])
