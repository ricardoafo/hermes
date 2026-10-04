"""Tests for the read-only SQLite supervisor reader (#470).

Invariants protected here:

- the reader is a structural ``SupervisorReadPort`` and cannot write;
- ``signals_since`` is inclusive of its bound, oldest first, and carries no
  price, stop or size;
- an unreadable payload is a ``LedgerError``, not a crash.
"""

from __future__ import annotations

import dataclasses
import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest

from hermes.adapters.ledger.supervisor_reader import SqliteSupervisorReader
from hermes.domain.errors import LedgerError
from hermes.domain.strategy.signal import SignalKind
from hermes.ports.supervisor import SignalFact, SupervisorReadPort
from tests.support.status_ledger import BTC, ETH, LAST_4H, signal, write_ledger


def test_reader_satisfies_the_port() -> None:
    """The adapter is a structural SupervisorReadPort."""
    assert issubclass(SqliteSupervisorReader, SupervisorReadPort)


def test_a_write_through_the_reader_connection_fails(tmp_path: Path) -> None:
    """mode=ro means the supervisor can never change the ledger it reads."""
    path = write_ledger(tmp_path / "hermes.sqlite3")

    with SqliteSupervisorReader.open(path) as reader, pytest.raises(sqlite3.OperationalError):
        reader._conn.execute("DELETE FROM events")


def test_signals_since_is_inclusive_and_oldest_first(tmp_path: Path) -> None:
    """A signal exactly at ``since`` is included; earlier ones are not."""
    early = LAST_4H - timedelta(hours=8)
    at = LAST_4H - timedelta(hours=4)
    path = write_ledger(
        tmp_path / "hermes.sqlite3",
        signals=(
            signal(ETH, SignalKind.EXIT, LAST_4H),
            signal(BTC, SignalKind.ENTRY, early),
            signal(ETH, SignalKind.ENTRY, at),
        ),
    )

    with SqliteSupervisorReader.open(path) as reader:
        facts = reader.signals_since(at)

    assert facts == (
        SignalFact(pair=ETH, kind="entry", occurred_at=at),
        SignalFact(pair=ETH, kind="exit", occurred_at=LAST_4H),
    )


def test_signal_facts_carry_no_price_fields() -> None:
    """SignalFact has exactly pair, kind and time: no price, stop, ATR or size."""
    assert [f.name for f in dataclasses.fields(SignalFact)] == ["pair", "kind", "occurred_at"]


def test_an_unreadable_signal_payload_is_a_ledger_error(tmp_path: Path) -> None:
    """A malformed row surfaces as LedgerError so the tool can refuse cleanly."""
    path = write_ledger(tmp_path / "hermes.sqlite3")
    connection = sqlite3.connect(path)
    connection.execute(
        "INSERT INTO events (event_type, event_schema_version, correlation_id, occurred_at, "
        "payload_json, recorded_at) VALUES ('SignalEmitted', 1, 'c', ?, '{}', ?)",
        (LAST_4H.isoformat(), LAST_4H.isoformat()),
    )
    connection.commit()
    connection.close()

    with SqliteSupervisorReader.open(path) as reader, pytest.raises(LedgerError):
        reader.signals_since(LAST_4H - timedelta(days=1))
