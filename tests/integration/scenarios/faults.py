"""Storage and clock faults for the chaos suite (issue #846).

Serve opens its ledger with ``open_ledger`` inside
:func:`hermes.entrypoints.serve.build_composition`. :func:`faulty_ledger`
swaps that call for one returning a connection that counts every write
statement and every commit while it is *armed*, and raises a chosen error
just before a chosen one. Raising :class:`ProcessDied` stands in for the
process being killed: nothing in serve catches a ``BaseException`` that is not
an ``Exception``, no rollback runs, and the connection is closed with the
transaction open, so only committed transactions stay on disk, as after a
``kill -9``. A write made through the event bus is the one exception: the
bus wraps whatever a handler raises in ``EventDispatchError``, so serve exits 1
instead; it closes the connection with the transaction still open all the same.

Arming is per task (a context variable), so a write made by another task, such
as the boot EUR pricing, is not counted unless that task is armed too.

:class:`SkewedClock` gives serve a local clock that can jump while the fake
exchange keeps true time, as when NTP steps the host clock.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
from collections.abc import Callable, Iterator, Sequence
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import cast
from unittest import mock

from hermes.adapters.ledger.connection import BUSY_TIMEOUT_SECONDS
from hermes.domain.events.signal_emitted import SignalEmitted
from hermes.domain.market_data.bar import Bar
from hermes.domain.strategy.signal import SignalKind
from hermes.entrypoints import serve as serve_module
from tests.support.fake_market import harness
from tests.support.fake_market.exchange import FakeExchange, SimClock
from tests.support.serve_loop import RecordingChannel

_ARMED: ContextVar[bool] = ContextVar("chaos_armed", default=False)
_WRITES = ("INSERT", "UPDATE", "DELETE", "REPLACE")
COMMIT = "COMMIT"


class ProcessDied(BaseException):
    """The process was killed: no handler, no rollback, no further write."""


@dataclass
class FaultPlan:
    """Which armed write point fails, and how.

    Attributes:
        fail_at: Index of the armed point to fail, or ``None`` to only record.
        fail_on: Label prefix of the first armed point to fail; checked when
            ``fail_at`` is ``None``.
        error: Builds the exception raised just before the failing point runs.
        points: Label of every armed point seen, in order.
        fired: The exception raised, once raised.
        after_commit: Called after every armed commit (to snapshot the ledger).

    """

    fail_at: int | None = None
    fail_on: str | None = None
    error: Callable[[], BaseException] = ProcessDied
    points: list[str] = field(default_factory=list)
    fired: BaseException | None = None
    after_commit: Callable[[], None] | None = None

    def before(self, label: str) -> None:
        """Count ``label`` when armed; raise when it is the point to fail."""
        if not _ARMED.get():
            return
        index = len(self.points)
        self.points.append(label)
        due = (
            index == self.fail_at
            if self.fail_at is not None
            else self.fail_on is not None and label.startswith(self.fail_on)
        )
        if due and self.fired is None:
            self.fired = self.error()
            raise self.fired

    def committed(self) -> None:
        """Run ``after_commit`` for an armed commit."""
        if _ARMED.get() and self.after_commit is not None:
            self.after_commit()


def label(sql: str) -> str:
    """Return a short name for a write statement: verb and table."""
    words = sql.split()
    verb = words[0].upper()
    if verb == "INSERT":
        table = words[words.index("INTO") + 1] if "INTO" in words else words[-1]
        return f"INSERT {table}"
    if verb == "DELETE":
        return f"DELETE {words[2]}"
    return f"{verb} {words[1]}"


class _FaultyConnection(sqlite3.Connection):
    plan: FaultPlan

    def execute(self, sql: str, parameters: object = (), /) -> sqlite3.Cursor:
        if sql.lstrip().upper().startswith(_WRITES):
            self.plan.before(label(sql))
        return super().execute(sql, parameters)  # type: ignore[arg-type]

    def executemany(self, sql: str, parameters: object, /) -> sqlite3.Cursor:
        if sql.lstrip().upper().startswith(_WRITES):
            self.plan.before(label(sql))
        return super().executemany(sql, parameters)  # type: ignore[arg-type]

    def commit(self) -> None:
        self.plan.before(COMMIT)
        super().commit()
        self.plan.committed()


@contextlib.contextmanager
def faulty_ledger(plan: FaultPlan) -> Iterator[None]:
    """Make serve open its ledger through a connection that follows ``plan``."""

    def connect(database: Path, *, read_only: bool = False) -> sqlite3.Connection:
        assert not read_only, "serve opens its ledger for writing"
        connection = sqlite3.connect(
            database, timeout=BUSY_TIMEOUT_SECONDS, factory=_FaultyConnection
        )
        connection.plan = plan
        return connection

    with mock.patch.object(serve_module, "open_ledger", connect):
        yield


@contextlib.contextmanager
def armed() -> Iterator[None]:
    """Arm the plan for the current task and every task or thread it starts."""
    token = _ARMED.set(True)
    try:
        yield
    finally:
        _ARMED.reset(token)


@contextlib.contextmanager
def armed_on(
    chosen: Callable[[Bar], bool], *, on_start: Callable[[], None] | None = None
) -> Iterator[list[datetime]]:
    """Arm the plan while serve's bar loop handles a bar ``chosen`` accepts.

    ``on_start`` runs as such a bar starts, before any of its writes.

    Yields:
        The serve clock's reading each time a chosen bar started, in order.

    """
    original = serve_module._BarLoop.handle
    started: list[datetime] = []

    async def handle(self: serve_module._BarLoop, bar: Bar) -> None:
        if not chosen(bar):
            await original(self, bar)
            return
        started.append(self.c.clock.now())
        if on_start is not None:
            on_start()
        with armed():
            await original(self, bar)

    with mock.patch.object(serve_module._BarLoop, "handle", handle):
        yield started


@contextlib.contextmanager
def armed_in_boot_pricing() -> Iterator[None]:
    """Arm the plan while serve prices pending EUR values after catch-up."""
    original = serve_module.price_pending_eur_once

    def price(*args: object, **kwargs: object) -> None:
        with armed():
            original(*args, **kwargs)  # type: ignore[arg-type]

    with mock.patch.object(serve_module, "price_pending_eur_once", price):
        yield


@contextlib.contextmanager
def recording_channels() -> Iterator[list[RecordingChannel]]:
    """Collect the Telegram stand-in of every run, even one that dies.

    Yields:
        Each run's channel, in boot order.

    """
    channels: list[RecordingChannel] = []

    def channel(raises: Exception | None = None) -> RecordingChannel:
        made = RecordingChannel(raises=raises)
        channels.append(made)
        return made

    with mock.patch.object(harness, "RecordingChannel", channel):
        yield channels


def entries_sent(channels: Sequence[RecordingChannel]) -> int:
    """Return how many entry signals reached Telegram across ``channels``."""
    return sum(
        1
        for channel in channels
        for event in channel.seen
        if isinstance(event, SignalEmitted) and event.kind is SignalKind.ENTRY
    )


class SkewedClock:
    """Serve's local clock: true simulated time plus a skew that jumps.

    The fake exchange answers on the true time, as a real exchange would.
    ``monotonic`` and ``sleep`` follow the true time: only the wall clock
    jumps when NTP steps it.
    """

    def __init__(self, true: SimClock, jumps: Sequence[tuple[datetime, timedelta]]) -> None:
        """Wrap ``true``; at each true instant in ``jumps`` add its step to the skew."""
        self.true = true
        self.jumps = sorted(jumps)
        self.skew = timedelta(0)

    def now(self) -> datetime:
        """Return the local wall clock."""
        while self.jumps and self.true.current >= self.jumps[0][0]:
            self.skew += self.jumps.pop(0)[1]
        return self.true.current + self.skew

    def monotonic(self) -> float:
        """Return true simulated seconds; a monotonic clock never steps."""
        return self.true.monotonic()

    async def sleep(self, seconds: float) -> None:
        """Advance the true clock."""
        await self.true.sleep(seconds)


@dataclass
class _ServeSide:
    clock: SkewedClock
    exchange: FakeExchange

    def transport(self) -> object:
        return self.exchange.transport()


@contextlib.contextmanager
def skewed_clock(jumps: Sequence[tuple[datetime, timedelta]]) -> Iterator[None]:
    """Give serve a :class:`SkewedClock` with ``jumps`` while the exchange keeps true time."""
    original = harness.compose

    def compose(
        config: object, db: Path, exchange: FakeExchange, channel: object
    ) -> serve_module.Composition:
        side = _ServeSide(SkewedClock(exchange.clock, jumps), exchange)
        return original(config, db, cast(FakeExchange, side), channel)  # type: ignore[arg-type]

    with mock.patch.object(harness, "compose", compose):
        yield


_RANDOM_OR_WALL_CLOCK = frozenset(
    {"id", "lot_id", "correlation_id", "recorded_at", "payload_sha256"}
)


def ledger_rows(
    db: Path, *, ignore: frozenset[str] = frozenset()
) -> dict[str, list[tuple[str, ...]]]:
    """Return every table's rows as sorted string tuples, for comparing two ledgers.

    Columns that differ between two runs of the same market are left out: row
    ids, lot ids and correlation ids are random, ``recorded_at`` is wall time.
    Event payloads drop their correlation id. ``ignore`` names more columns to
    leave out.
    """
    dropped = _RANDOM_OR_WALL_CLOCK | ignore
    with contextlib.closing(sqlite3.connect(db)) as conn:
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name NOT IN ('schema_migrations', 'sqlite_sequence') ORDER BY name"
            )
        ]
        return {table: _rows(conn, table, dropped) for table in tables}


def _rows(conn: sqlite3.Connection, table: str, dropped: frozenset[str]) -> list[tuple[str, ...]]:
    columns = [row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')]
    kept = [c for c in columns if c not in dropped]
    select = ", ".join(f'"{c}"' for c in kept)
    rows = conn.execute(f'SELECT {select} FROM "{table}"').fetchall()  # noqa: S608
    payload = kept.index("payload_json") if "payload_json" in kept else None
    return sorted(
        tuple(
            _payload(value, dropped) if i == payload else str(value) for i, value in enumerate(row)
        )
        for row in rows
    )


def _payload(raw: str, dropped: frozenset[str]) -> str:
    data = json.loads(raw)
    return json.dumps({k: v for k, v in data.items() if k not in dropped}, sort_keys=True)
