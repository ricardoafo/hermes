"""Shared base of the ledger stores: the connection, the clock and the one-transaction write."""

import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime

from hermes.adapters.ledger.codecs import (
    event_to_json,
    iso_utc,
)
from hermes.adapters.ledger.migration_runner import apply_migrations
from hermes.domain.errors import LedgerError
from hermes.domain.events.base import DomainEvent
from hermes.infrastructure.time import now_utc
from hermes.ports.clock import ClockPort


class _LedgerCore:
    """Shared base of the ledger stores: the connection, the clock and the one-transaction write."""

    def __init__(self, connection: sqlite3.Connection, *, clock: ClockPort | None = None) -> None:
        """Bind to a caller-owned SQLite connection and apply pending migrations.

        Args:
            connection: An open :class:`sqlite3.Connection`. The caller
                is responsible for closing it. The adapter runs
                :func:`apply_migrations` immediately so the ledger schema
                is present before the first write.
            clock: The source of every ``recorded_at`` stamp. Defaults to
                the wall clock.
        """
        self._conn = connection
        self._now: Callable[[], datetime] = clock.now if clock is not None else now_utc
        # isolation_level=None would put us in autocommit and break the
        # I-06 grouping. The default (BEGIN implicit on first DML) is what
        # every write method relies on — `_write` commits at the end of the
        # batch, marking the single transaction boundary.
        apply_migrations(self._conn)

    @contextmanager
    def _write(self, op: str) -> Iterator[None]:
        """Run one write as ONE transaction: commit on success, roll back on any error.

        A failed write leaves the implicit transaction open, so any error not
        rolled back here would be committed by the next successful write on
        the connection (issue #853). That is why every exception rolls back,
        not only SQLite ones.

        Args:
            op: The write method's name, used in the error message.

        Yields:
            Nothing; the ``with`` body runs the write's statements.

        Raises:
            LedgerError: On any SQLite failure, the commit included. Any
                other exception is re-raised unchanged after the rollback.
        """
        try:
            yield
            self._conn.commit()
        except sqlite3.Error as exc:
            self._conn.rollback()
            raise LedgerError(msg=f"{op} failed: {exc}") from exc
        except BaseException:
            self._conn.rollback()
            raise

    def _insert_event(self, event: DomainEvent) -> None:
        """Insert the event row alongside a fresh recorded_at timestamp."""
        payload_json = event_to_json(event)
        self._conn.execute(
            "INSERT INTO events (event_type, event_schema_version, correlation_id, "
            "                    occurred_at, payload_json, recorded_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                type(event).__qualname__,
                event.__event_schema_version__,
                event.correlation_id,
                iso_utc(event.occurred_at),
                payload_json,
                iso_utc(self._now()),
            ),
        )
